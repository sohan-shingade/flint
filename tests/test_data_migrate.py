"""Legacy Flint v1.x DuckDB -> new store one-shot importer (2.8, §19.6).

The fixture DuckDB is built in-test using the *old* schema (recovered from
bf2d385:flint/store/schema.py) and populated with a handful of hand-authored
unit rows — never generated market-like data (D26). The tests assert that the
importer:

* opens the legacy file strictly read-only (a write attempt raises),
* lifts each of the four now-or-never market-data kinds into the new Arrow
  schemas the engine reads, row-for-row and value-for-value,
* reports honest per-kind row counts for verification,
* is idempotent (a re-run is a no-op thanks to the sink's upsert),
* leaves absent tables and run-metadata tables handled without moving them,
* preserves the legacy file byte-for-byte.
"""

from __future__ import annotations

from datetime import UTC, datetime

import duckdb
import pytest

from flint.data import (
    InMemoryCacheSource,
    Kind,
    MigrationReport,
    TimeRange,
    migrate_legacy_duckdb,
)

# --- old schema (recovered from bf2d385:flint/store/schema.py) ---------------

_LEGACY_DDL = [
    """CREATE TABLE candles (
        market VARCHAR NOT NULL, resolution_s INTEGER NOT NULL, ts BIGINT NOT NULL,
        open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE,
        venue VARCHAR NOT NULL DEFAULT 'pyth',
        PRIMARY KEY (venue, market, resolution_s, ts))""",
    """CREATE TABLE venue_funding_rates (
        venue VARCHAR NOT NULL, market VARCHAR NOT NULL, ts BIGINT NOT NULL,
        rate_hourly DOUBLE NOT NULL, mark_price DOUBLE NOT NULL DEFAULT 0,
        index_price DOUBLE NOT NULL DEFAULT 0,
        PRIMARY KEY (venue, market, ts))""",
    """CREATE TABLE orderbook_snapshots (
        venue VARCHAR NOT NULL DEFAULT 'pyth', market VARCHAR NOT NULL,
        ts BIGINT NOT NULL, bid_prices DOUBLE[], bid_sizes DOUBLE[],
        ask_prices DOUBLE[], ask_sizes DOUBLE[],
        PRIMARY KEY (venue, market, ts))""",
    """CREATE TABLE open_interest (
        venue VARCHAR NOT NULL DEFAULT 'drift', market VARCHAR NOT NULL,
        ts BIGINT NOT NULL, long_oi DOUBLE NOT NULL, short_oi DOUBLE NOT NULL,
        PRIMARY KEY (venue, market, ts))""",
    """CREATE TABLE journal_equity (
        run_id VARCHAR NOT NULL, ts BIGINT NOT NULL, equity DOUBLE NOT NULL,
        PRIMARY KEY (run_id, ts))""",
]


def _ms(y, m, d, h=0) -> int:
    return int(datetime(y, m, d, h, tzinfo=UTC).timestamp() * 1000)


def _legacy_db(path, *, with_run_meta: bool = True) -> None:
    """Build a legacy-schema DuckDB with hand-authored unit rows (D26)."""
    con = duckdb.connect(str(path))
    try:
        for ddl in _LEGACY_DDL:
            con.execute(ddl)
        # Two candle series (distinct venue/market) to exercise grouping.
        con.executemany(
            "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ("SOL-PERP", 60, _ms(2025, 1, 1, 0), 100.0, 101.0, 99.0, 100.5, 12.0, "hyperliquid"),
                ("SOL-PERP", 60, _ms(2025, 1, 1, 1), 100.5, 102.0, 100.0, 101.0, 8.0, "hyperliquid"),
                ("BTC-PERP", 60, _ms(2025, 1, 1, 0), 90000.0, 90100.0, 89900.0, 90050.0, 3.0, "hyperliquid"),
            ],
        )
        con.executemany(
            "INSERT INTO venue_funding_rates VALUES (?,?,?,?,?,?)",
            [
                ("hyperliquid", "SOL-PERP", _ms(2025, 1, 1, 0), 0.0000125, 100.5, 100.4),
                ("hyperliquid", "SOL-PERP", _ms(2025, 1, 1, 1), 0.0000130, 101.0, 100.9),
            ],
        )
        con.executemany(
            "INSERT INTO orderbook_snapshots VALUES (?,?,?,?,?,?,?)",
            [
                (
                    "hyperliquid", "SOL-PERP", _ms(2025, 1, 1, 0),
                    [100.4, 100.3], [5.0, 7.0], [100.6, 100.7], [4.0, 6.0],
                ),
            ],
        )
        con.executemany(
            "INSERT INTO open_interest VALUES (?,?,?,?,?)",
            [
                ("hyperliquid", "SOL-PERP", _ms(2025, 1, 1, 0), 1_000.0, 1_000.0),
                ("hyperliquid", "SOL-PERP", _ms(2025, 1, 1, 1), 1_050.0, 1_050.0),
            ],
        )
        if with_run_meta:
            con.executemany(
                "INSERT INTO journal_equity VALUES (?,?,?)",
                [("run-a", _ms(2025, 1, 1, 0), 10_000.0), ("run-a", _ms(2025, 1, 1, 1), 10_050.0)],
            )
    finally:
        con.close()


@pytest.fixture()
def legacy_path(tmp_path):
    path = tmp_path / "legacy.duckdb"
    _legacy_db(path)
    return path


# --- tests ------------------------------------------------------------------


