"""LiveFeed (slice 5.5, §6.7) — the paper/live feed, its clock, and gap replay.

Everything here runs on **hand-authored real-shape Hyperliquid WS frames** and
recorded-fragment lake data (D26 — never generated series, no network, no keys).
The frames are the exact ``trades`` / ``l2Book`` / ``activeAssetCtx`` shapes the
shared ``data.normalize`` parsers consume, so the feed proves it reuses the one
parser rather than re-implementing HL parsing.

Coverage:
* the paper clock advances on venue event time only, forward-only;
* bars close on the venue-event boundary crossing, with OHLCV from real trades;
* a tradeless bar yields no candle (never forward-filled);
* reconnect replays the lake gap through the *same* engine loop, contiguous and
  time-ordered, and settles funding on the replayed bars;
* a lake that lacks the gap is flagged ``degraded_fidelity`` without fabricating
  bars;
* overlapping gap data is de-duplicated (the feed's half of no-double-fill).
"""

from __future__ import annotations

import pytest

from flint.adapters import InMemoryUserData
from flint.core.models import Candle, FundingRate, MarkSnapshot, Signal
from flint.data.ingest.recorders import ReplayWsSource
from flint.data.livefeed import (
    InMemoryGapSource,
    LiveFeed,
    PaperClock,
    assemble_engine_inputs,
)
from flint.engine import EngineConfig
from flint.engine.api import EngineFeed, EngineRunSpec
from flint.engine.context import OpenInterestSnapshot
from flint.engine.portfolio import EventLog
from flint.engine.select import engine_for
from flint.ports import TenantContext
from flint.venues import HYPERLIQUID

VENUE = "hyperliquid"
COIN = "SOL"
MARKET = "SOL-PERP"
HOUR_S = 3600
HOUR_MS = HOUR_S * 1000
BASE_HOUR = 472_223  # a real 2023-era epoch hour, so bar starts are hour-aligned


def bar_start_ms(n: int) -> int:
    return (BASE_HOUR + n) * HOUR_MS


# --- hand-authored real-shape HL WS frames --------------------------------


def trades_frame(ts: int, px: float, sz: float, *, side: str = "B", tid: int = 1):
    return (
        "trades",
        [{"coin": COIN, "side": side, "px": str(px), "sz": str(sz),
          "time": ts, "hash": "0xabc", "tid": tid}],
    )


def book_frame(ts: int, bid: float, ask: float):
    return (
        "l2Book",
        {"coin": COIN, "time": ts, "levels": [
            [{"px": str(bid), "sz": "10.0", "n": 1}],
            [{"px": str(ask), "sz": "10.0", "n": 1}],
        ]},
    )


def ctx_frame(*, oi: float, mark: float, oracle: float, funding: float):
    return (
        "activeAssetCtx",
        {"coin": COIN, "ctx": {
            "openInterest": str(oi), "markPx": str(mark), "oraclePx": str(oracle),
            "funding": str(funding), "midPx": str(mark),
        }},
    )


# --- the paper clock -------------------------------------------------------


def test_paper_clock_is_venue_time_and_forward_only():
    clock = PaperClock(HOUR_S)
    assert clock.now is None and clock.current_bar_start is None

    clock.observe(bar_start_ms(0) + 100_000)
    assert clock.now == bar_start_ms(0) + 100_000
    assert clock.current_bar_start == bar_start_ms(0)

    # A later venue event advances the clock across the boundary.
    clock.observe(bar_start_ms(1) + 5_000)
    assert clock.current_bar_start == bar_start_ms(1)

    # A stale / reordered frame never rewinds it (a rewind would reopen a bar).
    clock.observe(bar_start_ms(0) + 200_000)
    assert clock.now == bar_start_ms(1) + 5_000


# --- bar aggregation -------------------------------------------------------


