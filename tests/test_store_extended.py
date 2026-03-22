"""Tests for extended DuckDB store — oracle prices, orderbook snapshots, pool snapshots,
open interest, liquidations, whale transfers, DEX volume, token unlocks, sync metadata."""
import pytest
from flint.store import FlintStore
from flint.models import (
    OraclePrice, OpenInterest, Liquidation, WhaleTransfer,
    DexVolume, TokenUnlock, SyncMetadata,
)


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


# -- open interest -----------------------------------------------------------

def test_upsert_and_query_open_interest(store):
    oi = [
        OpenInterest(market="SOL-PERP", ts=1000, long_oi=5000, short_oi=4500),
        OpenInterest(market="SOL-PERP", ts=2000, long_oi=5500, short_oi=4000),
    ]
    assert store.upsert_open_interest(oi) == 2
    result = store.query_open_interest("SOL-PERP")
    assert len(result) == 2
    assert result[0].long_oi == 5000
    assert result[0].net_oi == 500
    assert result[0].total_oi == 9500


def test_query_open_interest_with_range(store):
    oi = [
        OpenInterest(market="SOL-PERP", ts=1000, long_oi=5000, short_oi=4500),
        OpenInterest(market="SOL-PERP", ts=2000, long_oi=5500, short_oi=4000),
        OpenInterest(market="SOL-PERP", ts=3000, long_oi=6000, short_oi=3500),
    ]
    store.upsert_open_interest(oi)
    result = store.query_open_interest("SOL-PERP", start_ts=1500, end_ts=2500)
    assert len(result) == 1
    assert result[0].ts == 2000


def test_upsert_open_interest_empty(store):
    assert store.upsert_open_interest([]) == 0


def test_upsert_open_interest_replaces(store):
    oi1 = [OpenInterest(market="SOL-PERP", ts=1000, long_oi=5000, short_oi=4500)]
    oi2 = [OpenInterest(market="SOL-PERP", ts=1000, long_oi=6000, short_oi=5500)]
    store.upsert_open_interest(oi1)
    store.upsert_open_interest(oi2)
    result = store.query_open_interest("SOL-PERP")
    assert len(result) == 1
    assert result[0].long_oi == 6000


# -- liquidations ------------------------------------------------------------

def test_upsert_and_query_liquidations(store):
    liqs = [Liquidation(market="SOL-PERP", ts=1000, side="long", size=10.0, price=150.0, slot=100)]
    assert store.upsert_liquidations(liqs) == 1
    result = store.query_liquidations("SOL-PERP")
    assert len(result) == 1
    assert result[0].side == "long"
    assert result[0].size == 10.0
    assert result[0].price == 150.0


def test_query_liquidations_with_range(store):
    liqs = [
        Liquidation(market="SOL-PERP", ts=1000, side="long", size=10.0, price=150.0, slot=100, tx_sig="tx1"),
        Liquidation(market="SOL-PERP", ts=2000, side="short", size=5.0, price=155.0, slot=200, tx_sig="tx2"),
        Liquidation(market="SOL-PERP", ts=3000, side="long", size=8.0, price=148.0, slot=300, tx_sig="tx3"),
    ]
    store.upsert_liquidations(liqs)
    result = store.query_liquidations("SOL-PERP", start_ts=1500, end_ts=2500)
    assert len(result) == 1
    assert result[0].ts == 2000


def test_upsert_liquidations_empty(store):
    assert store.upsert_liquidations([]) == 0


# -- whale transfers ---------------------------------------------------------

def test_upsert_and_query_whale_transfers(store):
    wts = [WhaleTransfer(wallet="ABC", token_mint="SOL", amount=50000, ts=1000, direction="in", tx_sig="tx1")]
    assert store.upsert_whale_transfers(wts) == 1
    result = store.query_whale_transfers(token_mint="SOL")
    assert len(result) == 1
    assert result[0].wallet == "ABC"
    assert result[0].direction == "in"


