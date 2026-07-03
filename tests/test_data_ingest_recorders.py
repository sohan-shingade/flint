"""HL WS recorder + sequence/lag monitors (2.5, §9.1, §9.0).

Recorder is driven by a deterministic ReplayWsSource over recorded frame
fragments — no socket (D26). Sink is the in-memory cache tier.
"""

from __future__ import annotations

import json

import pytest

from flint.data import InMemoryCacheSource, Kind, TimeRange
from flint.data.ingest.recorders import (
    HyperliquidRecorder,
    LagMonitor,
    ReplayWsSource,
    SeqMode,
    SequenceTracker,
    next_hour_boundary,
    subscribe_messages,
)
from flint.data.store.coverage import CoverageLedger

SPAN = TimeRange(0, 10_000)

HOUR_MS = 3_600_000


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


def test_recorder_persists_all_channels():
    clock = [5000]
    sink = InMemoryCacheSource()
    rec = HyperliquidRecorder(sink, clock=lambda: clock[0])

    stats = rec.run(
        ReplayWsSource([_trades(_trade(1, 900), _trade(2, 950)), _book(960), _ctx()]),
        flush_every=100,
    )
    # A ctx frame folds into an OI row AND a predicted FUNDING row (§9.2).
    assert stats.rows_written == {
        Kind.TRADES: 2,
        Kind.DEPTH: 1,
        Kind.OI: 1,
        Kind.FUNDING: 1,
    }

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


# --- D3: bbo -> QUOTES -------------------------------------------------------


def _bbo(t, coin="SOL", bid=("100.3", "5"), ask=("100.5", "4")):
    """An HL ``bbo`` frame: {coin, time, bbo: [bid|null, ask|null]} (§9.2)."""
    level = lambda px_sz, n: (  # noqa: E731 - tiny fixture builder
        None if px_sz is None else {"px": px_sz[0], "sz": px_sz[1], "n": n}
    )
    return ("bbo", {"coin": coin, "time": t, "bbo": [level(bid, 2), level(ask, 1)]})


def test_recorder_persists_bbo_as_quotes():
    sink = InMemoryCacheSource()
    rec = HyperliquidRecorder(sink, clock=lambda: 1)
    stats = rec.run(
        ReplayWsSource([_bbo(900), _bbo(950, bid=("100.4", "3"))]), flush_every=100
    )
    assert stats.rows_written == {Kind.QUOTES: 2}
    quotes = sink.fetch("hyperliquid", "SOL-PERP", Kind.QUOTES, SPAN)
    assert quotes.column("ts").to_pylist() == [900, 950]
    assert quotes.column("bid_px").to_pylist() == [100.3, 100.4]
    assert quotes.column("ask_sz").to_pylist() == [4.0, 4.0]


def test_recorder_bbo_empty_side_stays_null():
    # A null side is recorded as absent, never fabricated (D26).
    sink = InMemoryCacheSource()
    rec = HyperliquidRecorder(sink, clock=lambda: 1)
    rec.run(ReplayWsSource([_bbo(900, ask=None)]), flush_every=1)
    quotes = sink.fetch("hyperliquid", "SOL-PERP", Kind.QUOTES, SPAN)
    assert quotes.column("bid_px").to_pylist() == [100.3]
    assert quotes.column("ask_px").to_pylist() == [None]
    assert quotes.column("ask_sz").to_pylist() == [None]


# --- D3: predicted-funding capture from activeAssetCtx -----------------------


def _ctx_rate(funding, coin="SOL"):
    return (
        "activeAssetCtx",
        {"coin": coin, "ctx": {"openInterest": "1000", "markPx": "100.4",
                               "oraclePx": "100.3", "funding": funding}},
    )


def test_predicted_funding_row_shape():
    clock = [7_200_500]  # mid-hour capture time
    sink = InMemoryCacheSource()
    rec = HyperliquidRecorder(sink, clock=lambda: clock[0])
    rec.process(*_ctx_rate("0.0000125"))
    rec.flush()
    funding = sink.fetch("hyperliquid", "SOL-PERP", Kind.FUNDING, TimeRange(0, 10**9))
    assert funding.num_rows == 1
    row = funding.to_pylist()[0]
    assert row["ts"] == 7_200_500  # capture clock, the §6.4 "known at" time
    assert row["rate_hourly"] == 0.0000125
    assert row["rate_type"] == "predicted"
    assert row["price_basis"] == "oracle"
    assert row["interval_s"] == 3600
    assert row["settlement_ts"] == 10_800_000  # the next hour boundary


def test_predicted_funding_emits_on_change_only():
    clock = [1000]
    sink = InMemoryCacheSource()
    rec = HyperliquidRecorder(sink, clock=lambda: clock[0])
    for ts, rate in ((1000, "0.0000125"), (2000, "0.0000125"), (3000, "0.0000200")):
        clock[0] = ts
        rec.process(*_ctx_rate(rate))
    stats_rows = rec.flush()
    # 3 ctx frames -> 3 OI rows but only 2 FUNDING rows (first + the change).
    assert stats_rows[Kind.OI] == 3
    assert stats_rows[Kind.FUNDING] == 2
    funding = sink.fetch("hyperliquid", "SOL-PERP", Kind.FUNDING, SPAN)
    assert funding.column("ts").to_pylist() == [1000, 3000]
    assert funding.column("rate_hourly").to_pylist() == [0.0000125, 0.00002]


