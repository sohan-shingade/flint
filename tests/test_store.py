"""Tests for DuckDB store."""
from flint.models import Candle
from flint.store import FlintStore


def test_upsert_and_query(store: FlintStore, sample_candles: list[Candle]):
    n = store.upsert_candles(sample_candles)
    assert n == 60
    assert store.count_candles("TEST-PERP", 3600) == 60

    result = store.query_candles("TEST-PERP", 3600)
    assert len(result) == 60
    assert result[0].ts < result[-1].ts


def test_upsert_replaces(store: FlintStore):
    c1 = Candle(ts=1000, open=1, high=2, low=0.5, close=1.5, volume=10, market="X", resolution_s=60)
    c2 = Candle(ts=1000, open=2, high=3, low=1.0, close=2.5, volume=20, market="X", resolution_s=60)

    store.upsert_candles([c1])
    assert store.count_candles("X", 60) == 1

    store.upsert_candles([c2])
    assert store.count_candles("X", 60) == 1

    rows = store.query_candles("X", 60)
    assert rows[0].open == 2.0
    assert rows[0].volume == 20.0


def test_query_time_range(store: FlintStore, sample_candles: list[Candle]):
    store.upsert_candles(sample_candles)
    mid_ts = sample_candles[25].ts
    result = store.query_candles("TEST-PERP", 3600, start_ts=mid_ts)
    assert len(result) == 35
    assert result[0].ts == mid_ts


def test_upsert_empty(store: FlintStore):
    assert store.upsert_candles([]) == 0


def test_query_no_results(store: FlintStore):
    assert store.query_candles("NOPE", 60) == []


def test_paper_session_tables_exist():
    """Paper trading tables should be created on store init."""
    import tempfile, os
    db = os.path.join(tempfile.gettempdir(), "test_paper_tables.duckdb")
    try:
        store = FlintStore(db)
        with store._lock:
            tables = [r[0] for r in store._conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
            ).fetchall()]
        assert "paper_sessions" in tables
        assert "paper_equity_history" in tables
        assert "paper_trades" in tables
        assert "paper_positions" in tables
        store.close()
    finally:
        if os.path.exists(db):
            os.unlink(db)
