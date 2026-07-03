"""Ingestion quality bars: cadence gap detection + pre-write checks (2.4, §9.0).

All inputs are hand-authored ts-keyed Arrow tables (unit inputs, never generated
market data — D26). No network, no I/O.
"""

from __future__ import annotations

import pyarrow as pa

from flint.data.ingest import (
    check_prewrite,
    detect_candle_gaps,
    expected_bar_count,
)
from flint.data.ranges import RangeSet, TimeRange

_MIN = 60_000  # one 1m bar in ms


def _candles(ts_values: list[int], *, closes: list[float] | None = None,
             vols: list[float] | None = None) -> pa.Table:
    n = len(ts_values)
    return pa.table(
        {
            "ts": ts_values,
            "close": closes if closes is not None else [100.0] * n,
            "volume": vols if vols is not None else [1.0] * n,
        }
    )


# --- expected_bar_count ------------------------------------------------------


def test_expected_bar_count_counts_aligned_bars_in_span():
    # [0, 300000) at 60s = bars at 0,60k,120k,180k,240k = 5.
    assert expected_bar_count(TimeRange(0, 5 * _MIN), 60) == 5


def test_expected_bar_count_empty_and_unaligned_start():
    assert expected_bar_count(TimeRange(0, 0), 60) == 0
    # Start at 30s into a bar: first aligned bar is 60k; [30k,180k) -> 60k,120k = 2.
    assert expected_bar_count(TimeRange(30_000, 3 * _MIN), 60) == 2


# --- detect_candle_gaps (cadence-aware internal gaps) ------------------------


def test_no_gaps_when_every_bar_present():
    table = _candles([0, _MIN, 2 * _MIN, 3 * _MIN])
    assert detect_candle_gaps(table, 60, TimeRange(0, 4 * _MIN)) == RangeSet()


def test_internal_gap_is_detected_and_coalesced():
    # Bars at 0 and 3m present; 1m and 2m missing -> one coalesced [60k,180k).
    table = _candles([0, 3 * _MIN])
    gaps = detect_candle_gaps(table, 60, TimeRange(0, 4 * _MIN))
    assert gaps == RangeSet((TimeRange(_MIN, 3 * _MIN),))


def test_empty_table_makes_the_whole_span_a_gap():
    gaps = detect_candle_gaps(pa.table({}), 60, TimeRange(0, 2 * _MIN))
    assert gaps == RangeSet((TimeRange(0, 2 * _MIN),))


def test_empty_span_has_no_gaps():
    assert detect_candle_gaps(_candles([0]), 60, TimeRange(0, 0)) == RangeSet()


# --- check_prewrite ----------------------------------------------------------


def test_clean_batch_is_ok_with_no_findings():
    report = check_prewrite(_candles([0, _MIN, 2 * _MIN]))
    assert report.ok
    assert report.errors == ()
    assert report.warnings == ()
    assert report.rows == 3


def test_out_of_order_timestamps_is_an_error():
    report = check_prewrite(_candles([0, 2 * _MIN, _MIN]))
    assert not report.ok
    assert any("sorted" in e for e in report.errors)


def test_duplicate_ts_is_an_error():
    report = check_prewrite(_candles([0, _MIN, _MIN]))
    assert not report.ok
    assert any("duplicate" in e for e in report.errors)


def test_price_spike_is_a_warning_not_an_error():
    # 100 -> 100 -> 5000: a 50x jump exceeds the default 10x spike ratio.
    report = check_prewrite(_candles([0, _MIN, 2 * _MIN], closes=[100.0, 100.0, 5000.0]))
    assert report.ok  # warnings do not block
    assert any("spike" in w for w in report.warnings)


def test_zero_volume_candles_are_warned():
    report = check_prewrite(_candles([0, _MIN], vols=[0.0, 1.0]))
    assert report.ok
    assert any("zero-volume" in w for w in report.warnings)


def test_empty_table_is_trivially_ok():
    report = check_prewrite(pa.table({}))
    assert report.ok
    assert report.rows == 0