def test_query_whale_transfers_by_wallet(store):
    wts = [
        WhaleTransfer(wallet="ABC", token_mint="SOL", amount=50000, ts=1000, direction="in", tx_sig="tx1"),
        WhaleTransfer(wallet="DEF", token_mint="SOL", amount=30000, ts=2000, direction="out", tx_sig="tx2"),
    ]
    store.upsert_whale_transfers(wts)
    result = store.query_whale_transfers(wallet="ABC")
    assert len(result) == 1
    assert result[0].wallet == "ABC"


def test_query_whale_transfers_with_range(store):
    wts = [
        WhaleTransfer(wallet="ABC", token_mint="SOL", amount=50000, ts=1000, direction="in", tx_sig="tx1"),
        WhaleTransfer(wallet="ABC", token_mint="SOL", amount=60000, ts=2000, direction="out", tx_sig="tx2"),
        WhaleTransfer(wallet="ABC", token_mint="SOL", amount=70000, ts=3000, direction="in", tx_sig="tx3"),
    ]
    store.upsert_whale_transfers(wts)
    result = store.query_whale_transfers(start_ts=1500, end_ts=2500)
    assert len(result) == 1
    assert result[0].ts == 2000


def test_upsert_whale_transfers_empty(store):
    assert store.upsert_whale_transfers([]) == 0


# -- dex volume --------------------------------------------------------------

def test_upsert_and_query_dex_volume(store):
    dvs = [DexVolume(market="SOL/USDC", dex="raydium", ts=1000, volume_usd=1_000_000, txn_count=500)]
    assert store.upsert_dex_volume(dvs) == 1
    result = store.query_dex_volume("SOL/USDC")
    assert len(result) == 1
    assert result[0].dex == "raydium"
    assert result[0].volume_usd == 1_000_000
    assert result[0].txn_count == 500


def test_query_dex_volume_by_dex(store):
    dvs = [
        DexVolume(market="SOL/USDC", dex="raydium", ts=1000, volume_usd=1_000_000, txn_count=500),
        DexVolume(market="SOL/USDC", dex="orca", ts=1000, volume_usd=500_000, txn_count=200),
    ]
    store.upsert_dex_volume(dvs)
    result = store.query_dex_volume("SOL/USDC", dex="orca")
    assert len(result) == 1
    assert result[0].dex == "orca"


def test_query_dex_volume_with_range(store):
    dvs = [
        DexVolume(market="SOL/USDC", dex="raydium", ts=1000, volume_usd=1_000_000, txn_count=500),
        DexVolume(market="SOL/USDC", dex="raydium", ts=2000, volume_usd=2_000_000, txn_count=800),
        DexVolume(market="SOL/USDC", dex="raydium", ts=3000, volume_usd=1_500_000, txn_count=600),
    ]
    store.upsert_dex_volume(dvs)
    result = store.query_dex_volume("SOL/USDC", start_ts=1500, end_ts=2500)
    assert len(result) == 1
    assert result[0].ts == 2000


def test_upsert_dex_volume_empty(store):
    assert store.upsert_dex_volume([]) == 0


# -- token unlocks -----------------------------------------------------------

def test_upsert_token_unlocks(store):
    unlocks = [
        TokenUnlock(token_mint="SOL111", unlock_ts=1700000000, amount=1_000_000, vesting_account="vest1"),
        TokenUnlock(token_mint="SOL111", unlock_ts=1703000000, amount=500_000, vesting_account="vest1"),
    ]
    assert store.upsert_token_unlocks(unlocks) == 2


def test_upsert_token_unlocks_empty(store):
    assert store.upsert_token_unlocks([]) == 0


def test_upsert_token_unlocks_replaces(store):
    u1 = [TokenUnlock(token_mint="SOL111", unlock_ts=1700000000, amount=1_000_000, vesting_account="vest1")]
    u2 = [TokenUnlock(token_mint="SOL111", unlock_ts=1700000000, amount=2_000_000, vesting_account="vest1")]
    store.upsert_token_unlocks(u1)
    store.upsert_token_unlocks(u2)
    # Verify replacement by checking we still have 1 row (not 2)
    with store._lock:
        row = store._conn.execute(
            "SELECT COUNT(*) FROM token_unlocks WHERE token_mint = ?", ["SOL111"]
        ).fetchone()
    assert row[0] == 1


