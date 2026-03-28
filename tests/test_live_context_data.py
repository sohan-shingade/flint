"""Tests for LiveContext data access methods."""
import os
import tempfile
import pytest

from flint.execution.live_context import LiveContext
from flint.execution.paper_broker import PaperBroker
from flint.models import Candle
from flint.store import FlintStore


@pytest.fixture
def ctx_with_store():
    db = os.path.join(tempfile.gettempdir(), "test_live_ctx.duckdb")
    store = FlintStore(db)
    broker = PaperBroker(initial_capital=10000)
    ctx = LiveContext(broker, store=store, resolution_s=3600, session_id="test1")
    yield ctx, store
    store.close()
    if os.path.exists(db):
        os.unlink(db)


def test_get_candles_returns_data(ctx_with_store):
    ctx, store = ctx_with_store
    candles = [
        Candle(market="SOL-PERP", resolution_s=3600, ts=1700000000 + i * 3600,
               open=100, high=101, low=99, close=100, volume=1000)
        for i in range(10)
    ]
    store.upsert_candles(candles)
    result = ctx.get_candles("SOL-PERP", lookback=5)
    assert len(result) == 5


def test_get_candles_without_store():
    broker = PaperBroker(initial_capital=10000)
    ctx = LiveContext(broker)
    result = ctx.get_candles("SOL-PERP", lookback=5)
    assert result == []


def test_get_funding_rates_without_store():
    broker = PaperBroker(initial_capital=10000)
    ctx = LiveContext(broker)
    result = ctx.get_funding_rates("SOL-PERP")
    assert result == []


def test_get_funding_by_venue_without_store():
    broker = PaperBroker(initial_capital=10000)
    ctx = LiveContext(broker)
    result = ctx.get_funding_by_venue("SOL-PERP")
    assert result == {}


def test_get_orderbook_without_store():
    broker = PaperBroker(initial_capital=10000)
    ctx = LiveContext(broker)
    result = ctx.get_orderbook("SOL-PERP")
    assert result is None


def test_log_does_not_crash(ctx_with_store):
    ctx, _ = ctx_with_store
    ctx.log("test message")


def test_backward_compatible_no_store():
    """Creating LiveContext without store should still work."""
    broker = PaperBroker(initial_capital=10000)
    ctx = LiveContext(broker)
    assert ctx.account.equity == 10000
    assert ctx.positions == []
