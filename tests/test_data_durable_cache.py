"""Durable Parquet-backed cache tier (2.7, §9.0).

Exercises the write-through contract, honest on-disk coverage, half-open reads,
idempotent re-writes, cross-day partitioning, and reindex-on-reopen. No network,
no fabricated market data (D26).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pyarrow as pa

from flint.data import Kind, TimeRange
from flint.data.store import DurableCacheSource


def _ms(y, m, d, h=0) -> int:
    return int(datetime(y, m, d, h, tzinfo=UTC).timestamp() * 1000)


def _funding(ts_values: list[int]) -> pa.Table:
    return pa.table(
        {
            "ts": ts_values,
            "rate_hourly": [0.0001] * len(ts_values),
            "venue": ["hyperliquid"] * len(ts_values),
            "market": ["SOL-PERP"] * len(ts_values),
        }
    )


def test_store_then_fetch_round_trips_through_parquet(tmp_path):
    cache = DurableCacheSource(tmp_path)
    ts = [_ms(2025, 1, 1, 0), _ms(2025, 1, 1, 1), _ms(2025, 1, 1, 2)]
    cache.store("hyperliquid", "SOL-PERP", Kind.FUNDING, _funding(ts))

    got = cache.fetch(
        "hyperliquid", "SOL-PERP", Kind.FUNDING, TimeRange(ts[0], ts[2] + 1)
    )
    assert got.column("ts").to_pylist() == ts
    # A parquet file was actually written under the kind/venue/market partition.
    assert list(tmp_path.rglob("part.parquet"))


def test_coverage_is_the_honest_on_disk_envelope(tmp_path):
    cache = DurableCacheSource(tmp_path)
    ts = [_ms(2025, 1, 1), _ms(2025, 1, 2)]
    cache.store("hyperliquid", "SOL-PERP", Kind.FUNDING, _funding(ts))
    avail = cache.available(
        "hyperliquid", "SOL-PERP", Kind.FUNDING, TimeRange(0, _ms(2025, 2, 1))
    )
    assert avail.ranges == (TimeRange(ts[0], ts[1] + 1),)
    # A market never written has no coverage.
    assert cache.available(
        "hyperliquid", "BTC-PERP", Kind.FUNDING, TimeRange(0, _ms(2025, 2, 1))
    ).is_empty


def test_fetch_is_half_open_and_spans_day_partitions(tmp_path):
    cache = DurableCacheSource(tmp_path)
    ts = [_ms(2025, 1, 1), _ms(2025, 1, 2), _ms(2025, 1, 3)]
    cache.store("hyperliquid", "SOL-PERP", Kind.FUNDING, _funding(ts))
    # [day1, day3) excludes the day-3 row.
    got = cache.fetch(
        "hyperliquid", "SOL-PERP", Kind.FUNDING, TimeRange(ts[0], ts[2])
    )
    assert got.column("ts").to_pylist() == [ts[0], ts[1]]


def test_store_is_idempotent_by_ts(tmp_path):
    cache = DurableCacheSource(tmp_path)
    ts = [_ms(2025, 1, 1), _ms(2025, 1, 1, 1)]
    cache.store("hyperliquid", "SOL-PERP", Kind.FUNDING, _funding(ts))
    cache.store("hyperliquid", "SOL-PERP", Kind.FUNDING, _funding(ts))  # re-run
    got = cache.fetch(
        "hyperliquid", "SOL-PERP", Kind.FUNDING, TimeRange(ts[0], ts[1] + 1)
    )
    assert got.column("ts").to_pylist() == ts  # no duplicates


def test_reopen_reindexes_held_ranges_from_disk(tmp_path):
    ts = [_ms(2025, 1, 1), _ms(2025, 1, 2)]
    DurableCacheSource(tmp_path).store(
        "hyperliquid", "SOL-PERP", Kind.FUNDING, _funding(ts)
    )
    # A fresh instance over the same root knows its coverage without a re-write.
    reopened = DurableCacheSource(tmp_path)
    avail = reopened.available(
        "hyperliquid", "SOL-PERP", Kind.FUNDING, TimeRange(0, _ms(2025, 2, 1))
    )
    assert avail.ranges == (TimeRange(ts[0], ts[1] + 1),)
    assert reopened.fetch(
        "hyperliquid", "SOL-PERP", Kind.FUNDING, TimeRange(ts[0], ts[1] + 1)
    ).num_rows == 2
