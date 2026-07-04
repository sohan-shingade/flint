"""The engine seam — the substrate-agnostic run contract (§6.0, D29, plan §A1).

:class:`SimulationEngine` is the Protocol both the legacy bar loop and the future
Nautilus engine satisfy: an :class:`EngineFeed` (all recorded market data) and an
:class:`EngineRunSpec` (config + venue + policy knobs) in, a ``PortfolioState`` out,
with every event written to the injected ``EventLog``. This module names no engine
implementation and imports nothing venue-locked or Nautilus-specific — it is the
pure interface :mod:`flint.engine.select` dispatches over, so ``flint/engine`` can
expose the seam without ever pulling Nautilus into a candle-only process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

from flint.core.models import Candle, FundingRate, MarkSnapshot, OrderbookSnapshot
from flint.venues import VenueSpec

from .context import OpenInterestSnapshot
from .fills import TradePrint
from .loop import EngineConfig
from .portfolio import EventLog
from .state import PortfolioState


@dataclass(frozen=True)
class EngineFeed:
    """All recorded market data an engine run consumes (§6.0, plan §A1).

    Carries the per-market feeds a run consumes: one ascending candle stream plus
    per-market ts-sorted dicts of funding / marks / books / trades / oi. ``quotes``
    and ``book_deltas`` are the tick-lane slots; the bar lane leaves them empty and
    no bar-lane code reads them.

    **Canonical event ordering (§19.4).** ``candles`` are normalized to ``(ts,
    market)`` order at construction — so a run's multi-market interleaving is fixed
    at the seam, independent of the caller's feed order. The Nautilus lane delivers
    same-ts bars market-name-sorted, so a shared timestamp settles the same way
    byte-for-byte regardless of how the feed was built (and reproduces the frozen
    parity goldens the legacy engine recorded before its N10 deletion). The sort is
    stable, so it never reorders candles that already share a ``(ts, market)`` key.
    """

    candles: list[Candle] = field(default_factory=list)
    funding: dict[str, list[FundingRate]] = field(default_factory=dict)
    marks: dict[str, list[MarkSnapshot]] = field(default_factory=dict)
    books: dict[str, list[OrderbookSnapshot]] = field(default_factory=dict)
    trades: dict[str, list[TradePrint]] = field(default_factory=dict)
    oi: dict[str, list[OpenInterestSnapshot]] = field(default_factory=dict)
    quotes: dict[str, Sequence[object]] = field(default_factory=dict)
    book_deltas: dict[str, Sequence[object]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Canonicalize the multi-market interleaving at the seam so both engines
        # emit shared-ts events in the same (ts, market) order (§19.4). Stable, so
        # candles already sharing a (ts, market) key keep their relative order.
        object.__setattr__(
            self, "candles", sorted(self.candles, key=lambda c: (c.ts, c.market))
        )


@dataclass(frozen=True)
class EngineRunSpec:
    """The run's config + funding + policy knobs, substrate-agnostic (§6.0).

    ``fund_venue``/``initial_capital`` seed the account the engine starts from.
    ``equity_sample_interval_s`` is the tick-lane EQUITY cadence (``None`` = per bar,
    the bar-lane default). ``mark_policy`` gates close-derived mark synthesis:
    ``"close_derived"`` is the bar lane's OHLCV-only path; ``"recorded"`` forbids
    synthesizing a mark from a close (the tick lane requires real marks, §6.0). Both
    knobs are stamped into the run manifest (§19.6) so a result never hides how it
    was produced.

    ``initial_state`` is the paper-resume warm start (§6.7). ``None`` (the default,
    every backtest) → the engine funds a fresh ``PortfolioState`` with
    ``initial_capital`` at ``fund_venue``. Provided → the engine *adopts* that book
    as its starting point (it mutates it and returns it as the final state) and does
    **not** fund it again, so a ``PaperSession`` can fold its persisted event log
    into a ``PortfolioState`` and resume from the exact carried book (cash, open
    positions, accumulators). Only the bar lane supports it; the tick lane rejects it.
    """

    config: EngineConfig
    venue_spec: VenueSpec
    initial_capital: str = "100000"
    fund_venue: str = ""
    equity_sample_interval_s: int | None = None
    mark_policy: str = "recorded"
    initial_state: PortfolioState | None = None


class SimulationEngine(Protocol):
    """The one contract every simulation substrate satisfies (§6.0, D29).

    An implementation is a lightweight factory-constructed object with a ``name``
    (recorded in the run manifest) and a ``run`` that drives one backtest: it reads
    ``feed``, calls ``strategy``, writes every event to ``event_log``, and returns
    the final ``PortfolioState``. ``strategy`` is deliberately untyped here — the bar
    lane hosts a Flint ``Strategy`` while the tick lane hosts a Nautilus strategy
    object, and the seam must not assume either.
    """

    name: str

    def run(
        self,
        feed: EngineFeed,
        strategy: object,
        *,
        event_log: EventLog,
        spec: EngineRunSpec,
    ) -> PortfolioState:
        ...
