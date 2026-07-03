"""The tick-native ``TickStrategy`` surface — event-driven strategies (§8.6, plan §A7b).

Where a bar :class:`~flint.strategy.base.Strategy` reacts to a *closed candle*, a
:class:`TickStrategy` reacts to the raw event tape: individual trades, top-of-book
quotes, L2 book deltas, predicted funding, and (optionally) aggregated candles. The
user overrides **only** the handlers they need; the engine subscribes to only those
event streams, so an unrequested stream never crosses the GIL into Python (the
2–4k ev/s ceiling applies only to what a strategy actually asks for, §8.6).

Every handler takes the event datum plus a read-only ``ctx`` — the **same** §8.2
visibility window a bar strategy sees, evaluated **as of the event's timestamp**
(``ctx.now`` = event ts). It exposes the identical accessors (``position``,
``account``, ``funding_rate``, ``orderbook``, the sizing helpers, and the
``submit_order`` escape hatch); the only difference from the bar ctx is that fills
are immediate (no T+1) and there is no separate ``history`` argument — closed bars
come from ``ctx.candles`` exactly as in the bar lane.

Handlers return a ``list[Signal]`` (empty / ``None`` = do nothing; a bare ``Signal``
is wrapped) validated by the identical §8.1 rules. :class:`TickEngineStrategy` is the
internal adapter the runner wraps a :class:`TickStrategy` in — the tick-lane analogue
of :class:`~flint.strategy.base.EngineStrategy`: it normalizes each handler's return,
enforces the D28 executable-venue gate (recording every non-executable leg as a
structured rejection), and flags imperative ``ctx.submit_order`` use for the
tearsheet. A tick-lane run is Nautilus-only (native L2 matching), which
``flint/services`` enforces before the engine is ever reached (§19.1).
"""

from __future__ import annotations

from typing import Any

from flint.core.models import Signal

from .base import (
    IMPERATIVE_ORDERS_NOTE,
    Strategy,
    StrategyRejection,
    _CtxProxy,
    _gate_executable,
    normalize_signals,
)

# The optional event handlers, in the fixed order the host tests for overrides. A
# subclass that leaves one untouched inherits the no-op below, and the host does not
# subscribe to that stream — the whole point of the per-handler subscription (§8.6).
TICK_HANDLERS: tuple[str, ...] = (
    "on_trade",
    "on_quote",
    "on_book",
    "on_funding",
    "on_candle",
)


class TickStrategy(Strategy):
    """Base class for an event-driven (tick-native) user strategy (§8.6, plan §A7b).

    Subclass it, declare tunable ``params`` (auto-exposed to the optimizer/UI exactly
    as a bar :class:`Strategy`, via :meth:`param_spec`), and override any subset of the
    five event handlers below. Every handler is an optional no-op by default, so a
    trades-only strategy overrides just :meth:`on_trade` and the engine subscribes to
    nothing else. Handlers return ``list[Signal] | Signal | None`` under the §8.1
    rules; funding, fees, native fills, and liquidation are the engine's job.
    """

    #: Lane marker read by the runner (§19.1) and the engine's assembly branch. A tick
    #: strategy runs **only** on the Nautilus core (native L2 matching), never the
    #: legacy bar loop — services validate this before the engine is selected.
    lane: str = "tick"

    def on_trade(self, trade: Any, ctx: Any) -> list[Signal] | Signal | None:
        """React to one executed trade print (price/size/ts). Optional (§8.6)."""

    def on_quote(self, quote: Any, ctx: Any) -> list[Signal] | Signal | None:
        """React to one top-of-book quote update. Optional (§8.6)."""

    def on_book(self, delta: Any, ctx: Any) -> list[Signal] | Signal | None:
        """React to one L2 incremental book delta. Optional (§8.6)."""

    def on_funding(self, rate: Any, ctx: Any) -> list[Signal] | Signal | None:
        """React to one **predicted** funding observation (§6.4). Optional.

        Only predicted rows reach here — final rows settle through the engine's
        funding module, never the strategy (the predicted/final split that blocks
        funding-arb look-ahead, §6.4), exactly as ``ctx.funding_rate`` returns.
        """

    def on_candle(self, candle: Any, ctx: Any) -> list[Signal] | Signal | None:
        """React to one aggregated closed candle over the tick stream. Optional.

        The candle is Nautilus's own time-bar aggregation of the trade tape, proven
        equal to Flint's :class:`~flint.data.livefeed.aggregator.CandleAggregator`
        over the same fragment — so a strategy can mix candle logic with tick logic
        without a second, divergent notion of what a bar is (§8.6).
        """

    @property
    def handlers(self) -> frozenset[str]:
        """The event streams this strategy wants — the handlers it actually overrides.

        Exposed on the base class so a raw :class:`TickStrategy` and the
        :class:`TickEngineStrategy` adapter present the **same** ``handlers`` surface to
        the tick-lane host, which subscribes to only these streams (§8.6). Delegates to
        :func:`overridden_handlers`, so the identity rule (an un-overridden handler is
        still :class:`TickStrategy`'s no-op) is defined in exactly one place.
        """
        return overridden_handlers(self)


