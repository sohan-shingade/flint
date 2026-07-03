"""Unit tests for core/time (§5, §8.2) — bar alignment and the 'as of t' rule."""

from __future__ import annotations

import pytest

from flint.core.models import Candle
from flint.core.time import (
    MS_PER_SECOND,
    bar_end,
    bar_start,
    is_bar_closed,
    last_before,
)


def test_ms_per_second():
    assert MS_PER_SECOND == 1000


# ── bar_start / bar_end are epoch-aligned ────────────────────────────────────


@pytest.mark.parametrize(
    "ts_ms,resolution_s,expected",
    [
        (1000, 1, 1000),  # exactly on a boundary stays
        (1999, 1, 1000),  # floors down within the bar
        (2000, 1, 2000),  # next boundary
        (0, 60, 0),
        (59_999, 60, 0),  # anything in the first minute -> 0
        (60_000, 60, 60_000),  # start of the second minute
        (1_700_000_123_456, 3600, 1_700_000_123_456 - (1_700_000_123_456 % 3_600_000)),
    ],
)
def test_bar_start_alignment(ts_ms, resolution_s, expected):
    assert bar_start(ts_ms, resolution_s) == expected


def test_bar_end_is_start_plus_width():
    assert bar_end(60_000, 60) == 120_000
    assert bar_end(1000, 1) == 2000


def test_bar_start_rejects_nonpositive_resolution():
    with pytest.raises(ValueError):
        bar_start(1000, 0)
    with pytest.raises(ValueError):
        bar_end(1000, -1)


# ── is_bar_closed: closed bars only (§8.2) ───────────────────────────────────


def test_is_bar_closed():
    # bar [1000, 2000): closed once as_of reaches its end.
    assert is_bar_closed(1000, 1, as_of_ms=2000) is True
    assert is_bar_closed(1000, 1, as_of_ms=2001) is True
    assert is_bar_closed(1000, 1, as_of_ms=1999) is False  # still in progress
    assert is_bar_closed(1000, 1, as_of_ms=1000) is False


# ── last_before: STRICTLY < t (the no-look-ahead core) ───────────────────────


def test_last_before_ints_strict_inequality():
    xs = [10, 20, 30]
    assert last_before(xs, 20) == 10  # strict: 20 itself is not yet knowable at 20
    assert last_before(xs, 25) == 20
    assert last_before(xs, 31) == 30
    assert last_before(xs, 10) is None  # nothing strictly before the first
    assert last_before(xs, 5) is None


def test_last_before_empty():
    assert last_before([], 100) is None


def test_last_before_with_key_on_candles():
    candles = [
        Candle(1000, 1, 1, 1, 1, 1, "SOL-PERP", 1, "hl"),
        Candle(2000, 2, 2, 2, 2, 2, "SOL-PERP", 1, "hl"),
        Candle(3000, 3, 3, 3, 3, 3, "SOL-PERP", 1, "hl"),
    ]
    got = last_before(candles, 3000, key=lambda c: c.ts)
    assert got is not None and got.ts == 2000  # the 3000 bar is not visible at 3000
    assert last_before(candles, 2500, key=lambda c: c.ts).ts == 2000
    assert last_before(candles, 1000, key=lambda c: c.ts) is None