def test_bar_closes_on_venue_event_crossing_with_real_ohlcv():
    feed = LiveFeed(MARKET, resolution_s=HOUR_S)
    frames = [
        trades_frame(bar_start_ms(0) + 100_000, 100.0, 1.0, tid=1),
        trades_frame(bar_start_ms(0) + 120_000, 105.0, 2.0, tid=2),
        trades_frame(bar_start_ms(0) + 140_000, 98.0, 1.0, tid=3),
        ctx_frame(oi=5000.0, mark=101.0, oracle=100.5, funding=0.0001),
        trades_frame(bar_start_ms(0) + 160_000, 102.0, 3.0, tid=4),
        # A trade in bar 1 crosses the boundary and closes bar 0.
        trades_frame(bar_start_ms(1) + 50_000, 110.0, 1.0, tid=5),
    ]
    bars = feed.connect(ReplayWsSource(frames))

    assert len(bars) == 1  # only bar 0 closed; bar 1 is still open
    bar = bars[0]
    c = bar.candle
    assert (c.ts, c.market, c.venue, c.resolution_s) == (bar_start_ms(0), MARKET, VENUE, HOUR_S)
    assert (c.open, c.high, c.low, c.close) == (100.0, 105.0, 98.0, 102.0)
    assert c.volume == 7.0
    assert bar.origin == "live"
    # The activeAssetCtx snapshot folded into mark / OI / predicted funding,
    # stamped at the latest venue event when it arrived (the +140_000 trade).
    assert bar.marks == (MarkSnapshot(MARKET, bar_start_ms(0) + 140_000, 101.0, 100.5, VENUE),)
    assert bar.oi == (OpenInterestSnapshot(MARKET, bar_start_ms(0) + 140_000, 5000.0, VENUE),)
    assert len(bar.funding) == 1 and bar.funding[0].rate_type == "predicted"
    assert bar.funding[0].rate_hourly == 0.0001

    # The trailing partial bar 1 flushes only on a graceful session end.
    tail = feed.close_session()
    assert len(tail) == 1 and tail[0].candle.ts == bar_start_ms(1)


def test_ctx_snapshot_stamped_at_venue_time_not_wall_clock():
    # The ctx snapshot carries no ts of its own; it must be stamped with the last
    # venue event time (§6.7), i.e. the trade at +140_000 — not any wall clock.
    feed = LiveFeed(MARKET, resolution_s=HOUR_S)
    frames = [
        trades_frame(bar_start_ms(0) + 140_000, 100.0, 1.0),
        ctx_frame(oi=1.0, mark=100.0, oracle=100.0, funding=0.0),
        trades_frame(bar_start_ms(1) + 10_000, 101.0, 1.0),
    ]
    bar = feed.connect(ReplayWsSource(frames))[0]
    assert bar.marks[0].ts == bar_start_ms(0) + 140_000


def test_ctx_before_any_venue_time_is_dropped_not_wall_stamped():
    # A ctx frame arriving before any trade/book has no venue time to anchor to;
    # stamping it with the wall clock is forbidden, so it is dropped (D26).
    feed = LiveFeed(MARKET, resolution_s=HOUR_S)
    frames = [
        ctx_frame(oi=1.0, mark=100.0, oracle=100.0, funding=0.0),
        trades_frame(bar_start_ms(0) + 100_000, 100.0, 1.0),
        trades_frame(bar_start_ms(1) + 100_000, 101.0, 1.0),
    ]
    bar = feed.connect(ReplayWsSource(frames))[0]
    assert bar.marks == ()  # the pre-anchor ctx frame produced no mark


def test_tradeless_bar_yields_no_candle_never_forward_filled():
    feed = LiveFeed(MARKET, resolution_s=HOUR_S)
    frames = [
        trades_frame(bar_start_ms(0) + 100_000, 100.0, 1.0),
        # bar 1 sees only a book update (carries venue ts), never a trade.
        book_frame(bar_start_ms(1) + 50_000, 99.0, 101.0),
        # bar 2 trade closes bar 1 — which has no trade, so no candle.
        trades_frame(bar_start_ms(2) + 50_000, 105.0, 1.0),
    ]
    bars = feed.connect(ReplayWsSource(frames))
    starts = [b.candle.ts for b in bars]
    assert starts == [bar_start_ms(0)]  # bar 1 absent, not a forward-filled flat
    assert bar_start_ms(1) not in starts


# --- reconnect gap replay --------------------------------------------------


