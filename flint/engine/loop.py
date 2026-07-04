"""Engine configuration and the strategy-visible context (§6.1, §8.2).

This module holds the two pure, substrate-independent pieces the simulation core
exposes to strategy code:

* :class:`EngineConfig` — the locked-but-calibratable defaults for a run
  (deterministic ``rng_seed``; fees / latency / liquidation tiers are venue law on
  the :class:`~flint.venues.VenueSpec`, not here).
* :class:`EngineContext` — the read-only window a strategy sees under the §8.2
  visibility contract: **strictly less than bar start, closed data only, ``None``
  over stale, never synthetic** (D26). The Nautilus bar lane builds this ctx per
  bar and hands it to the strategy.

Also here: the :class:`Strategy` call seam and :class:`NoopStrategy`. The per-bar
backtest *walk* that once lived in this module (``BacktestEngine``) was the legacy
simulation substrate; it was deleted in N10 (2026-07-04) when every backtest moved
to the Nautilus core (§6.0). ``SignalValidationError`` is re-exported here for
back-compat with ``from flint.engine.loop import SignalValidationError``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from random import Random
from typing import Callable, Protocol

from flint.core.models import (
    Candle,
    FundingRate,
    MarkSnapshot,
    Order,
    OrderbookSnapshot,
    OrderType,
    Position,
    Side,
    Signal,
    TimeInForce,
)
from flint.core.time import bar_end, last_before
from flint.venues import VenueSpec

from .context import AccountView, OpenInterestSnapshot
from .signals import SignalValidationError
from .state import PortfolioState

# ``SignalValidationError`` moved to ``signals.py`` (§6.0, D29); it is re-exported
# here (and via ``flint.engine``) so existing imports keep working.
__all__ = [
    "EngineConfig",
    "EngineContext",
    "NoopStrategy",
    "Strategy",
    "SignalValidationError",
]


@dataclass(frozen=True)
class EngineConfig:
    """The engine's locked-but-calibratable defaults for a run.

    ``maint_frac`` is retained only for back-compat: since slice 3.4 the
    liquidation check reads the venue's size-tiered maintenance off
    ``VenueSpec.liquidation`` (§6.5, D14), so this flat fraction is no longer
    consulted. Fees, latency, tick, and the oracle band are likewise venue law on
    the ``VenueSpec``, not here. ``rng_seed`` seeds ``ctx.rng`` — and the
    fill-model latency draws — so a run is deterministic.
    """

    maint_frac: float = 0.025  # superseded by VenueSpec.liquidation tiers (§6.5)
    rng_seed: int = 0


class Strategy(Protocol):
    """The minimal strategy seam the loop calls (slice 3.6 formalises it).

    ``on_candle`` receives the just-closed candle and a read-only ``ctx`` and
    returns a list of ``Signal``s (empty = do nothing). Slice 3.6 builds the full
    ``ctx`` visibility contract (§8.2) and the Signal→Order conversion rules
    (§8.1); 3.1 needs only the call seam so the loop is end-to-end.
    """

    def on_candle(self, candle: Candle, ctx: "EngineContext") -> list[Signal]:
        ...


class NoopStrategy:
    """A strategy that never trades — the default for engine-mechanics tests."""

    def on_candle(self, candle: Candle, ctx: "EngineContext") -> list[Signal]:
        return []


@dataclass
class EngineContext:
    """The read-only window a strategy sees, under the §8.2 visibility contract.

    Every accessor obeys one rule: **strictly less than bar start, closed data
    only, ``None`` over stale, never synthetic** (D26). ``now`` is the bar start,
    and each accessor returns the most recent datum with ``ts < now`` — what a
    trader actually knew when deciding this bar. A missing/stale datum is ``None``
    (a fidelity gap), never a forward-filled or invented value. The ctx is a value
    object: it holds no path to config, secrets, or the store (§8.3); ``submit_order``
    reaches the engine only through a narrow order-submitting callable.
    """

    state: PortfolioState
    default_venue: str
    now: int
    rng: Random
    venue_spec: VenueSpec
    submit_order_fn: Callable[[Order], object]
    funding: dict[str, list[FundingRate]] = field(default_factory=dict)
    marks: dict[str, list[MarkSnapshot]] = field(default_factory=dict)
    books: dict[str, list[OrderbookSnapshot]] = field(default_factory=dict)
    oi: dict[str, list[OpenInterestSnapshot]] = field(default_factory=dict)
    candle_history: dict[str, list[Candle]] = field(default_factory=dict)

    def position(self, market: str, venue: str | None = None) -> Position | None:
        """The open position on ``(venue or default, market)``, or ``None``."""
        return self.state.position(venue or self.default_venue, market)

    def account(self, venue: str | None = None) -> AccountView:
        """A per-venue equity snapshot + the §8.1 sizing helpers (§8.2, §6.5/§6.6).

        Equity and the cross-margin pool are valued on marks known at bar start
        (the visibility contract); a position whose market has no as-of mark simply
        contributes no unrealized (it cannot be valued this bar).
        """
        v = venue or self.default_venue
        as_of = self._marks_as_of(v)
        return AccountView(
            venue=v,
            equity=self.state.equity(v, as_of),
            cross_margin_available=self.state.cross_margin_available(v, as_of),
        )

    def funding_rate(
        self, market: str, venue: str | None = None
    ) -> FundingRate | None:
        """Last **published predicted** funding rate knowable at bar start (§6.4).

        Returns the most recent ``predicted`` row with ``ts < now`` — what a trader
        knew when deciding. It **never** returns the ``final`` rate that settles
        later in the bar: leaking the settled rate inflates funding-arb backtests,
        and blocking that leak here (where a look-ahead linter can't see it) is the
        whole point of the predicted/final split. ``None`` if none published yet.
        """
        predicted = [
            r for r in self.funding.get(market, []) if r.rate_type == "predicted"
        ]
        return last_before(predicted, self.now, key=lambda r: r.ts)

    def basis_bps(self, market: str, venue: str | None = None) -> float | None:
        """Perp premium vs index from the last MarkSnapshot before bar start (§8.2).

        ``None`` when no mark is knowable yet (Tier-C / gap) — never a stale or
        synthetic premium.
        """
        snap = last_before(self.marks.get(market, []), self.now, key=lambda m: m.ts)
        return snap.basis_bps if snap is not None else None

    def open_interest(self, market: str, venue: str | None = None) -> float | None:
        """Last open-interest reading before bar start, or ``None`` (§8.2)."""
        snap = last_before(self.oi.get(market, []), self.now, key=lambda o: o.ts)
        return snap.open_interest if snap is not None else None

    def orderbook(
        self, market: str, venue: str | None = None
    ) -> OrderbookSnapshot | None:
        """Last L2 snapshot before bar start, **within** the staleness threshold (§8.2/§6.3).

        A snapshot older than the venue's ``book_staleness_s`` at bar start is
        treated as absent → ``None`` (a fidelity gap), never a stale book handed
        out as if fresh.
        """
        snap = last_before(self.books.get(market, []), self.now, key=lambda b: b.ts)
        if snap is None:
            return None
        if (self.now - snap.ts) > self.venue_spec.book_staleness_s * 1000:
            return None  # stale beyond threshold → absent, not silently fresh
        return snap

    def candles(
        self, market: str, lookback: int, venue: str | None = None
    ) -> list[Candle]:
        """The last ``lookback`` **closed** bars for ``market`` (bar_end ≤ now, §8.2).

        Closed bars only — the current, not-yet-closed bar is excluded, and gaps
        show up as missing bars, never forward-filled.
        """
        closed = [
            c
            for c in self.candle_history.get(market, [])
            if bar_end(c.ts, c.resolution_s) <= self.now
        ]
        return closed[-lookback:] if lookback > 0 else []

    def submit_order(
        self,
        market: str,
        venue: str,
        side: Side,
        size: float,
        *,
        type: OrderType = OrderType.MARKET,
        price: float = 0.0,
        tif: TimeInForce | None = None,
        margin_mode: str = "cross",
        reduce_only: bool = False,
        client_order_id: str = "",
    ) -> object:
        """The imperative escape hatch (§8.1, D21): submit an order directly.

        Same Order model, same engine fill path, same caps as a Signal — but the
        look-ahead linter reasons about Signals, not this, so imperative strategies
        earn a tearsheet note. Routes through the engine's idempotent submit seam.
        """
        order = Order(
            market=market,
            venue=venue,
            side=side,
            type=type,
            size=size,
            price=price,
            client_order_id=client_order_id,
            tif=tif or (TimeInForce.IOC if type is OrderType.MARKET else TimeInForce.GTC),
            margin_mode=margin_mode,
            reduce_only=reduce_only,
        )
        return self.submit_order_fn(order)

    def _marks_as_of(self, venue: str) -> dict[str, float]:
        """As-of mark price per market this venue holds a position in (bar start).

        Prefers the last MarkSnapshot before ``now``; falls back to the last closed
        candle's close. Markets with neither are omitted (unvaluable this bar).
        """
        out: dict[str, float] = {}
        for (pos_venue, market) in self.state.positions:
            if pos_venue != venue or market in out:
                continue
            snap = last_before(self.marks.get(market, []), self.now, key=lambda m: m.ts)
            if snap is not None:
                out[market] = snap.mark_price
                continue
            candle = last_before(
                self.candle_history.get(market, []), self.now, key=lambda c: c.ts
            )
            if candle is not None:
                out[market] = candle.close
        return out