def test_candles_import_row_for_row(legacy_path):
    sink = InMemoryCacheSource()
    migrate_legacy_duckdb(legacy_path, sink)

    got = sink.fetch(
        "hyperliquid", "SOL-PERP", Kind.CANDLES, TimeRange(0, _ms(2025, 2, 1))
    )
    assert got.column("ts").to_pylist() == [_ms(2025, 1, 1, 0), _ms(2025, 1, 1, 1)]
    assert got.column("close").to_pylist() == [100.5, 101.0]
    assert got.column("resolution_s").to_pylist() == [60, 60]
    assert set(got.column("venue").to_pylist()) == {"hyperliquid"}
    # The second candle series landed under its own venue/market partition.
    btc = sink.fetch(
        "hyperliquid", "BTC-PERP", Kind.CANDLES, TimeRange(0, _ms(2025, 2, 1))
    )
    assert btc.column("close").to_pylist() == [90050.0]


def test_funding_carries_rate_and_marks_provenance(legacy_path):
    sink = InMemoryCacheSource()
    migrate_legacy_duckdb(legacy_path, sink)

    got = sink.fetch(
        "hyperliquid", "SOL-PERP", Kind.FUNDING, TimeRange(0, _ms(2025, 2, 1))
    )
    assert got.column("rate_hourly").to_pylist() == [0.0000125, 0.0000130]
    # Fields the legacy schema never recorded are marked, not invented.
    assert set(got.column("price_basis").to_pylist()) == {"legacy"}
    assert set(got.column("rate_type").to_pylist()) == {"legacy"}
    assert set(got.column("interval_s").to_pylist()) == {0}
    # Legacy mark/index have no funding-schema home and were dropped.
    assert "mark_price" not in got.column_names


def test_depth_rezips_parallel_arrays_into_level_pairs(legacy_path):
    sink = InMemoryCacheSource()
    migrate_legacy_duckdb(legacy_path, sink)

    got = sink.fetch(
        "hyperliquid", "SOL-PERP", Kind.DEPTH, TimeRange(0, _ms(2025, 2, 1))
    )
    assert got.num_rows == 1
    assert got.column("bids").to_pylist() == [[[100.4, 5.0], [100.3, 7.0]]]
    assert got.column("asks").to_pylist() == [[[100.6, 4.0], [100.7, 6.0]]]


def test_oi_uses_long_side_and_nulls_unrecorded_prices(legacy_path):
    sink = InMemoryCacheSource()
    migrate_legacy_duckdb(legacy_path, sink)

    got = sink.fetch(
        "hyperliquid", "SOL-PERP", Kind.OI, TimeRange(0, _ms(2025, 2, 1))
    )
    assert got.column("oi").to_pylist() == [1_000.0, 1_050.0]
    # mark/index/funding were never recorded on the legacy OI row -> null, not 0.
    assert got.column("mark_price").to_pylist() == [None, None]
    assert got.column("index_price").to_pylist() == [None, None]
    assert got.column("funding_hourly").to_pylist() == [None, None]


def test_report_counts_and_run_metadata_inventory(legacy_path):
    sink = InMemoryCacheSource()
    report = migrate_legacy_duckdb(legacy_path, sink)

    assert isinstance(report, MigrationReport)
    assert report.rows_by_kind[Kind.CANDLES] == 3
    assert report.rows_by_kind[Kind.FUNDING] == 2
    assert report.rows_by_kind[Kind.DEPTH] == 1
    assert report.rows_by_kind[Kind.OI] == 2
    assert report.groups_by_kind[Kind.CANDLES] == 2  # SOL + BTC series
    assert report.total_market_rows == 8
    # Run metadata is counted but not moved.
    assert report.run_metadata_counts == {"journal_equity": 2}
    text = report.format()
    assert "candles" in text and "run metadata" in text.lower()


def test_import_is_idempotent(legacy_path):
    sink = InMemoryCacheSource()
    migrate_legacy_duckdb(legacy_path, sink)
    migrate_legacy_duckdb(legacy_path, sink)  # second run must not duplicate

    got = sink.fetch(
        "hyperliquid", "SOL-PERP", Kind.CANDLES, TimeRange(0, _ms(2025, 2, 1))
    )
    assert got.column("ts").to_pylist() == [_ms(2025, 1, 1, 0), _ms(2025, 1, 1, 1)]


def test_legacy_file_is_read_only_and_unmodified(legacy_path):
    before = legacy_path.read_bytes()
    sink = InMemoryCacheSource()
    migrate_legacy_duckdb(legacy_path, sink)
    # Byte-for-byte identical: the importer never wrote to the legacy file.
    assert legacy_path.read_bytes() == before

    # And the read-only connection the importer uses would reject a write.
    con = duckdb.connect(str(legacy_path), read_only=True)
    try:
        with pytest.raises(duckdb.Error):
            con.execute("INSERT INTO candles VALUES "
                        "('X', 1, 0, 0,0,0,0,0, 'hyperliquid')")
    finally:
        con.close()


def test_absent_tables_are_skipped_not_fatal(tmp_path):
    path = tmp_path / "sparse.duckdb"
    con = duckdb.connect(str(path))
    con.execute(_LEGACY_DDL[0])  # candles only
    con.execute(
        "INSERT INTO candles VALUES "
        "('SOL-PERP', 60, 0, 1,1,1,1,1, 'hyperliquid')"
    )
    con.close()

    sink = InMemoryCacheSource()
    report = migrate_legacy_duckdb(path, sink)
    assert report.rows_by_kind == {Kind.CANDLES: 1}
    assert "venue_funding_rates" in report.skipped_tables
    assert "open_interest" in report.skipped_tables


def test_missing_legacy_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        migrate_legacy_duckdb(tmp_path / "nope.duckdb", InMemoryCacheSource())