def _gap_candle(n: int, close: float) -> Candle:
    return Candle(
        ts=bar_start_ms(n), open=close, high=close, low=close, close=close,
        volume=1.0, market=MARKET, resolution_s=HOUR_S, venue=VENUE,
    )


def test_reconnect_replays_lake_gap_through_the_same_engine_loop():
    # Lake holds authoritative bars 2,3,4 + a final funding settlement + a mark to
    # price it — recorded fragments the runner would read on reconnect.
    gap = InMemoryGapSource(
        candles={(VENUE, MARKET): [_gap_candle(2, 112.0), _gap_candle(3, 113.0), _gap_candle(4, 114.0)]},
        funding={(VENUE, MARKET): [FundingRate(
            ts=bar_start_ms(2) + 1_000, rate_hourly=0.0002, interval_s=HOUR_S,
            price_basis="oracle", rate_type="final", venue=VENUE, market=MARKET)]},
        marks={(VENUE, MARKET): [MarkSnapshot(MARKET, bar_start_ms(2) + 500, 112.0, 112.0, VENUE)]},
    )
    feed = LiveFeed(MARKET, resolution_s=HOUR_S, gap_source=gap)

    # Connection 1: bars 0 and 1 close (a bar-2 trade closes bar 1, then drop).
    conn1 = [
        trades_frame(bar_start_ms(0) + 100_000, 100.0, 1.0, tid=1),
        trades_frame(bar_start_ms(1) + 100_000, 101.0, 1.0, tid=2),
        trades_frame(bar_start_ms(2) + 10_000, 111.0, 1.0, tid=3),  # opens bar2, dropped
    ]
    bars1 = feed.connect(ReplayWsSource(conn1))
    assert [b.candle.ts for b in bars1] == [bar_start_ms(0), bar_start_ms(1)]

    # Connection 2: reconnect lands in bar 5 — bars 2,3,4 were missed.
    conn2 = [
        trades_frame(bar_start_ms(5) + 20_000, 120.0, 1.0, tid=9),
        trades_frame(bar_start_ms(6) + 20_000, 121.0, 1.0, tid=10),  # closes bar5
    ]
    bars2 = feed.connect(ReplayWsSource(conn2))
    assert [b.candle.ts for b in bars2] == [
        bar_start_ms(2), bar_start_ms(3), bar_start_ms(4), bar_start_ms(5)]
    assert [b.origin for b in bars2] == ["gap-replay", "gap-replay", "gap-replay", "live"]

    # One recovery record, fully recovered, "recovered from 180-minute gap".
    assert len(feed.recoveries) == 1
    rec = feed.recoveries[0]
    assert rec.bars_expected == 3 and rec.bars_recovered == 3
    assert rec.degraded_fidelity is False
    assert rec.gap_minutes == 180.0
    assert rec.message == "recovered from 180-minute gap"

    # Drive the SAME engine over the full stream: a long opened on bar 0 fills at
    # bar 1's open, then funding settles on a replayed gap bar — proving the gap
    # updates positions/funding as if live.
    # Lower the live + gap-replayed bars into an EngineFeed and drive the SAME engine
    # seam the paper lane uses (flint/live/session.py) — the Nautilus bar lane as of
    # N10. A long opened on bar 0 fills at bar 1's open, then funding settles on a
    # replayed gap bar, proving the gap updates positions/funding as if live.
    pytest.importorskip("nautilus_trader")
    all_bars = bars1 + bars2 + feed.close_session()
    inputs = assemble_engine_inputs(all_bars)
    store = InMemoryUserData()
    log = EventLog(store, TenantContext.local(), run_id="livefeed-5.5")
    spec = EngineRunSpec(
        config=EngineConfig(),
        venue_spec=HYPERLIQUID,
        initial_capital="100000",
        fund_venue=VENUE,
        mark_policy="close_derived",
    )
    feed_in = EngineFeed(
        candles=inputs.candles,
        marks=inputs.marks,
        funding=inputs.funding,
        books=inputs.books,
        trades=inputs.trades,
        oi=inputs.oi,
    )

    class _OpenOnce:
        def __init__(self) -> None:
            self.done = False

        def on_candle(self, candle, ctx):
            if not self.done:
                self.done = True
                return [Signal.long(MARKET, VENUE, size_usd=1000.0)]
            return []

    state = engine_for("nautilus")().run(
        feed_in, _OpenOnce(), event_log=log, spec=spec
    )

    assert state.position(VENUE, MARKET) is not None  # position carried across the gap
    assert state.account(VENUE).funding_paid != 0  # gap's final funding settled


