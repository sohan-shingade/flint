"""The engine seam — SimulationEngine conformance + the mark-policy fence (§6.0, N1).

Two proofs the N1 extraction is behavior-neutral:

* **Byte-compare conformance.** The legacy engine reached through the
  :class:`SimulationEngine` seam (``engine_for("legacy-bar")``) writes an event log
  byte-identical to a direct :meth:`BacktestEngine.run` on the same hand-authored
  fixture — a run exercising order routing, T+1 fills, funding settlement, and the
  liquidation cascade, so every moved code path is covered.
* **Mark-policy fence.** ``mark_policy="recorded"`` on a candle-only feed lets **no**
  synthesized mark reach the engine; ``mark_policy="close_derived"`` reproduces
  today's per-market close-mark synthesis exactly.

Hand-authored inputs only (D26).
"""

from __future__ import annotations

import pytest

from flint.adapters import InMemoryUserData
from flint.core.models import Candle, FundingRate, MarkSnapshot, Signal
from flint.data.ranges import Kind
from flint.engine import BacktestEngine, EngineConfig, PortfolioState, money
from flint.engine.api import EngineFeed, EngineRunSpec
from flint.engine.portfolio import LIQUIDATION, EventLog
from flint.engine.select import LegacyBarEngine, UnknownEngineError, engine_for
from flint.ports import TenantContext
from flint.services._engine_inputs import build_engine_feed, build_engine_inputs
from flint.venues import HYPERLIQUID

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
HOUR_S = 3600
HOUR_MS = 3600 * 1000
T0 = 1_700_000_000_000


def _bar(ts, open_, high, low, close) -> Candle:
    return Candle(
        ts=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
        market=MARKET,
        resolution_s=HOUR_S,
        venue=VENUE,
    )


def _scenario_feed() -> EngineFeed:
    """A run touching routing, T+1 fills, funding, and the liquidation cascade.

    bar0: strategy opens a leveraged long (fills at bar1 open). bar1: a final funding
    settlement is charged. bar2: the mark collapses and the position is liquidated.
    """
    candles = [
        _bar(T0, 100.0, 101.0, 99.0, 100.0),
        _bar(T0 + HOUR_MS, 100.0, 101.0, 99.0, 100.0),
        _bar(T0 + 2 * HOUR_MS, 100.0, 101.0, 60.0, 65.0),
    ]
    funding = {
        MARKET: [
            FundingRate(
                market=MARKET,
                ts=T0 + HOUR_MS + 12 * 60 * 1000,  # minute 12 of bar1
                rate_hourly=0.001,
                interval_s=HOUR_S,
                price_basis="oracle",
                rate_type="final",
                venue=VENUE,
            )
        ]
    }
    marks = {
        MARKET: [
            MarkSnapshot(market=MARKET, ts=T0 + HOUR_MS, mark_price=100.0, index_price=100.0, venue=VENUE),
            MarkSnapshot(market=MARKET, ts=T0 + HOUR_MS + 12 * 60 * 1000, mark_price=100.0, index_price=100.0, venue=VENUE),
            MarkSnapshot(market=MARKET, ts=T0 + 2 * HOUR_MS, mark_price=62.0, index_price=62.0, venue=VENUE),
        ]
    }
    return EngineFeed(candles=candles, funding=funding, marks=marks)


def _strategy():
    # Fresh instance per run — the strategy is stateful (fires once).
    class _OpenOnce:
        def __init__(self) -> None:
            self._fired = False

        def on_candle(self, candle, ctx):
            if not self._fired:
                self._fired = True
                return [Signal.long(MARKET, VENUE, size=50.0)]
            return []

    return _OpenOnce()


def _log(run_id: str) -> EventLog:
    return EventLog(InMemoryUserData(), TenantContext.local(), run_id=run_id)


def test_legacy_engine_via_seam_is_byte_identical_to_a_direct_run():
    feed = _scenario_feed()

    # (a) direct BacktestEngine.run, funded + configured exactly as the seam does.
    direct_log = _log("direct")
    state = PortfolioState()
    state.fund(VENUE, money("1000"))
    BacktestEngine(
        direct_log, config=EngineConfig(), state=state, venue_spec=HYPERLIQUID
    ).run(
        feed.candles,
        marks=feed.marks,
        funding=feed.funding,
        books=feed.books,
        trades=feed.trades,
        oi=feed.oi,
        strategy=_strategy(),
    )

    # (b) the same run reached through the SimulationEngine seam.
    seam_log = _log("seam")
    spec = EngineRunSpec(
        config=EngineConfig(),
        venue_spec=HYPERLIQUID,
        initial_capital="1000",
        fund_venue=VENUE,
        mark_policy="close_derived",
    )
    engine = engine_for("legacy-bar")()
    assert engine.name == "legacy-bar"
    engine.run(feed, _strategy(), event_log=seam_log, spec=spec)

    direct_rows = [e.to_row() for e in direct_log.read()]
    seam_rows = [e.to_row() for e in seam_log.read()]
    assert direct_rows == seam_rows  # byte-identical event log — zero behavior change
    # And the fixture genuinely exercised the cascade path.
    assert any(r["kind"] == LIQUIDATION for r in seam_rows)


def test_auto_resolves_to_legacy_bar_and_nautilus_is_not_built_yet():
    assert engine_for("auto") is LegacyBarEngine
    assert engine_for("legacy-bar") is LegacyBarEngine
    with pytest.raises(UnknownEngineError, match="not built yet"):
        engine_for("nautilus")
    with pytest.raises(UnknownEngineError, match="unknown engine"):
        engine_for("does-not-exist")


# --- the mark-policy fence (§6.0) -----------------------------------------


def _candle_only_tables() -> dict:
    table = _ArrowLike(
        [
            {
                "ts": T0,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 98.0,
                "volume": 1000.0,
                "market": MARKET,
                "resolution_s": HOUR_S,
                "venue": VENUE,
            }
        ]
    )
    return {(VENUE, MARKET, Kind.CANDLES): table}


class _ArrowLike:
    """A minimal stand-in for the Arrow tables ``build_engine_feed`` reads."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    @property
    def num_rows(self) -> int:
        return len(self._rows)

    def to_pylist(self) -> list[dict]:
        return list(self._rows)


def test_recorded_policy_lets_no_synthesized_mark_reach_the_engine():
    feed = build_engine_feed(
        _candle_only_tables(), {VENUE}, mark_policy="recorded"
    )
    assert feed.marks == {}  # candle-only feed, no synthesis under "recorded"


def test_close_derived_policy_synthesizes_todays_marks_exactly():
    feed = build_engine_feed(
        _candle_only_tables(), {VENUE}, mark_policy="close_derived"
    )
    # One close-derived mark at the first candle (mark = index = close), matching the
    # legacy build_engine_inputs behavior byte-for-byte.
    assert list(feed.marks) == [MARKET]
    (mark,) = feed.marks[MARKET]
    assert (mark.mark_price, mark.index_price) == (98.0, 98.0)

    legacy = build_engine_inputs(_candle_only_tables(), {VENUE})
    assert feed.marks == legacy.marks  # the fence preserves today's exact marks
