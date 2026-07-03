"""The public ``Strategy`` base — what a user actually writes (§8.1).

A user subclasses :class:`Strategy`, declares tunable ``params`` (the knobs the
optimizer and UI expose, §3.3), and writes ``on_candle(self, candle, history,
ctx)`` returning a ``list[Signal]`` — empty list (or ``None``) means *do nothing*,
a bare ``Signal`` is accepted and wrapped. The user writes only trading logic;
funding, fees, fills, and liquidation are the engine's job (§8.1).

The engine's per-bar loop, however, calls a narrower internal seam —
``on_candle(candle, ctx)`` (two args, §6.1/§8.2). :class:`EngineStrategy` is the
adapter that bridges the two: it derives ``history`` from the read-only ctx
(all closed bars for the bar's market, current bar last — the §8.2/Phase-3
convention), normalizes the return, and — the D28 gate — routes only signals
whose venue is executable, recording every other one as a structured
``venue_not_executable`` rejection instead of letting a non-Hyperliquid order
reach the fill path. It also flags imperative ``ctx.submit_order`` use so the
tearsheet can carry the "linter coverage partial" note (§8.1, D21).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from flint.core.models import Signal
from flint.venues import HYPERLIQUID

if TYPE_CHECKING:  # engine types are duck-typed at runtime — no hard dep here.
    from flint.core.models import Candle

# The only executable venue in v1 (D28). Sourced from the venue spec so a second
# executable venue is one spec away, never a hardcoded string here. Everything
# else stays venue-parameterized — a Signal may *target* any venue; only these
# actually route.
EXECUTABLE_VENUES: frozenset[str] = frozenset({HYPERLIQUID.name})

# The tearsheet note earned by any run that used the imperative escape hatch: the
# look-ahead linter reasons about Signals, not ``ctx.submit_order`` calls (§8.1).
IMPERATIVE_ORDERS_NOTE = "imperative orders — linter coverage partial"


class UnknownParamError(ValueError):
    """A param override named a knob the strategy never declared (§8.1)."""


@dataclass(frozen=True)
class StrategyRejection:
    """A signal the strategy surface refused *before* it reached the engine.

    The D28 case is ``venue_not_executable``: a ``Signal(venue="binance")`` in a
    v1 (Hyperliquid-only) run. It is not an error and not a silent drop — it is a
    structured record (§19.1) the runner surfaces, so a two-leg strategy shows
    exactly which leg was inert while the executable leg still ran.
    """

    reason: str
    signal: Signal
    detail: str
    ts: int

    @classmethod
    def venue_not_executable(cls, signal: Signal, ts: int) -> "StrategyRejection":
        executable = ", ".join(sorted(EXECUTABLE_VENUES))
        return cls(
            reason="venue_not_executable",
            signal=signal,
            detail=(
                f"venue {signal.venue!r} is not executable in v1 (executable: "
                f"{executable}) — the leg is inert until its adapter lands (D28)"
            ),
            ts=ts,
        )


class Strategy:
    """Base class for a user strategy (§8.1).

    Subclass it, set ``params`` (a dict of the tunable knobs — auto-exposed to the
    optimizer and UI via :meth:`param_spec`), and override
    :meth:`on_candle`. Instances get their *own* ``params`` dict copied from the
    class declaration, so the optimizer can vary a trial's params without mutating
    the class or leaking across trials.
    """

    #: The tunable knobs, declared on the subclass (``params = dict(...)``). The
    #: class-level dict is the *declaration*; each instance gets a copy.
    params: dict[str, Any] = {}

    def __init__(self, **overrides: Any) -> None:
        declared = dict(type(self).params)
        unknown = set(overrides) - set(declared)
        if unknown:
            known = ", ".join(sorted(declared)) or "(none)"
            raise UnknownParamError(
                f"{type(self).__name__} has no param(s) {sorted(unknown)}; "
                f"declared params: {known}"
            )
        declared.update(overrides)
        self.params = declared

    @property
    def name(self) -> str:
        """The strategy's display name (its class name by default)."""
        return type(self).__name__

    @classmethod
    def param_spec(cls) -> dict[str, Any]:
        """The declared knobs (name → default) for the optimizer/UI (§3.3)."""
        return dict(cls.params)

    def on_candle(
        self, candle: "Candle", history: list["Candle"], ctx: Any
    ) -> list[Signal] | Signal | None:
        """React to the just-closed ``candle`` and return signals (§8.1).

        ``history`` is the market's closed bars oldest→newest with ``candle`` last;
        ``ctx`` is the read-only §8.2 window (positions, funding, book, sizing
        helpers, and the ``submit_order`` escape hatch). Return a ``list[Signal]``
        (empty or ``None`` = do nothing; a bare ``Signal`` is wrapped).
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement on_candle(self, candle, history, ctx)"
        )


def normalize_signals(result: list[Signal] | Signal | None) -> list[Signal]:
    """Coerce an ``on_candle`` return into a ``list[Signal]`` (§8.1).

    ``None`` and ``[]`` mean *do nothing*; a bare ``Signal`` is wrapped in a list.
    Anything else (a non-Signal element, a non-iterable) is a strategy authoring
    error and raised loudly rather than silently dropped.
    """
    if result is None:
        return []
    if isinstance(result, Signal):
        return [result]
    if isinstance(result, (str, bytes)) or not isinstance(result, (list, tuple)):
        raise TypeError(
            "on_candle must return a list[Signal], a bare Signal, or None; got "
            f"{type(result).__name__}"
        )
    signals = list(result)
    for i, sig in enumerate(signals):
        if not isinstance(sig, Signal):
            raise TypeError(
                f"on_candle return element {i} is {type(sig).__name__}, not a Signal"
            )
    return signals


class _CtxProxy:
    """A thin pass-through over the engine ctx that flags imperative order use.

    Every §8.2 accessor forwards unchanged to the real ctx (so the visibility and
    value-object contracts are exactly those of the engine ctx); only
    ``submit_order`` is intercepted, to set the flag that earns the tearsheet's
    "imperative orders" note (§8.1, D21). It adds no attribute the real ctx lacks,
    so it reaches no credential/store/config path the engine ctx didn't already.
    """

    __slots__ = ("_ctx", "_adapter")

    def __init__(self, ctx: Any, adapter: "EngineStrategy") -> None:
        object.__setattr__(self, "_ctx", ctx)
        object.__setattr__(self, "_adapter", adapter)

    def submit_order(self, *args: Any, **kwargs: Any) -> object:
        self._adapter.used_imperative_orders = True
        return self._ctx.submit_order(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ctx, name)


# A lookback large enough to mean "every closed bar" — ``ctx.candles`` slices
# ``closed[-lookback:]``, so any value past the history length returns all of it.
_ALL_CLOSED = 1 << 62


def _gate_executable(
    signals: list[Signal],
    default_venue: str,
    now: int,
    rejections: list[StrategyRejection],
) -> list[Signal]:
    """Return only signals whose venue is executable; append the rest to ``rejections``.

    The single D28 executable-venue gate shared by the bar adapter
    (:class:`EngineStrategy`), the ML adapter, and the tick adapter
    (:class:`~flint.strategy.tick.TickEngineStrategy`). A signal with no explicit
    venue defaults to ``default_venue`` (the bar's venue, or the tick event's venue);
    a non-executable venue becomes a structured ``venue_not_executable`` rejection at
    ``now`` rather than an order on the fill path (§19.1).
    """
    executable: list[Signal] = []
    for sig in signals:
        venue = sig.venue or default_venue
        if venue not in EXECUTABLE_VENUES:
            rejections.append(StrategyRejection.venue_not_executable(sig, now))
            continue
        executable.append(sig)
    return executable


class EngineStrategy:
    """Adapts a public :class:`Strategy` to the engine's ``on_candle(candle, ctx)`` seam.

    The engine (§6.1) hands each bar a two-arg ``on_candle``; the user writes the
    three-arg ``on_candle(candle, history, ctx)``. This adapter bridges them and
    enforces the D28 executable-venue gate: it returns to the engine only the
    signals whose venue actually routes, collecting the rest as structured
    ``venue_not_executable`` rejections (drained by the runner). It carries no
    per-bar state beyond those rejections and the imperative-use flag.
    """

    def __init__(self, strategy: Strategy) -> None:
        self.strategy = strategy
        self.rejections: list[StrategyRejection] = []
        self.used_imperative_orders = False

    @property
    def name(self) -> str:
        return self.strategy.name

    def on_candle(self, candle: "Candle", ctx: Any) -> list[Signal]:
        """The engine-facing seam: derive history, delegate, gate venues (§8.1, D28)."""
        history = list(ctx.candles(candle.market, _ALL_CLOSED))
        history.append(candle)
        proxy = _CtxProxy(ctx, self)
        signals = normalize_signals(self.strategy.on_candle(candle, history, proxy))
        return self._gate(signals, candle, ctx.now)

    def _gate(
        self, signals: list[Signal], candle: "Candle", now: int
    ) -> list[Signal]:
        """Return only signals whose venue routes; record the rest (D28, §19.1).

        Shared by the plain adapter and :class:`~flint.strategy.ml.MLEngineStrategy`
        so the one-venue-away executable gate has a single implementation. Delegates
        to :func:`_gate_executable` (also the tick lane's gate) with the bar's venue
        as the per-signal default.
        """
        return _gate_executable(signals, candle.venue, now, self.rejections)

    def drain_rejections(self) -> list[StrategyRejection]:
        """Return and clear the structured rejections recorded so far (§19.1)."""
        out = self.rejections
        self.rejections = []
        return out

    def tearsheet_notes(self) -> list[str]:
        """Notes the run should surface on its tearsheet (§8.1)."""
        return [IMPERATIVE_ORDERS_NOTE] if self.used_imperative_orders else []
