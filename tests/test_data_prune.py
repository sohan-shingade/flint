"""D5 storage retention + ``flint data prune`` (§B2/§B9).

The invariant under test: **eviction never lies about coverage**. Pruning a
range deletes its partitions AND retracts it from the CoverageLedger in the
same pass, so ``available()`` shrinks to exactly what is still served. All
rows are hand-authored unit inputs (D26).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pyarrow as pa

from flint.data import Kind, TimeRange
from flint.data.store import DurableCacheSource
from flint.data.store.prune import (
    DEFAULT_RETENTION_DAYS,
    prune,
    retention_boundary_ms,
)

DAY_MS = 86_400_000
HOUR_MS = 3_600_000


def _ms(y, m, d, h=0) -> int:
    return int(datetime(y, m, d, h, tzinfo=UTC).timestamp() * 1000)


def _book_deltas(ts_values: list[int]) -> pa.Table:
    from flint.data.normalize import BOOK_DELTA_SCHEMA

    return pa.Table.from_pylist(
        [
            {
                "ts": ts,
                "local_ts": ts,
                "seq": i,
                "market": "SOL-PERP",
                "venue": "hyperliquid",
                "side": "bid",
                "px": 211.4,
                "sz": 10.0,
                "is_snapshot": False,
            }
            for i, ts in enumerate(ts_values)
        ],
        schema=BOOK_DELTA_SCHEMA,
    )


def _candles(ts_values: list[int]) -> pa.Table:
    return pa.table(
        {
            "ts": ts_values,
            "open": [211.0] * len(ts_values),
            "close": [211.5] * len(ts_values),
            "venue": ["hyperliquid"] * len(ts_values),
            "market": ["SOL-PERP"] * len(ts_values),
        }
    )


def _seeded_book_cache(tmp_path) -> DurableCacheSource:
    """BOOK_DELTA rows + asserted day coverage on 2025-01-01 and 2025-01-10."""
    cache = DurableCacheSource(tmp_path)
    old_day, new_day = _ms(2025, 1, 1), _ms(2025, 1, 10)
    cache.store(
        "hyperliquid",
        "SOL-PERP",
        Kind.BOOK_DELTA,
        _book_deltas([old_day + 12 * HOUR_MS, new_day + 12 * HOUR_MS]),
    )
    ledger = cache.coverage_ledger("hyperliquid", "SOL-PERP", Kind.BOOK_DELTA)
    ledger.assert_covered(TimeRange(old_day, old_day + DAY_MS), "tardis")
    ledger.assert_covered(TimeRange(new_day, new_day + DAY_MS), "tardis")
    return cache


def test_prune_deletes_expired_partitions_and_coverage_shrinks(tmp_path):
    _seeded_book_cache(tmp_path)
    now = _ms(2025, 2, 1)
    boundary = retention_boundary_ms(Kind.BOOK_DELTA, 25, now)
    assert boundary == _ms(2025, 1, 7)  # 25 days back, hour-floored

    report = prune(
        tmp_path, retention_days={Kind.BOOK_DELTA: 25}, now_ms=now, dry_run=False
    )

    old_part = (
        tmp_path / "book_delta" / "hyperliquid" / "SOL-PERP" / "2025-01-01"
    )
    new_part = (
        tmp_path
        / "book_delta"
        / "hyperliquid"
        / "SOL-PERP"
        / "2025-01-10"
        / "12"
        / "part.parquet"
    )
    assert not old_part.exists()  # expired partition (and its empty dirs) gone
    assert new_part.exists()  # retained partition untouched
    assert len(report.partitions) == 1
    assert report.partitions[0].path.startswith("book_delta/")
    assert report.bytes_reclaimed > 0

    # THE invariant: available() shrinks to exactly what is still on disk —
    # the pruned day is no longer advertised, the retained day still is.
    reopened = DurableCacheSource(tmp_path)
    want = TimeRange(0, _ms(2025, 2, 1))
    avail = reopened.available("hyperliquid", "SOL-PERP", Kind.BOOK_DELTA, want)
    assert avail.ranges == (TimeRange(_ms(2025, 1, 10), _ms(2025, 1, 11)),)
    # The ledger survives (provenance intact), trimmed to the retained range.
    assert (
        tmp_path / "book_delta" / "hyperliquid" / "SOL-PERP" / "_coverage.json"
    ).exists()


def test_prune_dry_run_touches_nothing_and_prints_the_plan(tmp_path):
    _seeded_book_cache(tmp_path)
    now = _ms(2025, 2, 1)

    report = prune(
        tmp_path, retention_days={Kind.BOOK_DELTA: 25}, now_ms=now, dry_run=True
    )

    # The plan is complete: the doomed partition and the exact coverage ranges.
    assert report.dry_run and len(report.partitions) == 1
    assert report.coverage[0].removed == (
        TimeRange(_ms(2025, 1, 1), _ms(2025, 1, 2)),
    )
    # ...but disk and coverage are untouched.
    assert (
        tmp_path
        / "book_delta"
        / "hyperliquid"
        / "SOL-PERP"
        / "2025-01-01"
        / "12"
        / "part.parquet"
    ).exists()
    avail = DurableCacheSource(tmp_path).available(
        "hyperliquid", "SOL-PERP", Kind.BOOK_DELTA, TimeRange(0, now)
    )
    assert avail.covers(TimeRange(_ms(2025, 1, 1), _ms(2025, 1, 2)))


def test_prune_retracts_zero_row_covered_days_too(tmp_path):
    # A covered day with no rows on disk (quiet market) is real coverage — and
    # retention retracts the *guarantee*, so it must go with its window.
    cache = DurableCacheSource(tmp_path)
    quiet_day, kept_day = _ms(2025, 1, 2), _ms(2025, 1, 10)
    cache.store(
        "hyperliquid",
        "SOL-PERP",
        Kind.BOOK_DELTA,
        _book_deltas([kept_day + HOUR_MS]),
    )
    ledger = cache.coverage_ledger("hyperliquid", "SOL-PERP", Kind.BOOK_DELTA)
    ledger.assert_covered(TimeRange(quiet_day, quiet_day + DAY_MS), "recorder")
    ledger.assert_covered(TimeRange(kept_day, kept_day + DAY_MS), "recorder")

    prune(
        tmp_path,
        retention_days={Kind.BOOK_DELTA: 25},
        now_ms=_ms(2025, 2, 1),
        dry_run=False,
    )

    avail = DurableCacheSource(tmp_path).available(
        "hyperliquid", "SOL-PERP", Kind.BOOK_DELTA, TimeRange(0, _ms(2025, 2, 1))
    )
    assert avail.ranges == (TimeRange(kept_day, kept_day + DAY_MS),)


def test_default_retention_keeps_candles_and_funding_forever(tmp_path):
    cache = DurableCacheSource(tmp_path)
    ancient = _ms(2020, 1, 1)
    cache.store("hyperliquid", "SOL-PERP", Kind.CANDLES, _candles([ancient]))
    # Seed the ledger (available() envelope-seeding is the one-shot migration).
    cache.available(
        "hyperliquid", "SOL-PERP", Kind.CANDLES, TimeRange(0, _ms(2026, 1, 1))
    )

    report = prune(tmp_path, now_ms=_ms(2026, 1, 1), dry_run=False)

    assert report.partitions == ()  # nothing expired under defaults
    assert DurableCacheSource(tmp_path).available(
        "hyperliquid", "SOL-PERP", Kind.CANDLES, TimeRange(0, _ms(2026, 1, 1))
    ).covers(TimeRange(ancient, ancient + 1))
    assert DEFAULT_RETENTION_DAYS[Kind.CANDLES] is None
    assert DEFAULT_RETENTION_DAYS[Kind.FUNDING] is None


def test_cli_data_prune_dry_run_emits_the_plan(tmp_path):
    from flint.sdk.cli import build_parser, cmd_data_prune

    _seeded_book_cache(tmp_path)
    args = build_parser().parse_args(
        [
            "data",
            "prune",
            "--cache-root",
            str(tmp_path),
            "--retention",
            "book_delta=25",
            "--dry-run",
        ]
    )
    lines: list[str] = []
    assert cmd_data_prune(args, out=lines.append) == 0

    payload = json.loads("\n".join(lines))
    assert payload["dry_run"] is True
    # now_ms is wall clock here; 2025 fixtures are long expired under 25 days.
    assert payload["partitions_deleted"] == 2
    assert payload["coverage_removed"][0]["kind"] == "book_delta"
    # Dry run: nothing was deleted.
    assert (
        tmp_path
        / "book_delta"
        / "hyperliquid"
        / "SOL-PERP"
        / "2025-01-01"
        / "12"
        / "part.parquet"
    ).exists()


def test_cli_data_prune_retention_forever_disables_a_default(tmp_path):
    from flint.sdk.cli import build_parser, cmd_data_prune

    _seeded_book_cache(tmp_path)
    args = build_parser().parse_args(
        [
            "data",
            "prune",
            "--cache-root",
            str(tmp_path),
            "--retention",
            "book_delta=forever",
        ]
    )
    lines: list[str] = []
    assert cmd_data_prune(args, out=lines.append) == 0
    payload = json.loads("\n".join(lines))
    assert payload["partitions_deleted"] == 0
