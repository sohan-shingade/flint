"""Durable DuckDB store adapters + lake layout/cache contracts (slice 2.1).

The DuckDB adapters must honour the exact same port contracts the Phase-1
in-memory adapters proved (§2.7): tenant cross-leak isolation, half-open Arrow
loads, append-only events. These tests run those contracts against the durable
adapters, plus the §9.0 layout/migration/cache-key behaviour they add.
"""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from flint.data.store import (
    FRESHNESS_TTL_MS,
    DuckDBMarketData,
    DuckDBUserData,
    MigrationRegistry,
    cache_key,
    is_fresh,
    is_immutable,
    partition_path,
    read_parquet,
    read_schema_version,
    write_parquet,
)
from flint.ports import RunRecord, TenantContext

ALICE = TenantContext(tenant_id="alice")
BOB = TenantContext(tenant_id="bob")


# --- DuckDBMarketData: Arrow boundary + half-open loads --------------------


def test_market_data_arrow_roundtrip_is_half_open():
    store = DuckDBMarketData()
    table = pa.table({"ts": [100, 200, 300], "close": [1.0, 2.0, 3.0]})
    assert store.save_candles("hyperliquid", "SOL-PERP", table) == 3

    got = store.load_candles("hyperliquid", "SOL-PERP", 100, 300)
    assert got.column("ts").to_pylist() == [100, 200]  # 300 excluded (half-open)
    assert got.column("close").to_pylist() == [1.0, 2.0]
    # venue/market are storage keys, not part of the returned candle schema.
    assert set(got.column_names) == {"ts", "close"}


def test_market_data_load_before_any_save_is_empty():
    assert DuckDBMarketData().load_candles("hyperliquid", "SOL-PERP", 0, 10).num_rows == 0


def test_market_data_is_keyed_per_venue_market():
    store = DuckDBMarketData()
    store.save_candles("hyperliquid", "SOL-PERP", pa.table({"ts": [1], "close": [9.0]}))
    store.save_candles("hyperliquid", "BTC-PERP", pa.table({"ts": [1], "close": [8.0]}))
    sol = store.load_candles("hyperliquid", "SOL-PERP", 0, 10)
    assert sol.column("close").to_pylist() == [9.0]  # BTC row not mixed in


def test_market_data_resave_is_idempotent_per_key():
    store = DuckDBMarketData()
    store.save_candles("hyperliquid", "SOL-PERP", pa.table({"ts": [1, 2], "close": [1.0, 2.0]}))
    # Re-saving the same ts keys (with a corrected value) replaces, never dupes.
    store.save_candles("hyperliquid", "SOL-PERP", pa.table({"ts": [2], "close": [2.5]}))
    got = store.load_candles("hyperliquid", "SOL-PERP", 0, 10)
    assert got.column("ts").to_pylist() == [1, 2]
    assert got.column("close").to_pylist() == [1.0, 2.5]


def test_market_data_survives_reopen(tmp_path):
    db = str(tmp_path / "market.duckdb")
    DuckDBMarketData(db).save_candles(
        "hyperliquid", "SOL-PERP", pa.table({"ts": [5], "close": [7.0]})
    )
    reopened = DuckDBMarketData(db)  # durable: a fresh handle sees prior writes
    assert reopened.load_candles("hyperliquid", "SOL-PERP", 0, 10).column("close").to_pylist() == [7.0]


# --- DuckDBUserData: the tenant cross-leak seam (§2.7) ----------------------


def test_tenant_cannot_read_other_tenants_run():
    store = DuckDBUserData()
    store.save_run(ALICE, RunRecord(run_id="r1"))
    store.save_run(BOB, RunRecord(run_id="r2"))

    assert store.load_run(ALICE, "r1").run_id == "r1"
    assert store.load_run(BOB, "r2").run_id == "r2"
    with pytest.raises(KeyError):
        store.load_run(BOB, "r1")
    with pytest.raises(KeyError):
        store.load_run(ALICE, "r2")


def test_list_runs_never_shows_another_tenant():
    store = DuckDBUserData()
    store.save_run(ALICE, RunRecord(run_id="r1"))
    store.save_run(ALICE, RunRecord(run_id="r2"))
    store.save_run(BOB, RunRecord(run_id="r3"))
    assert {r.run_id for r in store.list_runs(ALICE)} == {"r1", "r2"}
    assert {r.run_id for r in store.list_runs(BOB)} == {"r3"}


def test_cross_leak_error_does_not_reveal_existence():
    store = DuckDBUserData()
    store.save_run(ALICE, RunRecord(run_id="secret"))
    with pytest.raises(KeyError) as owned:
        store.load_run(BOB, "secret")
    with pytest.raises(KeyError) as absent:
        store.load_run(BOB, "never-existed")
    assert str(owned.value) == str(absent.value)


def test_run_record_round_trips_all_fields():
    store = DuckDBUserData()
    rec = RunRecord(
        run_id="r1", kind="paper", status="done", created_ts=1234,
        summary={"sharpe": 1.8, "trades": 42},
    )
    store.save_run(ALICE, rec)
    assert store.load_run(ALICE, "r1") == rec


def test_save_run_upserts_on_same_id():
    store = DuckDBUserData()
    store.save_run(ALICE, RunRecord(run_id="r1", status="created"))
    store.save_run(ALICE, RunRecord(run_id="r1", status="done"))
    assert store.load_run(ALICE, "r1").status == "done"
    assert len(store.list_runs(ALICE)) == 1