def test_next_hour_boundary_on_the_boundary_rolls_forward():
    # A rate captured exactly on the hour settles at the NEXT boundary — the
    # settlement at that instant has already happened.
    assert next_hour_boundary(HOUR_MS) == 2 * HOUR_MS
    assert next_hour_boundary(HOUR_MS + 1) == 2 * HOUR_MS


# --- D3: CoverageLedger hook (§9.2) ------------------------------------------


def _ledger_factory(root):
    def ledger_of(venue, market, kind):
        return CoverageLedger(root / kind.value / venue / market)

    return ledger_of


def test_flush_asserts_session_start_to_newest_event(tmp_path):
    sink = InMemoryCacheSource()
    rec = HyperliquidRecorder(
        sink,
        clock=lambda: 1,
        ledger_of=_ledger_factory(tmp_path),
        session_start_ts=500,
    )
    rec.run(ReplayWsSource([_bbo(1000), _bbo(2000), _bbo(3000)]), flush_every=100)
    ledger = CoverageLedger(tmp_path / "quotes" / "hyperliquid" / "SOL-PERP")
    # Covered since subscribe time (a quiet stream before its first event is
    # data), half-open at the newest event.
    assert ledger.covered().ranges == (TimeRange(500, 3000),)
    assert all(e.source == "recorder" for e in ledger.entries)


def test_repeated_flushes_extend_one_seamless_range(tmp_path):
    sink = InMemoryCacheSource()
    rec = HyperliquidRecorder(sink, clock=lambda: 1, ledger_of=_ledger_factory(tmp_path))
    # flush_every=1 -> a flush per frame; the windows must chain, not gap.
    rec.run(ReplayWsSource([_bbo(1000), _bbo(2000), _bbo(3000)]), flush_every=1)
    ledger = CoverageLedger(tmp_path / "quotes" / "hyperliquid" / "SOL-PERP")
    assert ledger.covered().ranges == (TimeRange(1000, 3000),)


def test_sequence_gap_splits_the_asserted_range(tmp_path):
    sink = InMemoryCacheSource()
    rec = HyperliquidRecorder(sink, clock=lambda: 1, ledger_of=_ledger_factory(tmp_path))
    # Hand-wire a CONTIGUOUS tracker so a tid jump is a detected gap.
    rec._seq_trades = SequenceTracker(SeqMode.CONTIGUOUS)
    rec.run(
        ReplayWsSource(
            [
                _trades(_trade(1, 1000), _trade(2, 2000)),
                _trades(_trade(5, 5000), _trade(6, 6000)),  # tids 3,4 missed
            ]
        ),
        flush_every=100,
    )
    ledger = CoverageLedger(tmp_path / "trades" / "hyperliquid" / "SOL-PERP")
    # Closed at the last good event, reopened at recovery — the missed span
    # between them is NOT covered.
    assert ledger.covered().ranges == (TimeRange(1000, 2000), TimeRange(5000, 6000))


def test_reconnect_closes_and_reopens_the_range(tmp_path):
    sink = InMemoryCacheSource()
    rec = HyperliquidRecorder(
        sink,
        clock=lambda: 1,
        ledger_of=_ledger_factory(tmp_path),
        session_start_ts=500,
    )
    rec.run(ReplayWsSource([_bbo(1000), _bbo(2000)]), flush_every=100)
    rec.mark_disconnect()
    rec.run(ReplayWsSource([_bbo(5000), _bbo(6000)]), flush_every=100)
    ledger = CoverageLedger(tmp_path / "quotes" / "hyperliquid" / "SOL-PERP")
    # First window anchors at session start; the post-reconnect window reopens
    # at the recovery event — the offline span stays uncovered.
    assert ledger.covered().ranges == (TimeRange(500, 2000), TimeRange(5000, 6000))


def test_close_session_flushes_and_closes_windows(tmp_path):
    sink = InMemoryCacheSource()
    rec = HyperliquidRecorder(sink, clock=lambda: 1, ledger_of=_ledger_factory(tmp_path))
    rec.process(*_bbo(1000))
    rec.process(*_bbo(2000))
    written = rec.close_session()
    assert written == {Kind.QUOTES: 2}
    ledger = CoverageLedger(tmp_path / "quotes" / "hyperliquid" / "SOL-PERP")
    assert ledger.covered().ranges == (TimeRange(1000, 2000),)
    # The session is closed: a later event opens a fresh window at its own ts.
    rec.process(*_bbo(9000))
    rec.process(*_bbo(9500))
    rec.close_session()
    assert ledger.__class__(ledger.path.parent).covered().ranges == (
        TimeRange(1000, 2000),
        TimeRange(9000, 9500),
    )


def test_no_ledger_factory_means_no_coverage_side_effects(tmp_path):
    sink = InMemoryCacheSource()
    rec = HyperliquidRecorder(sink, clock=lambda: 1)
    rec.run(ReplayWsSource([_bbo(1000), _bbo(2000)]), flush_every=100)
    assert list(tmp_path.iterdir()) == []  # nothing asserted anywhere


# --- D3: live WS subscribe payloads (pure, no socket) ------------------------


def test_subscribe_messages_shape_and_coin_mapping():
    subs = subscribe_messages(["SOL-PERP", "BTC-PERP"], ["trades", "bbo"])
    assert {json.dumps(s, sort_keys=True) for s in subs} == {
        json.dumps(
            {"method": "subscribe", "subscription": {"type": t, "coin": c}},
            sort_keys=True,
        )
        for t in ("trades", "bbo")
        for c in ("SOL", "BTC")
    }


def test_subscribe_messages_rejects_unknown_channels():
    with pytest.raises(ValueError, match="unrecordable"):
        subscribe_messages(["SOL-PERP"], ["candles"])
