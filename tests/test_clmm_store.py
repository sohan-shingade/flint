"""Tests for tick_snapshots store methods."""
import json
import pytest
from flint.store import FlintStore

class TestTickSnapshots:
    def test_upsert_and_query(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        tick_data = json.dumps([{"lower": -1000, "upper": 0, "liquidity": 500000}])
        store.upsert_tick_snapshot(pool_address="pool1", ts=1000, dex="orca",
            token_a_mint="SOL", token_b_mint="USDC", current_tick=50, tick_spacing=64,
            fee_rate=0.003, sqrt_price=12.247, tick_data_json=tick_data)
        results = store.query_tick_snapshots("pool1")
        assert len(results) == 1
        assert results[0]["dex"] == "orca"
        store.close()

    def test_query_with_time_range(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        for ts in [1000, 2000, 3000]:
            store.upsert_tick_snapshot(pool_address="pool1", ts=ts, dex="orca",
                token_a_mint="SOL", token_b_mint="USDC", current_tick=50,
                tick_spacing=64, fee_rate=0.003, sqrt_price=12.0, tick_data_json="[]")
        results = store.query_tick_snapshots("pool1", start_ts=1500, end_ts=2500)
        assert len(results) == 1
        assert results[0]["ts"] == 2000
        store.close()

    def test_upsert_replaces(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        store.upsert_tick_snapshot(pool_address="pool1", ts=1000, dex="orca",
            token_a_mint="SOL", token_b_mint="USDC", current_tick=50,
            tick_spacing=64, fee_rate=0.003, sqrt_price=12.0, tick_data_json="[]")
        store.upsert_tick_snapshot(pool_address="pool1", ts=1000, dex="orca",
            token_a_mint="SOL", token_b_mint="USDC", current_tick=100,
            tick_spacing=64, fee_rate=0.003, sqrt_price=13.0, tick_data_json="[]")
        results = store.query_tick_snapshots("pool1")
        assert len(results) == 1
        assert results[0]["current_tick"] == 100
        store.close()
