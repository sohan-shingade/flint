"""Tests for paper trading session persistence."""
import os
import tempfile
import pytest
from flint.store import FlintStore
from flint.paper.session_store import PaperSessionStore


@pytest.fixture
def session_store():
    db = os.path.join(tempfile.gettempdir(), "test_paper_store.duckdb")
    store = FlintStore(db)
    ss = PaperSessionStore(store)
    yield ss
    store.close()
    if os.path.exists(db):
        os.unlink(db)


def test_save_and_load_session(session_store):
    session_store.save_session(
        session_id="test1", strategy_name="MyStrat", strategy_code="class X: pass",
        strategy_params={"a": 1}, market="SOL-PERP", initial_capital=10000.0,
        replay_start_ts=1700000000, started_at=1700000100, status="replaying",
        risk_config={"max_drawdown_pct": 0.15},
    )
    session = session_store.load_session("test1")
    assert session is not None
    assert session["session_id"] == "test1"
    assert session["strategy_name"] == "MyStrat"
    assert session["market"] == "SOL-PERP"
    assert session["initial_capital"] == 10000.0
    assert session["status"] == "replaying"


def test_update_session_status(session_store):
    session_store.save_session(
        session_id="test2", strategy_name="S", strategy_code="", strategy_params={},
        market="BTC-PERP", initial_capital=5000, replay_start_ts=100, started_at=100,
        status="replaying", risk_config={},
    )
    session_store.update_status("test2", "live", live_start_ts=200)
    s = session_store.load_session("test2")
    assert s["status"] == "live"
    assert s["live_start_ts"] == 200


def test_save_and_query_equity_history(session_store):
    session_store.save_equity_snapshots("s1", [
        {"ts": 100, "equity": 10000, "cash": 9000, "unrealized_pnl": 1000, "is_replay": True},
        {"ts": 200, "equity": 10500, "cash": 9500, "unrealized_pnl": 1000, "is_replay": False},
    ])
    history = session_store.get_equity_history("s1")
    assert len(history) == 2
    assert history[0]["ts"] == 100
    assert history[1]["equity"] == 10500


def test_save_and_query_trades(session_store):
    session_store.save_trades("s1", [
        {"trade_id": "t1", "market": "SOL-PERP", "side": "long", "size": 10,
         "entry_price": 100, "exit_price": 110, "entry_ts": 100, "exit_ts": 200,
         "pnl": 100, "fees": 1, "is_replay": False},
    ])
    trades = session_store.get_trades("s1")
    assert len(trades) == 1
    assert trades[0]["pnl"] == 100


def test_save_and_load_positions(session_store):
    session_store.save_positions("s1", [
        {"market": "SOL-PERP", "side": "short", "size": 5, "entry_price": 120,
         "entry_ts": 100, "unrealized_pnl": -50},
    ])
    positions = session_store.load_positions("s1")
    assert len(positions) == 1
    assert positions[0]["side"] == "short"


def test_list_active_sessions(session_store):
    session_store.save_session(
        session_id="a1", strategy_name="S", strategy_code="", strategy_params={},
        market="SOL-PERP", initial_capital=10000, replay_start_ts=100, started_at=100,
        status="live", risk_config={},
    )
    session_store.save_session(
        session_id="a2", strategy_name="S", strategy_code="", strategy_params={},
        market="BTC-PERP", initial_capital=5000, replay_start_ts=100, started_at=100,
        status="stopped", risk_config={},
    )
    active = session_store.list_active_sessions()
    assert len(active) == 1
    assert active[0]["session_id"] == "a1"


def test_list_all_sessions(session_store):
    session_store.save_session(
        session_id="b1", strategy_name="S", strategy_code="", strategy_params={},
        market="SOL-PERP", initial_capital=10000, replay_start_ts=100, started_at=100,
        status="live", risk_config={},
    )
    session_store.save_session(
        session_id="b2", strategy_name="S2", strategy_code="", strategy_params={},
        market="BTC-PERP", initial_capital=5000, replay_start_ts=100, started_at=100,
        status="stopped", risk_config={},
    )
    all_sessions = session_store.list_all_sessions()
    assert len(all_sessions) == 2
