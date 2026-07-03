"""CoverageLedger — ingester-asserted coverage per store directory (D1, §9.0).

Round-trip, provenance, variant slot, removal (eviction never lies), and
atomic-write hygiene. Ranges are hand-authored unix-ms values (D26).
"""

from __future__ import annotations

import json

import pytest

from flint.data.ranges import RangeSet, TimeRange
from flint.data.store.coverage import LEDGER_FILENAME, CoverageEntry, CoverageLedger

_H = 3_600_000  # one hour in ms
_DAY0 = 1_735_689_600_000  # 2025-01-01T00:00:00Z


def test_load_returns_none_until_something_is_asserted(tmp_path):
    assert CoverageLedger.load(tmp_path) is None
    ledger = CoverageLedger(tmp_path)
    assert not ledger.exists
    assert ledger.covered() == RangeSet()


def test_assert_covered_round_trips_through_disk(tmp_path):
    ledger = CoverageLedger(tmp_path)
    ledger.assert_covered(TimeRange(_DAY0, _DAY0 + 24 * _H), "tardis", written_ts=1)
    ledger.assert_covered(
        TimeRange(_DAY0 + 24 * _H, _DAY0 + 30 * _H), "recorder", written_ts=2
    )

    reopened = CoverageLedger.load(tmp_path)
    assert reopened is not None
    # Adjacent asserted ranges fold into one covered span...
    assert reopened.covered() == RangeSet((TimeRange(_DAY0, _DAY0 + 30 * _H),))
    # ...while per-range provenance survives un-merged.
    assert reopened.entries == (
        CoverageEntry(_DAY0, _DAY0 + 24 * _H, "tardis", 1),
        CoverageEntry(_DAY0 + 24 * _H, _DAY0 + 30 * _H, "recorder", 2),
    )


def test_covered_range_may_hold_zero_rows(tmp_path):
    # A quiet market is data, not a gap: asserting a span is valid even though
    # no rows exist anywhere. Coverage is the ingester's statement, not a row count.
    ledger = CoverageLedger(tmp_path)
    ledger.assert_covered(TimeRange(_DAY0, _DAY0 + _H), "recorder")
    assert ledger.covered().covers(TimeRange(_DAY0, _DAY0 + _H))


def test_remove_punches_holes_and_keeps_provenance(tmp_path):
    ledger = CoverageLedger(tmp_path)
    ledger.assert_covered(TimeRange(_DAY0, _DAY0 + 10 * _H), "tardis", written_ts=7)
    ledger.remove(TimeRange(_DAY0 + 4 * _H, _DAY0 + 6 * _H))

    reopened = CoverageLedger.load(tmp_path)
    assert reopened is not None
    assert reopened.covered() == RangeSet(
        (
            TimeRange(_DAY0, _DAY0 + 4 * _H),
            TimeRange(_DAY0 + 6 * _H, _DAY0 + 10 * _H),
        )
    )
    # Both surviving pieces still say who asserted them and when.
    assert {(e.source, e.written_ts) for e in reopened.entries} == {("tardis", 7)}


def test_variant_slot_partitions_coverage(tmp_path):
    # Schema-only slot for future candle-resolution variants: entries carry it,
    # covered() filters on it, and the default "" is what everything uses today.
    ledger = CoverageLedger(tmp_path)
    ledger.assert_covered(TimeRange(0, 10), "hl_rest", variant="")
    ledger.assert_covered(TimeRange(20, 30), "hl_rest", variant="1m")
    assert ledger.covered() == RangeSet((TimeRange(0, 10),))
    assert ledger.covered(variant="1m") == RangeSet((TimeRange(20, 30),))
    payload = json.loads((tmp_path / LEDGER_FILENAME).read_text())
    assert {r["variant"] for r in payload["ranges"]} == {"", "1m"}


def test_unknown_source_is_rejected_and_empty_span_ignored(tmp_path):
    ledger = CoverageLedger(tmp_path)
    with pytest.raises(ValueError):
        ledger.assert_covered(TimeRange(0, 10), "vibes")
    ledger.assert_covered(TimeRange(5, 5), "recorder")  # zero-width: a no-op
    assert not (tmp_path / LEDGER_FILENAME).exists()


def test_writes_are_atomic_replacements(tmp_path):
    ledger = CoverageLedger(tmp_path)
    ledger.assert_covered(TimeRange(0, 10), "recorder", written_ts=1)
    ledger.assert_covered(TimeRange(10, 20), "recorder", written_ts=2)
    # Only the ledger file remains — the temp file was renamed over it, never
    # left behind, and the on-disk content is always complete valid JSON.
    assert [p.name for p in tmp_path.iterdir()] == [LEDGER_FILENAME]
    payload = json.loads((tmp_path / LEDGER_FILENAME).read_text())
    assert len(payload["ranges"]) == 2
    assert payload["schema_version"] == 1
