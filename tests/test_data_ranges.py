"""Half-open range algebra + kinds (slice 2.2 foundation).

The DataManager's partial-range merge and funding-gate arithmetic are only as
correct as this algebra, so it gets its own unit suite: normalisation, intersect,
subtract (the gap operation the gate rejects on), union, and coverage.
"""

from __future__ import annotations

import pytest

from flint.data.ranges import Kind, RangeSet, TimeRange


# --- TimeRange -------------------------------------------------------------


def test_time_range_is_half_open_and_validated():
    assert TimeRange(0, 10).duration_ms == 10
    assert TimeRange(5, 5).is_empty
    with pytest.raises(ValueError):
        TimeRange(10, 5)


def test_time_range_overlap_and_intersect_are_half_open():
    a, b = TimeRange(0, 10), TimeRange(10, 20)
    assert not a.overlaps(b)  # touching but half-open: no overlap
    assert a.intersect(b) is None
    assert TimeRange(0, 15).intersect(TimeRange(10, 20)) == TimeRange(10, 15)


# --- RangeSet normalisation ------------------------------------------------


def test_rangeset_merges_overlapping_and_adjacent():
    # [0,10) and [10,20) are adjacent -> collapse (half-open boundary).
    assert RangeSet([TimeRange(0, 10), TimeRange(10, 20)]).ranges == (TimeRange(0, 20),)
    # Overlapping collapse too; empties are dropped.
    rs = RangeSet([TimeRange(0, 12), TimeRange(8, 20), TimeRange(30, 30)])
    assert rs.ranges == (TimeRange(0, 20),)


def test_rangeset_bounds_and_totals():
    rs = RangeSet([TimeRange(0, 10), TimeRange(20, 30)])
    assert rs.bounds() == TimeRange(0, 30)
    assert rs.total_ms == 20
    assert not rs.is_empty
    assert RangeSet().bounds() is None
    assert RangeSet().is_empty


# --- set operations --------------------------------------------------------


def test_intersect_keeps_only_common_span():
    a = RangeSet([TimeRange(0, 20), TimeRange(40, 60)])
    b = RangeSet([TimeRange(10, 50)])
    assert a.intersect(b) == RangeSet([TimeRange(10, 20), TimeRange(40, 50)])


def test_subtract_yields_the_gaps():
    # This is the gate operation: requested - covered = missing ranges.
    requested = RangeSet([TimeRange(0, 30)])
    covered = RangeSet([TimeRange(10, 20)])
    assert requested.subtract(covered) == RangeSet([TimeRange(0, 10), TimeRange(20, 30)])
    # Fully covered -> no gap.
    assert RangeSet([TimeRange(0, 30)]).subtract(RangeSet([TimeRange(0, 30)])).is_empty


def test_union_and_covers():
    a = RangeSet([TimeRange(0, 15)])
    b = RangeSet([TimeRange(10, 30)])
    assert a.union(b) == RangeSet([TimeRange(0, 30)])
    assert a.union(b).covers(TimeRange(5, 25))
    assert not a.covers(TimeRange(5, 25))


# --- Kind gate policy ------------------------------------------------------


def test_kind_gate_policy():
    assert Kind.FUNDING.is_hard_required and not Kind.FUNDING.is_degradable
    assert Kind.DEPTH.is_degradable and not Kind.DEPTH.is_hard_required
    assert not Kind.CANDLES.is_hard_required and not Kind.CANDLES.is_degradable
    assert not Kind.OI.is_hard_required