def overridden_handlers(strategy: TickStrategy) -> frozenset[str]:
    """The event handlers ``strategy`` actually overrides (§8.6 per-handler subscribe).

    A handler the subclass left untouched is the same function object as
    :class:`TickStrategy`'s no-op, so an identity test separates "wants this stream"
    from "inherited the default" without calling anything. The host subscribes to
    only the returned streams.
    """
    cls = type(strategy)
    return frozenset(
        name
        for name in TICK_HANDLERS
        if getattr(cls, name) is not getattr(TickStrategy, name)
    )


class TickEngineStrategy:
    """Adapts a :class:`TickStrategy` to the tick-lane host (§8.6, D28).

    The tick-lane analogue of :class:`~flint.strategy.base.EngineStrategy`: the host
    calls one gated handler per event, and this normalizes the return, enforces the
    D28 executable-venue gate (collecting every non-executable leg as a structured
    ``venue_not_executable`` rejection the runner drains), and flags imperative
    ``ctx.submit_order`` use for the tearsheet. It carries no per-event state beyond
    those rejections and the imperative-use flag, so it is safe across the whole run.
    """

    lane = "tick"

    def __init__(self, strategy: TickStrategy) -> None:
        self.strategy = strategy
        self.rejections: list[StrategyRejection] = []
        self.used_imperative_orders = False
        # The streams the wrapped strategy asked for — the host reads this to subscribe
        # to exactly those Nautilus data types and no others (the GIL-cost contract).
        self.handlers = overridden_handlers(strategy)

    @property
    def name(self) -> str:
        return self.strategy.name

    def dispatch(self, handler: str, datum: Any, ctx: Any) -> list[Signal]:
        """Run one overridden handler and return only its executable signals (§8.1/D28).

        ``handler`` is one of :data:`TICK_HANDLERS`. The signal's venue defaults to
        ``ctx.default_venue`` (a trade print carries no venue of its own), and the
        rejection ts is the event ts (``ctx.now``) — so a refused leg is timestamped
        at the event that produced it.
        """
        method = getattr(self.strategy, handler)
        proxy = _CtxProxy(ctx, self)
        signals = normalize_signals(method(datum, proxy))
        return _gate_executable(signals, ctx.default_venue, ctx.now, self.rejections)

    def drain_rejections(self) -> list[StrategyRejection]:
        """Return and clear the structured rejections recorded so far (§19.1)."""
        out = self.rejections
        self.rejections = []
        return out

    def tearsheet_notes(self) -> list[str]:
        """Notes the run should surface on its tearsheet (§8.1)."""
        return [IMPERATIVE_ORDERS_NOTE] if self.used_imperative_orders else []


__all__ = [
    "TickStrategy",
    "TickEngineStrategy",
    "TICK_HANDLERS",
    "overridden_handlers",
]
