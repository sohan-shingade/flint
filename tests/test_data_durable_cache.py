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


# --- D1: per-kind dedupe keys, ledger coverage, hour partitions, migration ---


def _funding_typed(rows: list[tuple[int, str, int | None]]) -> pa.Table:
    """Full-schema funding rows: (ts, rate_type, settlement_ts) hand-authored."""
    from flint.data.normalize import FUNDING_SCHEMA

    return pa.Table.from_pylist(
        [
            {
                "ts": ts,
                "rate_hourly": 0.0001,
                "interval_s": 3600,
                "price_basis": "oracle",
                "rate_type": rate_type,
                "venue": "hyperliquid",
                "market": "SOL-PERP",
                "settlement_ts": settlement_ts,
            }
            for ts, rate_type, settlement_ts in rows
        ],
        schema=FUNDING_SCHEMA,
    )


def _trades(rows: list[tuple[int, float, float, str, int]]) -> pa.Table:
    """Hand-authored trade prints: (ts, price, size, side, trade_id)."""
    from flint.data.normalize import TRADES_SCHEMA

    return pa.Table.from_pylist(
        [
            {
                "ts": ts,
                "market": "SOL-PERP",
                "venue": "hyperliquid",
                "price": price,
                "size": size,
                "side": side,
                "trade_id": trade_id,
            }
            for ts, price, size, side, trade_id in rows
        ],
        schema=TRADES_SCHEMA,
    )


def test_predicted_and_final_funding_at_same_ts_both_survive(tmp_path):
    # The settlement instant carries BOTH the last predicted row and the final
    # row at the same ts — deduping by ts alone silently dropped one (§B2 fix).
    cache = DurableCacheSource(tmp_path)
    ts = _ms(2025, 1, 1, 1)
    table = _funding_typed([(ts, "predicted", None), (ts, "final", ts)])
    cache.store("hyperliquid", "SOL-PERP", Kind.FUNDING, table)
    cache.store("hyperliquid", "SOL-PERP", Kind.FUNDING, table)  # idempotent re-run

    got = cache.fetch("hyperliquid", "SOL-PERP", Kind.FUNDING, TimeRange(ts, ts + 1))
    assert got.num_rows == 2
    assert sorted(got.column("rate_type").to_pylist()) == ["final", "predicted"]
    by_type = dict(
        zip(got.column("rate_type").to_pylist(), got.column("settlement_ts").to_pylist())
    )
    assert by_type == {"predicted": None, "final": ts}


def test_same_ts_trades_with_distinct_ids_both_survive(tmp_path):
    cache = DurableCacheSource(tmp_path)
    ts = _ms(2025, 1, 1, 13)
    table = _trades([(ts, 211.5, 1.0, "buy", 101), (ts, 211.5, 0.5, "sell", 102)])
    cache.store("hyperliquid", "SOL-PERP", Kind.TRADES, table)
    cache.store("hyperliquid", "SOL-PERP", Kind.TRADES, table)

    got = cache.fetch("hyperliquid", "SOL-PERP", Kind.TRADES, TimeRange(ts, ts + 1))
    assert got.num_rows == 2
    assert sorted(got.column("trade_id").to_pylist()) == [101, 102]


def test_tick_rows_without_ledger_contribute_no_coverage(tmp_path):
    # Honesty rule: a quiet market and a capture gap are indistinguishable at
    # the row level, so tick-scale coverage must be asserted, never inferred.
    from flint.data.store.coverage import CoverageLedger

    cache = DurableCacheSource(tmp_path)
    ts = _ms(2025, 1, 1, 13)
    cache.store(
        "hyperliquid", "SOL-PERP", Kind.TRADES, _trades([(ts, 211.5, 1.0, "buy", 101)])
    )
    want = TimeRange(_ms(2025, 1, 1), _ms(2025, 1, 2))
    assert cache.available("hyperliquid", "SOL-PERP", Kind.TRADES, want).is_empty
    # The rows themselves are still fetchable — only *coverage* is withheld.
    assert cache.fetch("hyperliquid", "SOL-PERP", Kind.TRADES, want).num_rows == 1

    # Once the ingester asserts the captured span, coverage appears verbatim.
    ledger = CoverageLedger(tmp_path / "trades" / "hyperliquid" / "SOL-PERP")
    span = TimeRange(_ms(2025, 1, 1, 13), _ms(2025, 1, 1, 14))
    ledger.assert_covered(span, "recorder")
    avail = cache.available("hyperliquid", "SOL-PERP", Kind.TRADES, want)
    assert avail.ranges == (span,)


def test_candle_envelope_fallback_seeds_a_ledger_once(tmp_path):
    # Non-tick kinds keep the row-envelope behavior verbatim; the first
    # available() touch persists it as an asserted ledger (source hl_rest) and
    # later write-throughs extend that ledger.
    import json

    cache = DurableCacheSource(tmp_path)
    ts = [_ms(2025, 1, 1), _ms(2025, 1, 2)]
    cache.store("hyperliquid", "SOL-PERP", Kind.FUNDING, _funding(ts))

    want = TimeRange(0, _ms(2025, 2, 1))
    avail = cache.available("hyperliquid", "SOL-PERP", Kind.FUNDING, want)
    assert avail.ranges == (TimeRange(ts[0], ts[1] + 1),)  # unchanged envelope

    ledger_path = tmp_path / "funding" / "hyperliquid" / "SOL-PERP" / "_coverage.json"
    payload = json.loads(ledger_path.read_text())
    assert [(r["start_ms"], r["end_ms"], r["source"]) for r in payload["ranges"]] == [
        (ts[0], ts[1] + 1, "hl_rest")
    ]

    # A later write-through keeps the (now authoritative) ledger current.
    later = _ms(2025, 1, 5)
    cache.store("hyperliquid", "SOL-PERP", Kind.FUNDING, _funding([later]))
    avail = cache.available("hyperliquid", "SOL-PERP", Kind.FUNDING, want)
    assert avail.covers(TimeRange(later, later + 1))
    assert avail.covers(TimeRange(ts[0], ts[1] + 1))