def test_gap_flagged_degraded_when_lake_lacks_the_data():
    feed = LiveFeed(MARKET, resolution_s=HOUR_S, gap_source=InMemoryGapSource())  # empty lake

    feed.connect(ReplayWsSource([
        trades_frame(bar_start_ms(0) + 100_000, 100.0, 1.0, tid=1),
        trades_frame(bar_start_ms(1) + 100_000, 101.0, 1.0, tid=2),
        trades_frame(bar_start_ms(2) + 10_000, 111.0, 1.0, tid=3),
    ]))
    bars2 = feed.connect(ReplayWsSource([
        trades_frame(bar_start_ms(5) + 20_000, 120.0, 1.0, tid=9),
        trades_frame(bar_start_ms(6) + 20_000, 121.0, 1.0, tid=10),
    ]))

    # No fabricated gap bars — only the live bar 5 comes through.
    assert [b.candle.ts for b in bars2] == [bar_start_ms(5)]
    assert not any(bar_start_ms(n) == b.candle.ts for n in (2, 3, 4) for b in bars2)
    rec = feed.recoveries[0]
    assert rec.bars_expected == 3 and rec.bars_recovered == 0
    assert rec.degraded_fidelity is True
    assert "degraded" in rec.message and "180-minute gap" in rec.message


def test_contiguous_reconnect_is_not_a_gap():
    feed = LiveFeed(MARKET, resolution_s=HOUR_S, gap_source=InMemoryGapSource())
    feed.connect(ReplayWsSource([
        trades_frame(bar_start_ms(0) + 100_000, 100.0, 1.0, tid=1),
        trades_frame(bar_start_ms(1) + 10_000, 101.0, 1.0, tid=2),  # opens bar1, dropped
    ]))
    # Reconnect resumes in bar 1 (the very next bar) — no missed state.
    bars2 = feed.connect(ReplayWsSource([
        trades_frame(bar_start_ms(1) + 100_000, 102.0, 1.0, tid=3),
        trades_frame(bar_start_ms(2) + 10_000, 103.0, 1.0, tid=4),  # closes bar1
    ]))
    assert [b.candle.ts for b in bars2] == [bar_start_ms(1)]
    assert feed.recoveries == []


def test_overlapping_gap_data_is_deduplicated():
    # The lake returns bar 1 (already emitted) alongside the true gap 2,3,4; the
    # feed's cursor drops the overlap so a bar is never emitted — or filled — twice.
    gap = InMemoryGapSource(candles={(VENUE, MARKET): [
        _gap_candle(1, 101.0), _gap_candle(2, 112.0),
        _gap_candle(3, 113.0), _gap_candle(4, 114.0)]})
    feed = LiveFeed(MARKET, resolution_s=HOUR_S, gap_source=gap)

    feed.connect(ReplayWsSource([
        trades_frame(bar_start_ms(0) + 100_000, 100.0, 1.0, tid=1),
        trades_frame(bar_start_ms(1) + 100_000, 101.0, 1.0, tid=2),
        trades_frame(bar_start_ms(2) + 10_000, 111.0, 1.0, tid=3),
    ]))
    bars2 = feed.connect(ReplayWsSource([
        trades_frame(bar_start_ms(5) + 20_000, 120.0, 1.0, tid=9),
        trades_frame(bar_start_ms(6) + 20_000, 121.0, 1.0, tid=10),
    ]))
    gap_starts = [b.candle.ts for b in bars2 if b.origin == "gap-replay"]
    assert gap_starts == [bar_start_ms(2), bar_start_ms(3), bar_start_ms(4)]
    assert bar_start_ms(1) not in gap_starts  # the overlap was dropped


# --- D3: recorder tee — paper sessions passively build tick history (§9.2) ---


