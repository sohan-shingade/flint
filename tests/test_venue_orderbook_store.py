import os
import tempfile

from flint.models import OrderbookLevel, OrderbookSnapshot
from flint.store import FlintStore


def _make_store():
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)
    return FlintStore(path), path


def test_upsert_venue_orderbook():
    store, path = _make_store()
    try:
        snapshots = [
            {"venue": "drift", "market": "SOL-PERP", "ts": 1700000000,
             "bid_prices": [99.9, 99.8], "bid_sizes": [100.0, 200.0],
             "ask_prices": [100.1, 100.2], "ask_sizes": [150.0, 250.0]},
            {"venue": "binance", "market": "SOL-PERP", "ts": 1700000000,
             "bid_prices": [99.95, 99.85], "bid_sizes": [500.0, 800.0],
             "ask_prices": [100.05, 100.15], "ask_sizes": [600.0, 900.0]},
        ]
        count = store.upsert_orderbook_snapshots(snapshots)
        assert count == 2
    finally:
        store.close()
        os.unlink(path)


def test_query_nearest_orderbook():
    store, path = _make_store()
    try:
        snapshots = [
            {"venue": "drift", "market": "SOL-PERP", "ts": 1700000000,
             "bid_prices": [99.9], "bid_sizes": [100.0],
             "ask_prices": [100.1], "ask_sizes": [150.0]},
            {"venue": "drift", "market": "SOL-PERP", "ts": 1700000300,
             "bid_prices": [99.85], "bid_sizes": [110.0],
             "ask_prices": [100.15], "ask_sizes": [160.0]},
        ]
        store.upsert_orderbook_snapshots(snapshots)
        # Exact match
        book = store.query_nearest_orderbook("drift", "SOL-PERP", 1700000300)
        assert book is not None
        assert book.ts == 1700000300
        # Nearest before
        book = store.query_nearest_orderbook("drift", "SOL-PERP", 1700000200)
        assert book is not None
        assert book.ts == 1700000000
        # No data before the earliest snapshot
        book = store.query_nearest_orderbook("drift", "SOL-PERP", 1699999000)
        assert book is None
        # Wrong venue returns None
        book = store.query_nearest_orderbook("binance", "SOL-PERP", 1700000300)
        assert book is None
    finally:
        store.close()
        os.unlink(path)


def test_same_ts_different_venues():
    store, path = _make_store()
    try:
        snapshots = [
            {"venue": "drift", "market": "SOL-PERP", "ts": 1700000000,
             "bid_prices": [99.9], "bid_sizes": [100.0], "ask_prices": [100.1], "ask_sizes": [150.0]},
            {"venue": "binance", "market": "SOL-PERP", "ts": 1700000000,
             "bid_prices": [99.95], "bid_sizes": [500.0], "ask_prices": [100.05], "ask_sizes": [600.0]},
        ]
        store.upsert_orderbook_snapshots(snapshots)
        drift_book = store.query_nearest_orderbook("drift", "SOL-PERP", 1700000000)
        binance_book = store.query_nearest_orderbook("binance", "SOL-PERP", 1700000000)
        assert drift_book is not None and binance_book is not None
        assert drift_book.bids[0].size == 100.0
        assert binance_book.bids[0].size == 500.0
    finally:
        store.close()
        os.unlink(path)