# -- sync metadata -----------------------------------------------------------

def test_sync_metadata(store):
    sm = SyncMetadata(provider="birdeye", market="SOL", data_type="candles", last_sync_ts=1000, record_count=500)
    store.upsert_sync_metadata(sm)
    result = store.get_sync_metadata("birdeye", "SOL", "candles")
    assert result is not None
    assert result.last_sync_ts == 1000
    assert result.record_count == 500

    # Update
    sm2 = SyncMetadata(provider="birdeye", market="SOL", data_type="candles", last_sync_ts=2000, record_count=1000)
    store.upsert_sync_metadata(sm2)
    result2 = store.get_sync_metadata("birdeye", "SOL", "candles")
    assert result2.last_sync_ts == 2000
    assert result2.record_count == 1000


def test_get_sync_metadata_not_found(store):
    result = store.get_sync_metadata("nonexistent", "X", "candles")
    assert result is None


def test_sync_metadata_with_error(store):
    sm = SyncMetadata(
        provider="drift", market="SOL-PERP", data_type="candles",
        last_sync_ts=1000, record_count=0, status="error", error_msg="timeout"
    )
    store.upsert_sync_metadata(sm)
    result = store.get_sync_metadata("drift", "SOL-PERP", "candles")
    assert result.status == "error"
    assert result.error_msg == "timeout"


def test_list_sync_metadata(store):
    store.upsert_sync_metadata(SyncMetadata(provider="drift", market="SOL-PERP", data_type="candles", last_sync_ts=1000, record_count=100))
    store.upsert_sync_metadata(SyncMetadata(provider="drift", market="BTC-PERP", data_type="candles", last_sync_ts=2000, record_count=200))
    store.upsert_sync_metadata(SyncMetadata(provider="birdeye", market="SOL", data_type="candles", last_sync_ts=3000, record_count=300))
    result = store.list_sync_metadata()
    assert len(result) == 3


def test_list_sync_metadata_by_provider(store):
    store.upsert_sync_metadata(SyncMetadata(provider="drift", market="SOL-PERP", data_type="candles", last_sync_ts=1000, record_count=100))
    store.upsert_sync_metadata(SyncMetadata(provider="birdeye", market="SOL", data_type="candles", last_sync_ts=2000, record_count=200))
    result = store.list_sync_metadata(provider="drift")
    assert len(result) == 1
    assert result[0].provider == "drift"


def test_data_freshness(store):
    store.upsert_sync_metadata(SyncMetadata(provider="drift", market="SOL-PERP", data_type="candles", last_sync_ts=1000, record_count=100))
    freshness = store.get_data_freshness()
    assert len(freshness) >= 1
    assert freshness[0]["provider"] == "drift"
    assert freshness[0]["market"] == "SOL-PERP"
    assert freshness[0]["data_type"] == "candles"
    assert freshness[0]["last_sync_ts"] == 1000
    assert freshness[0]["record_count"] == 100
    assert "age_s" in freshness[0]
    assert freshness[0]["age_s"] > 0


def test_data_freshness_ordering(store):
    """Freshness results should be ordered by last_sync_ts descending (most recent first)."""
    store.upsert_sync_metadata(SyncMetadata(provider="drift", market="SOL-PERP", data_type="candles", last_sync_ts=1000, record_count=100))
    store.upsert_sync_metadata(SyncMetadata(provider="birdeye", market="SOL", data_type="candles", last_sync_ts=3000, record_count=300))
    freshness = store.get_data_freshness()
    assert len(freshness) == 2
    assert freshness[0]["last_sync_ts"] >= freshness[1]["last_sync_ts"]
