"""HL WS recorder + sequence/lag monitors (2.5, §9.1, §9.0).

Recorder is driven by a deterministic ReplayWsSource over recorded frame
fragments — no socket (D26). Sink is the in-memory cache tier.
"""

from __future__ import annotations

from flint.data import InMemoryCacheSource, Kind, TimeRange
from flint.data.ingest.recorders import (
    HyperliquidRecorder,
    LagMonitor,
    ReplayWsSource,
    SeqMode,
    SequenceTracker,
)

SPAN = TimeRange(0, 10_000)


def _trades(*items):
    return ("trades", list(items))


def _trade(tid, t, side="B", px="100", sz="1"):
    return {"coin": "SOL", "side": side, "px": px, "sz": sz, "time": t, "tid": tid}


def _book(t, coin="SOL"):
    return (
        "l2Book",
        {"coin": coin, "time": t, "levels": [[{"px": "100", "sz": "5"}], [{"px": "101", "sz": "4"}]]},
    )


def _ctx(coin="SOL"):
    return (
        "activeAssetCtx",
        {"coin": coin, "ctx": {"openInterest": "1000", "markPx": "100.4",
                               "oraclePx": "100.3", "funding": "0.0000125"}},
    )


# --- recorder: capture + persist all three channels --------------------------


def test_recorder_persists_all_three_channels():
    clock = [5000]
    sink = InMemoryCacheSource()
    rec = HyperliquidRecorder(sink, clock=lambda: clock[0])

    stats = rec.run(
        ReplayWsSource([_trades(_trade(1, 900), _trade(2, 950)), _book(960), _ctx()]),
        flush_every=100,
    )
    assert stats.rows_written == {Kind.TRADES: 2, Kind.DEPTH: 1, Kind.OI: 1}

    trades = sink.fetch("hyperliquid", "SOL-PERP", Kind.TRADES, SPAN)
    assert trades.column("side").to_pylist() == ["buy", "buy"]
    # ctx carries no event ts -> stamped with the clock's receipt time.
    oi = sink.fetch("hyperliquid", "SOL-PERP", Kind.OI, SPAN)
    assert oi.column("ts").to_pylist() == [5000]
    assert oi.column("mark_price").to_pylist() == [100.4]


def test_recorder_flush_batching_writes_the_same_rows():
    # flush_every smaller than the frame count exercises mid-run flushes; the
    # total persisted must be identical to a single final flush.
    sink = InMemoryCacheSource()
    rec = HyperliquidRecorder(sink, clock=lambda: 1)
    frames = [_book(t) for t in (10, 20, 30, 40, 50)]
    stats = rec.run(ReplayWsSource(frames), flush_every=2)
    assert stats.rows_written == {Kind.DEPTH: 5}
    assert sink.fetch("hyperliquid", "SOL-PERP", Kind.DEPTH, SPAN).num_rows == 5


def test_recorder_upsert_is_idempotent_on_replay():
    sink = InMemoryCacheSource()
    frames = [_book(960)]
    HyperliquidRecorder(sink, clock=lambda: 1).run(ReplayWsSource(frames), flush_every=1)
    HyperliquidRecorder(sink, clock=lambda: 1).run(ReplayWsSource(frames), flush_every=1)
    assert sink.fetch("hyperliquid", "SOL-PERP", Kind.DEPTH, SPAN).num_rows == 1


def test_recorder_detects_trade_id_regression():
    sink = InMemoryCacheSource()
    rec = HyperliquidRecorder(sink, clock=lambda: 1)
    # tid 2 then 1 -> a regression (out-of-order / replayed trade frame).
    stats = rec.run(
        ReplayWsSource([_trades(_trade(2, 900)), _trades(_trade(1, 950))]),
        flush_every=100,
    )
    assert stats.seq_regressions == 1


def test_recorder_splits_streams_by_market():
    sink = InMemoryCacheSource()
    rec = HyperliquidRecorder(sink, clock=lambda: 1)
    rec.run(ReplayWsSource([_book(10, "SOL"), _book(20, "BTC")]), flush_every=100)
    assert sink.fetch("hyperliquid", "SOL-PERP", Kind.DEPTH, SPAN).num_rows == 1
    assert sink.fetch("hyperliquid", "BTC-PERP", Kind.DEPTH, SPAN).num_rows == 1


# --- SequenceTracker ---------------------------------------------------------


def test_sequence_tracker_first_value_is_ok():
    tracker = SequenceTracker(SeqMode.STRICTLY_INCREASING)
    assert tracker.observe("s", 5).status == "ok"


def test_strictly_increasing_flags_duplicate_and_regression():
    tracker = SequenceTracker(SeqMode.STRICTLY_INCREASING)
    tracker.observe("s", 5)
    assert tracker.observe("s", 5).status == "duplicate"
    assert tracker.observe("s", 4).status == "regression"
    # A regression does not advance the high-water mark: 6 after 5 is still ok.
    assert tracker.observe("s", 6).status == "ok"
    assert tracker.duplicates == 1
    assert tracker.regressions == 1


def test_contiguous_mode_counts_a_gap():
    tracker = SequenceTracker(SeqMode.CONTIGUOUS)
    tracker.observe("s", 1)
    result = tracker.observe("s", 4)  # 2 and 3 missed
    assert result.status == "gap"
    assert result.missed == 2
    assert tracker.gaps == 1
    assert tracker.missed_total == 2


def test_non_decreasing_allows_repeats():
    tracker = SequenceTracker(SeqMode.NON_DECREASING)
    tracker.observe("s", 100)
    assert tracker.observe("s", 100).status == "duplicate"  # equal = duplicate
    assert tracker.observe("s", 101).status == "ok"


def test_sequence_streams_are_independent():
    tracker = SequenceTracker(SeqMode.STRICTLY_INCREASING)
    tracker.observe("a", 10)
    tracker.observe("b", 1)
    assert tracker.observe("a", 11).status == "ok"
    assert tracker.observe("b", 2).status == "ok"


# --- LagMonitor --------------------------------------------------------------


def test_lag_monitor_reports_age_of_newest_record():
    now = [1000]
    lag = LagMonitor(lambda: now[0])
    lag.record("hyperliquid", "SOL-PERP", Kind.DEPTH, 940)
    lag.record("hyperliquid", "SOL-PERP", Kind.DEPTH, 900)  # older, ignored
    assert lag.lag_ms("hyperliquid", "SOL-PERP", Kind.DEPTH) == 60
    assert lag.lag_ms("hyperliquid", "BTC-PERP", Kind.DEPTH) is None


def test_lag_monitor_staleness_query():
    now = [2000]
    lag = LagMonitor(lambda: now[0])
    lag.record("hyperliquid", "SOL-PERP", Kind.DEPTH, 1990)  # lag 10
    lag.record("hyperliquid", "BTC-PERP", Kind.OI, 1000)  # lag 1000
    stale = lag.stale(threshold_ms=500)
    assert stale == [("hyperliquid", "BTC-PERP", Kind.OI)]