def test_tick_kinds_are_hour_partitioned(tmp_path):
    from flint.data.normalize import BOOK_DELTA_SCHEMA, QUOTES_SCHEMA

    cache = DurableCacheSource(tmp_path)
    ts13, ts14 = _ms(2025, 1, 1, 13), _ms(2025, 1, 1, 14)

    cache.store(
        "hyperliquid",
        "SOL-PERP",
        Kind.TRADES,
        _trades([(ts13, 211.5, 1.0, "buy", 101), (ts14, 211.6, 2.0, "sell", 102)]),
    )
    base = tmp_path / "trades" / "hyperliquid" / "SOL-PERP" / "2025-01-01"
    assert (base / "13" / "part.parquet").exists()
    assert (base / "14" / "part.parquet").exists()
    got = cache.fetch(
        "hyperliquid", "SOL-PERP", Kind.TRADES, TimeRange(ts13, ts14 + 1)
    )
    assert got.column("trade_id").to_pylist() == [101, 102]

    quotes = pa.Table.from_pylist(
        [
            {
                "ts": ts13,
                "market": "SOL-PERP",
                "venue": "hyperliquid",
                "bid_px": 211.4,
                "bid_sz": 10.0,
                "ask_px": 211.6,
                "ask_sz": 8.0,
            }
        ],
        schema=QUOTES_SCHEMA,
    )
    cache.store("hyperliquid", "SOL-PERP", Kind.QUOTES, quotes)
    assert (
        tmp_path / "quotes" / "hyperliquid" / "SOL-PERP" / "2025-01-01" / "13"
        / "part.parquet"
    ).exists()

    deltas = pa.Table.from_pylist(
        [
            {
                "ts": ts13,
                "local_ts": ts13 + 2,
                "seq": 7,
                "market": "SOL-PERP",
                "venue": "hyperliquid",
                "side": "bid",
                "px": 211.4,
                "sz": 0.0,  # deletes the level
                "is_snapshot": False,
            }
        ],
        schema=BOOK_DELTA_SCHEMA,
    )
    cache.store("hyperliquid", "SOL-PERP", Kind.BOOK_DELTA, deltas)
    assert (
        tmp_path / "book_delta" / "hyperliquid" / "SOL-PERP" / "2025-01-01" / "13"
        / "part.parquet"
    ).exists()
    got = cache.fetch(
        "hyperliquid", "SOL-PERP", Kind.BOOK_DELTA, TimeRange(ts13, ts13 + 1)
    )
    assert got.column("seq").to_pylist() == [7]


def test_lake_v1_funding_file_gains_settlement_ts_on_read(tmp_path):
    # Upgrade-on-read (§9.0): a v1 file (no settlement_ts) is promoted in
    # memory — settled rows settle at their own ts, predicted rows stay null.
    from flint.data.store.layout import write_parquet

    ts_final, ts_pred = _ms(2025, 1, 1, 1), _ms(2025, 1, 1, 2)
    v1 = pa.table(
        {
            "ts": [ts_final, ts_pred],
            "rate_hourly": [0.0001, 0.0002],
            "interval_s": [3600, 3600],
            "price_basis": ["oracle", "oracle"],
            "rate_type": ["final", "predicted"],
            "venue": ["hyperliquid", "hyperliquid"],
            "market": ["SOL-PERP", "SOL-PERP"],
        }
    )
    part_dir = tmp_path / "funding" / "hyperliquid" / "SOL-PERP" / "2025-01-01"
    part_dir.mkdir(parents=True)
    write_parquet(v1, str(part_dir / "part.parquet"), schema_version=1)

    got = DurableCacheSource(tmp_path).fetch(
        "hyperliquid", "SOL-PERP", Kind.FUNDING, TimeRange(ts_final, ts_pred + 1)
    )
    assert got.column_names[-1] == "settlement_ts"
    by_type = dict(
        zip(got.column("rate_type").to_pylist(), got.column("settlement_ts").to_pylist())
    )
    assert by_type == {"final": ts_final, "predicted": None}


def test_lake_v1_non_funding_file_passes_through_unchanged(tmp_path):
    from flint.data.store.layout import write_parquet

    ts = _ms(2025, 1, 1)
    v1 = pa.table(
        {
            "ts": [ts],
            "open": [210.0],
            "high": [212.0],
            "low": [209.5],
            "close": [211.0],
        }
    )
    part_dir = tmp_path / "candles" / "hyperliquid" / "SOL-PERP" / "2025-01-01"
    part_dir.mkdir(parents=True)
    write_parquet(v1, str(part_dir / "part.parquet"), schema_version=1)

    got = DurableCacheSource(tmp_path).fetch(
        "hyperliquid", "SOL-PERP", Kind.CANDLES, TimeRange(ts, ts + 1)
    )
    assert got.column_names == ["ts", "open", "high", "low", "close"]
    assert got.column("close").to_pylist() == [211.0]
