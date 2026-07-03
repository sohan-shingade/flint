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

    Mirrors the legacy ``BacktestEngine.run`` kwargs: one ascending candle stream
    plus per-market ts-sorted dicts of funding / marks / books / trades / oi.
    ``quotes`` and ``book_deltas`` are the tick-lane slots — their models arrive in
    N8, so they are typed as generic sequences here to keep this seam stable before
    those models exist; the bar lane leaves them empty and no bar-lane code reads
    them.
    """

    candles: list[Candle] = field(default_factory=list)
    funding: dict[str, list[FundingRate]] = field(default_factory=dict)
    marks: dict[str, list[MarkSnapshot]] = field(default_factory=dict)
    books: dict[str, list[OrderbookSnapshot]] = field(default_factory=dict)
    trades: dict[str, list[TradePrint]] = field(default_factory=dict)
    oi: dict[str, list[OpenInterestSnapshot]] = field(default_factory=dict)
    quotes: dict[str, Sequence[object]] = field(default_factory=dict)
    book_deltas: dict[str, Sequence[object]] = field(default_factory=dict)


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
    """

    config: EngineConfig
    venue_spec: VenueSpec
    initial_capital: str = "100000"
    fund_venue: str = ""
    equity_sample_interval_s: int | None = None
    mark_policy: str = "recorded"


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
