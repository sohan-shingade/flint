"""Tests for extended DuckDB store — oracle prices, orderbook snapshots, pool snapshots."""
import pytest
from flint.store import FlintStore
from flint.models import OraclePrice


@pytest.fixture
def store():
    s = FlintStore(":memory:")
    yield s
    s.close()


def test_upsert_and_query_oracle_prices(store):
    prices = [
        OraclePrice(market="SOL-PERP", ts=1000, price=100.5),
        OraclePrice(market="SOL-PERP", ts=2000, price=101.0),
    ]
    count = store.upsert_oracle_prices(prices)
    assert count == 2
    result = store.query_oracle_prices("SOL-PERP")
    assert len(result) == 2
    assert result[0].price == 100.5


def test_query_oracle_prices_with_range(store):
    prices = [
        OraclePrice(market="SOL-PERP", ts=1000, price=100.0),
        OraclePrice(market="SOL-PERP", ts=2000, price=101.0),
        OraclePrice(market="SOL-PERP", ts=3000, price=102.0),
    ]
    store.upsert_oracle_prices(prices)
    result = store.query_oracle_prices("SOL-PERP", start_ts=1500, end_ts=2500)
    assert len(result) == 1
    assert result[0].ts == 2000


def test_upsert_orderbook_snapshots(store):
    count = store.upsert_orderbook_snapshots([{
        "market": "SOL-PERP",
        "ts": 1000,
        "bid_prices": [100.0, 99.5],
        "bid_sizes": [10.0, 20.0],
        "ask_prices": [101.0, 101.5],
        "ask_sizes": [15.0, 25.0],
    }])
    assert count == 1


def test_upsert_pool_snapshots(store):
    count = store.upsert_pool_snapshots([{
        "pool_address": "pool1",
        "dex": "raydium",
        "token_a_mint": "SOL",
        "token_b_mint": "USDC",
        "reserve_a": 1000.0,
        "reserve_b": 100000.0,
        "fee_rate": 0.003,
        "ts": 1000,
    }])
    assert count == 1


def test_collector_status_query(store):
    """Store should report table row counts for collector status."""
    prices = [OraclePrice(market="SOL-PERP", ts=1000, price=100.0)]
    store.upsert_oracle_prices(prices)
    count = store.count_oracle_prices("SOL-PERP")
    assert count == 1
