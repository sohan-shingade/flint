import os
import tempfile

from flint.models import Candle
from flint.store import FlintStore


def _make_store():
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)
    return FlintStore(path), path


def test_get_markets_needing_pyth_migration():
    store, path = _make_store()
    try:
        store.upsert_candles([
            Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "drift"),
        ])
        store.upsert_candles([
            Candle(1700000000, 40000.0, 40100.0, 39900.0, 40050.0, 500.0, "BTC-PERP", 3600, "pyth"),
        ])
        needs = store.get_markets_needing_pyth_migration()
        assert "SOL-PERP" in needs
        assert "BTC-PERP" not in needs
    finally:
        store.close()
        os.unlink(path)


def test_get_market_date_range():
    store, path = _make_store()
    try:
        store.upsert_candles([
            Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "drift"),
            Candle(1700050000, 100.5, 102.0, 100.0, 101.0, 1100.0, "SOL-PERP", 3600, "drift"),
        ])
        start, end = store.get_market_date_range("SOL-PERP")
        assert start == 1700000000
        assert end == 1700050000
    finally:
        store.close()
        os.unlink(path)


def test_get_market_date_range_no_data():
    store, path = _make_store()
    try:
        assert store.get_market_date_range("SOL-PERP") is None
    finally:
        store.close()
        os.unlink(path)


def test_query_candles_with_fallback_prefers_pyth():
    store, path = _make_store()
    try:
        store.upsert_candles([
            Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "drift"),
            Candle(1700000000, 100.1, 101.1, 99.1, 100.6, 1000.0, "SOL-PERP", 3600, "pyth"),
        ])
        candles = store.query_candles_with_fallback("SOL-PERP", 3600, 1700000000, 1700003600)
        assert len(candles) == 1
        assert candles[0].venue == "pyth"
    finally:
        store.close()
        os.unlink(path)


def test_query_candles_with_fallback_uses_any_venue():
    store, path = _make_store()
    try:
        store.upsert_candles([
            Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "drift"),
        ])
        candles = store.query_candles_with_fallback("SOL-PERP", 3600, 1700000000, 1700003600)
        assert len(candles) == 1
        assert candles[0].ts == 1700000000
    finally:
        store.close()
        os.unlink(path)
