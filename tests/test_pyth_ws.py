"""Tests for PythWebSocketFeed — mocked, no real connections."""
import asyncio
import time
import pytest

from flint.providers.pyth_ws import PythWebSocketFeed


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestPriceCache:
    def test_price_update_cached(self):
        feed = PythWebSocketFeed(markets=["SOL-PERP", "BTC-PERP"])
        run(feed._handle_message({
            "type": "price_update",
            "pair": "SOL/USD",
            "price": 150.25,
            "confidence": 0.05,
            "ts": 1000,
        }))
        result = feed.get_price("SOL-PERP")
        assert result is not None
        price, ts = result
        assert price == 150.25
        assert ts == 1000

    def test_unknown_pair_ignored(self):
        feed = PythWebSocketFeed(markets=["SOL-PERP"])
        run(feed._handle_message({
            "type": "price_update",
            "pair": "UNKNOWN/USD",
            "price": 1.0,
            "confidence": 0.01,
            "ts": 1000,
        }))
        assert feed.get_price("UNKNOWN-PERP") is None

    def test_multiple_updates_latest_wins(self):
        feed = PythWebSocketFeed(markets=["SOL-PERP"])
        run(feed._handle_message({
            "type": "price_update", "pair": "SOL/USD",
            "price": 150.0, "confidence": 0.05, "ts": 1000,
        }))
        run(feed._handle_message({
            "type": "price_update", "pair": "SOL/USD",
            "price": 151.0, "confidence": 0.04, "ts": 1001,
        }))
        price, ts = feed.get_price("SOL-PERP")
        assert price == 151.0
        assert ts == 1001

    def test_get_all_prices(self):
        feed = PythWebSocketFeed(markets=["SOL-PERP", "BTC-PERP"])
        run(feed._handle_message({
            "type": "price_update", "pair": "SOL/USD",
            "price": 150.0, "confidence": 0.05, "ts": 1000,
        }))
        run(feed._handle_message({
            "type": "price_update", "pair": "BTC/USD",
            "price": 65000.0, "confidence": 10.0, "ts": 1000,
        }))
        prices = feed.get_all_prices()
        assert "SOL-PERP" in prices
        assert "BTC-PERP" in prices


class TestBatchPersistence:
    def test_persist_prices_to_store(self, tmp_path):
        from flint.store import FlintStore
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        feed = PythWebSocketFeed(
            markets=["SOL-PERP"],
            store=store,
            batch_interval_s=0,
        )
        run(feed._handle_message({
            "type": "price_update", "pair": "SOL/USD",
            "price": 150.0, "confidence": 0.05, "ts": 1000,
        }))
        feed._flush_to_store()
        prices = store.query_oracle_prices("SOL-PERP")
        assert len(prices) == 1
        assert prices[0].price == 150.0
        store.close()
