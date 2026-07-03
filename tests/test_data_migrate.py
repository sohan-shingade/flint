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

from flint.adapters import InMemoryUserData
from flint.data import (
    InMemoryCacheSource,
    Kind,
    MigrationReport,
    TimeRange,
    migrate_legacy_duckdb,
)
from flint.data.migrate import (
    extract_legacy_run_metadata,
    import_legacy_runs_from_duckdb,
)
from flint.ports import TenantContext
from flint.research import list_runs, load_run

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


# --- run-metadata bridge into the Run Library (slice 6.4b, §19.6) ------------
#
# migrate_legacy_duckdb only *counts* the run-metadata tables; the extraction +
# wiring below lift them into the Phase-6 Run Library. These rows are run metadata
# (strategy names, source, recorded equity endpoints), NOT market data, so
# hand-authoring the fixture is D26-legal — same discipline as the market fixtures.

# strategies DDL recovered verbatim from bf2d385:flint/store/schema.py.
_STRATEGIES_DDL = """CREATE TABLE strategies (
    strategy_id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL,
    code TEXT NOT NULL DEFAULT '', params_json VARCHAR NOT NULL DEFAULT '{}',
    category VARCHAR NOT NULL DEFAULT 'custom', status VARCHAR NOT NULL DEFAULT 'draft',
    created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL,
    notes VARCHAR NOT NULL DEFAULT '')"""

ALICE = TenantContext(tenant_id="alice")
BOB = TenantContext(tenant_id="bob")


def _run_meta_db(path) -> None:
    """Legacy DuckDB holding only the two run-metadata tables (hand-authored, D26)."""
    con = duckdb.connect(str(path))
    try:
        con.execute(_STRATEGIES_DDL)
        con.execute(
            "CREATE TABLE journal_equity ("
            "run_id VARCHAR NOT NULL, ts BIGINT NOT NULL, equity DOUBLE NOT NULL, "
            "PRIMARY KEY (run_id, ts))"
        )
        con.executemany(
            "INSERT INTO strategies VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ("sid-1", "sma_cross", "class S: pass", "{}", "custom", "final",
                 _ms(2025, 1, 1, 0), _ms(2025, 1, 1, 2), ""),
                ("sid-2", "funding_harvest", "class F: pass", "{}", "custom", "draft",
                 _ms(2025, 1, 2, 0), _ms(2025, 1, 2, 0), ""),
            ],
        )
        # run-x rows inserted latest-first to prove the extractor re-orders them so
        # the group's tail is the latest-ts endpoint the importer reads verbatim.
        con.executemany(
            "INSERT INTO journal_equity VALUES (?,?,?)",
            [
                ("run-x", _ms(2025, 1, 1, 1), 10_120.0),
                ("run-x", _ms(2025, 1, 1, 0), 10_000.0),
                ("run-y", _ms(2025, 1, 1, 0), 5_000.0),
            ],
        )
    finally:
        con.close()


@pytest.fixture()
def run_meta_path(tmp_path):
    path = tmp_path / "legacy_runs.duckdb"
    _run_meta_db(path)
    return path


def test_extract_shapes_rows_for_the_run_library_importer(run_meta_path):
    strategy_rows, journal_rows = extract_legacy_run_metadata(run_meta_path)

    # strategies -> name/source/created_ts (no ORDER BY, so match by name).
    by_name = {r["name"]: r for r in strategy_rows}
    assert by_name["sma_cross"] == {
        "name": "sma_cross", "source": "class S: pass", "created_ts": _ms(2025, 1, 1, 0),
    }
    assert by_name["funding_harvest"]["source"] == "class F: pass"
    assert by_name["funding_harvest"]["created_ts"] == _ms(2025, 1, 2, 0)

    # journal_equity -> strategy(run_id)/ts/equity, ordered by run_id then ts so each
    # run's tail row is its latest endpoint — run-x's out-of-order rows are re-sorted.
    assert journal_rows == [
        {"strategy": "run-x", "ts": _ms(2025, 1, 1, 0), "equity": 10_000.0},
        {"strategy": "run-x", "ts": _ms(2025, 1, 1, 1), "equity": 10_120.0},
        {"strategy": "run-y", "ts": _ms(2025, 1, 1, 0), "equity": 5_000.0},
    ]


def test_extract_absent_run_meta_tables_yield_empty_lists(tmp_path):
    # A DB with only market data (no strategies / journal_equity) is not an error.
    path = tmp_path / "market_only.duckdb"
    con = duckdb.connect(str(path))
    con.execute(_LEGACY_DDL[0])  # candles only
    con.close()

    assert extract_legacy_run_metadata(path) == ([], [])


def test_extract_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract_legacy_run_metadata(tmp_path / "nope.duckdb")


def test_extract_leaves_legacy_file_byte_for_byte(run_meta_path):
    before = run_meta_path.read_bytes()
    extract_legacy_run_metadata(run_meta_path)
    assert run_meta_path.read_bytes() == before


def test_import_from_duckdb_persists_manifests_with_honest_sentinels(run_meta_path):
    store = InMemoryUserData()
    run_ids = import_legacy_runs_from_duckdb(ALICE, store, run_meta_path)

    # Two source-bearing strategy manifests + two journal-only run manifests: the
    # legacy tables have no stored join, so they land as four distinct legacy runs.
    assert set(run_ids) == {
        "legacy:sma_cross", "legacy:funding_harvest", "legacy:run-x", "legacy:run-y",
    }

    # Journal-only run: endpoint carried verbatim (latest ts), range = its points.
    run_x = load_run(ALICE, store, "legacy:run-x")
    assert run_x.metrics["final_equity"] == "10120.0"
    assert run_x.metrics["n_equity_points"] == 2
    assert run_x.effective_start_ts == _ms(2025, 1, 1, 0)
    assert run_x.effective_end_ts == _ms(2025, 1, 1, 1)
    # Honest sentinels — legacy never recorded these; seed is None, never 0 (D26).
    assert run_x.seed is None
    assert run_x.engine_version == "legacy"
    assert run_x.lake_revision == "legacy"
    assert run_x.provenance == "legacy"

    # Strategy manifest carries its source; still a legacy head record.
    strat = load_run(ALICE, store, "legacy:sma_cross")
    assert strat.strategy_source == "class S: pass"
    assert strat.seed is None
    assert strat.provenance == "legacy"


def test_import_from_duckdb_is_idempotent(run_meta_path):
    store = InMemoryUserData()
    first = import_legacy_runs_from_duckdb(ALICE, store, run_meta_path)
    second = import_legacy_runs_from_duckdb(ALICE, store, run_meta_path)

    assert first == second
    # Idempotent on legacy:<name> — the re-import overwrote, it did not duplicate.
    assert len(list_runs(ALICE, store)) == 4


def test_import_from_duckdb_is_tenant_scoped(run_meta_path):
    store = InMemoryUserData()
    import_legacy_runs_from_duckdb(ALICE, store, run_meta_path)

    # Bob owns none of Alice's imported runs (§2.7): absent is indistinguishable.
    assert list_runs(BOB, store) == []
    with pytest.raises(KeyError):
        load_run(BOB, store, "legacy:run-x")