def bbo_frame(ts: int, bid: float, ask: float):
    return (
        "bbo",
        {"coin": COIN, "time": ts, "bbo": [
            {"px": str(bid), "sz": "5.0", "n": 2},
            {"px": str(ask), "sz": "4.0", "n": 1},
        ]},
    )


def _tee_recorder(ledger_root=None):
    from flint.data import InMemoryCacheSource
    from flint.data.ingest.recorders import HyperliquidRecorder
    from flint.data.store.coverage import CoverageLedger

    sink = InMemoryCacheSource()
    ledger_of = None
    if ledger_root is not None:
        ledger_of = lambda venue, market, kind: CoverageLedger(  # noqa: E731
            ledger_root / kind.value / venue / market
        )
    rec = HyperliquidRecorder(
        sink, clock=lambda: bar_start_ms(1), ledger_of=ledger_of
    )
    return sink, rec


def test_feed_tee_frames_reach_both_consumer_and_recorder():
    from flint.data import Kind, TimeRange

    sink, rec = _tee_recorder()
    feed = LiveFeed(MARKET, resolution_s=HOUR_S, recorder_sink=rec)
    t0 = bar_start_ms(0)
    bars = feed.connect(ReplayWsSource([
        trades_frame(t0 + 10, 100.0, 1.0, tid=1),
        bbo_frame(t0 + 20, 99.9, 100.1),
        trades_frame(bar_start_ms(1) + 10, 101.0, 1.0, tid=2),  # closes bar 0
    ]))
    # Consumer side: the feed still closes and emits bar 0.
    assert [b.candle.ts for b in bars] == [t0]
    # Recorder side: the same frames landed in the durable sink (tee flushed
    # at end of connect) — trades AND the bbo frame the feed itself ignores.
    span = TimeRange(0, bar_start_ms(2))
    assert sink.fetch(VENUE, MARKET, Kind.TRADES, span).num_rows == 2
    assert sink.fetch(VENUE, MARKET, Kind.QUOTES, span).num_rows == 1


def test_feed_tee_skips_l2book_by_default_but_records_it_on_opt_in():
    from flint.data import Kind, TimeRange

    span = TimeRange(0, bar_start_ms(2))
    t0 = bar_start_ms(0)
    frames = [
        trades_frame(t0 + 10, 100.0, 1.0, tid=1),
        book_frame(t0 + 20, 99.9, 100.1),
    ]

    sink, rec = _tee_recorder()
    LiveFeed(MARKET, resolution_s=HOUR_S, recorder_sink=rec).connect(
        ReplayWsSource(frames)
    )
    assert sink.fetch(VENUE, MARKET, Kind.DEPTH, span).num_rows == 0  # off by default

    sink2, rec2 = _tee_recorder()
    LiveFeed(
        MARKET,
        resolution_s=HOUR_S,
        recorder_sink=rec2,
        record_channels=frozenset({"trades", "l2Book"}),
    ).connect(ReplayWsSource(frames))
    assert sink2.fetch(VENUE, MARKET, Kind.DEPTH, span).num_rows == 1  # opt-in


def test_feed_reconnect_splits_recorded_coverage(tmp_path):
    from flint.data import TimeRange
    from flint.data.store.coverage import CoverageLedger

    _, rec = _tee_recorder(ledger_root=tmp_path)
    feed = LiveFeed(MARKET, resolution_s=HOUR_S, recorder_sink=rec)
    t0 = bar_start_ms(0)
    feed.connect(ReplayWsSource([
        trades_frame(t0 + 10, 100.0, 1.0, tid=1),
        trades_frame(t0 + 20, 100.5, 1.0, tid=2),
    ]))
    # Connection drops; the next connect is a reconnect — the recorder's
    # coverage window must close at the last good event, not span the outage.
    t5 = bar_start_ms(5)
    feed.connect(ReplayWsSource([
        trades_frame(t5 + 10, 102.0, 1.0, tid=9),
        trades_frame(t5 + 20, 102.5, 1.0, tid=10),
    ]))
    ledger = CoverageLedger(tmp_path / "trades" / "hyperliquid" / MARKET)
    assert ledger.covered().ranges == (
        TimeRange(t0 + 10, t0 + 20),
        TimeRange(t5 + 10, t5 + 20),
    )
