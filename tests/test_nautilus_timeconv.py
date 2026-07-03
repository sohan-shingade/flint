"""Time-base conversion between Flint (unix-ms, bar-START) and Nautilus (ns, bar-CLOSE).

Pure integer arithmetic — no ``nautilus_trader`` import — so these run in every
environment, extra or not. Round-trip, epoch boundary, and non-hour resolutions
are all pinned (§A3, §5).
"""

from __future__ import annotations

import pytest

from flint.engine.nautilus import timeconv

HOUR_S = 3600
DAY_S = 86_400
MIN_S = 60


def test_ms_ns_round_trip_is_exact():
    for ms in (0, 1, 1_700_000_040_000, 999_999_999_999):
        assert timeconv.ns_to_ms(timeconv.ms_to_ns(ms)) == ms


def test_ns_to_ms_floors_sub_millisecond():
    # 1_500_000 ns = 1.5 ms -> floors to 1 ms (Flint never carries sub-ms).
    assert timeconv.ns_to_ms(1_500_000) == 1
    assert timeconv.ns_to_ms(999_999) == 0


def test_candle_start_to_bar_close_and_back_is_bar_exact():
    start_ms = 1_700_000_040_000
    close_ns = timeconv.candle_start_ms_to_bar_close_ns(start_ms, HOUR_S)
    # close = start + one hour, in ns.
    assert close_ns == timeconv.ms_to_ns(start_ms + HOUR_S * 1000)
    assert timeconv.bar_close_ns_to_candle_start_ms(close_ns, HOUR_S) == start_ms


def test_bar_at_epoch():
    # A bar whose START is unix epoch 0.
    close_ns = timeconv.candle_start_ms_to_bar_close_ns(0, HOUR_S)
    assert close_ns == HOUR_S * 1000 * timeconv.NS_PER_MS
    assert timeconv.bar_close_ns_to_candle_start_ms(close_ns, HOUR_S) == 0


@pytest.mark.parametrize("resolution_s", [1, MIN_S, 5 * MIN_S, 15 * MIN_S, HOUR_S, 4 * HOUR_S, DAY_S])
def test_round_trip_across_resolutions(resolution_s):
    start_ms = 1_700_000_040_000
    close_ns = timeconv.candle_start_ms_to_bar_close_ns(start_ms, resolution_s)
    assert timeconv.bar_close_ns_to_candle_start_ms(close_ns, resolution_s) == start_ms


def test_non_positive_resolution_rejected():
    with pytest.raises(ValueError):
        timeconv.candle_start_ms_to_bar_close_ns(0, 0)
    with pytest.raises(ValueError):
        timeconv.bar_close_ns_to_candle_start_ms(0, -1)
