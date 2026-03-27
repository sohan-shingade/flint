"""Tests for paper trading funding rate application."""
import os
import tempfile
import pytest

from flint.execution.paper_broker import PaperBroker


def test_apply_funding_long_positive_rate():
    """Long pays when funding rate is positive."""
    broker = PaperBroker(initial_capital=10000)
    broker.positions["SOL-PERP"] = {
        "market": "SOL-PERP", "side": "long", "size": 100,
        "entry_price": 100.0, "entry_ts": 1000, "unrealized_pnl": 0,
    }
    payment = broker.apply_funding("SOL-PERP", rate=0.0001, mark_price=100.0)
    assert abs(payment - 1.0) < 0.01  # 100 * 100 * 0.0001 = 1.0
    assert broker.cash < 10000
    assert broker.total_funding > 0


def test_apply_funding_short_positive_rate():
    """Short receives when funding rate is positive."""
    broker = PaperBroker(initial_capital=10000)
    broker.positions["SOL-PERP"] = {
        "market": "SOL-PERP", "side": "short", "size": 100,
        "entry_price": 100.0, "entry_ts": 1000, "unrealized_pnl": 0,
    }
    payment = broker.apply_funding("SOL-PERP", rate=0.0001, mark_price=100.0)
    assert payment < 0  # short receives
    assert broker.cash > 10000


def test_apply_funding_no_position():
    broker = PaperBroker(initial_capital=10000)
    payment = broker.apply_funding("SOL-PERP", rate=0.0001, mark_price=100.0)
    assert payment == 0.0
    assert broker.cash == 10000


def test_apply_funding_negative_rate():
    """Negative rate = longs receive."""
    broker = PaperBroker(initial_capital=10000)
    broker.positions["SOL-PERP"] = {
        "market": "SOL-PERP", "side": "long", "size": 50,
        "entry_price": 100.0, "entry_ts": 1000, "unrealized_pnl": 0,
    }
    payment = broker.apply_funding("SOL-PERP", rate=-0.0002, mark_price=100.0)
    assert payment < 0  # long receives when rate negative
    assert broker.cash > 10000


def test_close_all_positions():
    broker = PaperBroker(initial_capital=10000)
    broker.positions["SOL-PERP"] = {
        "market": "SOL-PERP", "side": "long", "size": 10,
        "entry_price": 100.0, "entry_ts": 1000, "unrealized_pnl": 0,
    }
    broker.close_all_positions({"SOL-PERP": 110.0})
    assert len(broker.positions) == 0
    assert len(broker.closed_trades) == 1
    assert broker.closed_trades[0]["pnl"] == 100.0  # (110-100)*10


def test_funding_persistence():
    """Test funding payment save/load through session store."""
    from flint.store import FlintStore
    from flint.paper.session_store import PaperSessionStore
    db = os.path.join(tempfile.gettempdir(), "test_funding_persist.duckdb")
    try:
        store = FlintStore(db)
        ss = PaperSessionStore(store)
        ss.save_funding_payment("s1", 1000, "SOL-PERP", 0.0001, 1.0, 100.0, 100.0)
        payments = ss.get_funding_payments("s1")
        assert len(payments) == 1
        assert payments[0]["rate"] == 0.0001
        assert payments[0]["payment"] == 1.0
        store.close()
    finally:
        if os.path.exists(db):
            os.unlink(db)