# --- DuckDBUserData: append-only event rows (§2.10) -------------------------


def test_append_events_preserves_order_and_totals():
    store = DuckDBUserData()
    assert store.append_events(ALICE, "run-1", [{"seq": 0, "k": "a"}]) == 1
    assert store.append_events(ALICE, "run-1", [{"seq": 1, "k": "b"}, {"seq": 2, "k": "c"}]) == 3
    assert [r["k"] for r in store.load_events(ALICE, "run-1")] == ["a", "b", "c"]


def test_events_are_tenant_scoped():
    store = DuckDBUserData()
    store.append_events(ALICE, "run-1", [{"who": "alice"}])
    assert store.load_events(BOB, "run-1") == []  # same run_id, different tenant


def test_events_round_trip_nested_payload_verbatim():
    store = DuckDBUserData()
    row = {"kind": "fill", "event_version": 1, "ts": 10, "seq": 0, "payload": {"price": 100.0}}
    store.append_events(ALICE, "r", [row])
    assert store.load_events(ALICE, "r") == [row]


# --- lake layout: partitioning + upgrade-on-read (§9.0) ---------------------


def test_partition_path_day_and_hour():
    assert partition_path("candles", "hyperliquid", "SOL-PERP", "2026-01-01") == (
        "candles/hyperliquid/SOL-PERP/2026-01-01"
    )
    assert partition_path("depth", "hyperliquid", "SOL-PERP", "2026-01-01", hour=9) == (
        "depth/hyperliquid/SOL-PERP/2026-01-01/09"
    )


def test_partition_path_rejects_hour_mismatch():
    with pytest.raises(ValueError):
        partition_path("depth", "hyperliquid", "SOL-PERP", "2026-01-01")  # depth needs hour
    with pytest.raises(ValueError):
        partition_path("candles", "hyperliquid", "SOL-PERP", "2026-01-01", hour=9)


def test_parquet_stamps_and_reads_schema_version(tmp_path):
    path = str(tmp_path / "c.parquet")
    write_parquet(pa.table({"ts": [1], "close": [1.0]}), path, schema_version=1)
    assert read_schema_version(path) == 1
    assert read_parquet(path).column("close").to_pylist() == [1.0]


def test_read_parquet_upgrades_old_file_on_read(tmp_path):
    path = str(tmp_path / "old.parquet")
    write_parquet(pa.table({"ts": [1]}), path, schema_version=1)

    reg = MigrationRegistry()

    @reg.register(1)
    def _v1_to_v2(table: pa.Table) -> pa.Table:
        return table.append_column("venue", pa.array(["hyperliquid"] * table.num_rows))

    upgraded = read_parquet(path, registry=reg, to_version=2)
    assert upgraded.column("venue").to_pylist() == ["hyperliquid"]
    # The on-disk file is untouched — still v1 with no venue column.
    assert read_schema_version(path) == 1
    assert "venue" not in pq.read_table(path).column_names


def test_read_parquet_needs_a_registry_for_old_files(tmp_path):
    path = str(tmp_path / "old.parquet")
    write_parquet(pa.table({"ts": [1]}), path, schema_version=1)
    with pytest.raises(KeyError):
        read_parquet(path, to_version=2)  # no registry to bridge the gap


def test_migration_registry_rejects_duplicate_and_walks_chain():
    reg = MigrationRegistry()
    reg.register(1)(lambda t: t.append_column("a", pa.array([1])))
    reg.register(2)(lambda t: t.append_column("b", pa.array([2])))
    with pytest.raises(ValueError):
        reg.register(1)(lambda t: t)  # duplicate from-version

    out = reg.upgrade(pa.table({"ts": [0]}), from_version=1, to_version=3)
    assert out.column_names == ["ts", "a", "b"]

    with pytest.raises(KeyError):
        reg.upgrade(pa.table({"ts": [0]}), from_version=3, to_version=4)  # gap


# --- cache: content-addressing + freshness (§9.0) --------------------------


def test_cache_key_is_deterministic_and_sensitive():
    base = cache_key("hl", 0, 100, 1, "rev-1")
    assert base == cache_key("hl", 0, 100, 1, "rev-1")  # deterministic
    # A lake correction (new revision) yields a *different* key, never a collision.
    assert base != cache_key("hl", 0, 100, 1, "rev-2")
    # Range and schema_version are part of the address too.
    assert base != cache_key("hl", 0, 101, 1, "rev-1")
    assert base != cache_key("hl", 0, 100, 2, "rev-1")


def test_cache_key_fields_cannot_collide_by_concatenation():
    # NUL delimiting means adjacent-field boundaries can't be forged.
    assert cache_key("ab", 0, 0, 1, "c") != cache_key("a", 0, 0, 1, "bc")


def test_immutable_kinds_never_expire_but_revisable_kinds_do():
    assert is_immutable("candles") and is_immutable("depth")
    assert not is_immutable("funding") and not is_immutable("oi")

    # Immutable kinds are fresh no matter how old.
    assert is_fresh("candles", written_ts_ms=0, now_ts_ms=10 * FRESHNESS_TTL_MS)
    # Revisable kinds go stale past the 24h TTL.
    assert is_fresh("funding", written_ts_ms=0, now_ts_ms=FRESHNESS_TTL_MS - 1)
    assert not is_fresh("funding", written_ts_ms=0, now_ts_ms=FRESHNESS_TTL_MS)
