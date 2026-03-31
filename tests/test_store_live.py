"""Tests for live trading store tables and methods."""
import time
import pytest

from flint.store import FlintStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    s = FlintStore(path=db_path)
    yield s
    s.close()


class TestLiveSessions:
    def test_create_and_get(self, store):
        store.create_live_session(
            session_id="s1", strategy_name="momentum", market="SOL-PERP",
            network="devnet", venue="drift", initial_capital=10000.0,
            config_snapshot='{"live_network": "devnet"}',
        )
        session = store.get_live_session("s1")
        assert session is not None
        assert session["strategy_name"] == "momentum"
        assert session["network"] == "devnet"
        assert session["status"] == "running"

    def test_update_status(self, store):
        store.create_live_session(
            session_id="s2", strategy_name="arb", market="BTC-PERP",
            network="mainnet", venue="drift", initial_capital=50000.0,
            config_snapshot="{}",
        )
        now = int(time.time())
        store.update_live_session_status("s2", "stopped", stopped_at=now)
        session = store.get_live_session("s2")
        assert session["status"] == "stopped"
        assert session["stopped_at"] == now

    def test_get_nonexistent(self, store):
        assert store.get_live_session("nope") is None


class TestLiveOrders:
    def test_upsert_and_query(self, store):
        now = int(time.time())
        store.upsert_live_order(
            order_id="ord-1", session_id="s1", venue_order_id=None,
            market="SOL-PERP", side="long", order_type="market",
            size=10.0, price=0.0, state="pending", retry_count=0,
            tx_sig=None, created_at=now, updated_at=now,
            state_history='[["pending", ' + str(now) + ']]',
        )
        orders = store.get_live_orders("s1")
        assert len(orders) == 1
        assert orders[0]["order_id"] == "ord-1"
        assert orders[0]["state"] == "pending"

    def test_upsert_updates_existing(self, store):
        now = int(time.time())
        store.upsert_live_order(
            order_id="ord-2", session_id="s1", venue_order_id=None,
            market="SOL-PERP", side="long", order_type="market",
            size=10.0, price=0.0, state="pending",
            retry_count=0, tx_sig=None, created_at=now, updated_at=now,
            state_history="[]",
        )
        store.upsert_live_order(
            order_id="ord-2", session_id="s1", venue_order_id=42,
            market="SOL-PERP", side="long", order_type="market",
            size=10.0, price=0.0, state="submitted",
            retry_count=1, tx_sig="abc123", created_at=now, updated_at=now + 1,
            state_history='[["pending", ' + str(now) + '], ["submitted", ' + str(now + 1) + ']]',
        )
        orders = store.get_live_orders("s1")
        assert len(orders) == 1
        assert orders[0]["state"] == "submitted"
        assert orders[0]["venue_order_id"] == 42


class TestLiveFills:
    def test_insert_and_query(self, store):
        now = int(time.time())
        store.insert_live_fill(
            fill_id="f1", order_id="ord-1", session_id="s1",
            market="SOL-PERP", side="long", price=150.25, size=10.0,
            fee=0.15, tx_sig="tx_abc", venue="drift", is_partial=False, ts=now,
        )
        fills = store.get_live_fills("s1")
        assert len(fills) == 1
        assert fills[0]["price"] == 150.25
        assert fills[0]["tx_sig"] == "tx_abc"

    def test_query_by_market(self, store):
        now = int(time.time())
        store.insert_live_fill(
            fill_id="f2", order_id="o1", session_id="s1",
            market="SOL-PERP", side="long", price=150.0, size=5.0,
            fee=0.05, tx_sig="tx1", venue="drift", is_partial=False, ts=now,
        )
        store.insert_live_fill(
            fill_id="f3", order_id="o2", session_id="s1",
            market="BTC-PERP", side="short", price=65000.0, size=0.1,
            fee=0.65, tx_sig="tx2", venue="drift", is_partial=False, ts=now,
        )
        sol_fills = store.get_live_fills("s1", market="SOL-PERP")
        assert len(sol_fills) == 1
        assert sol_fills[0]["market"] == "SOL-PERP"


class TestLiveEquityHistory:
    def test_insert_and_query(self, store):
        now = int(time.time())
        store.insert_live_equity("s1", now, 10500.0, 10200.0, 300.0)
        store.insert_live_equity("s1", now + 60, 10550.0, 10200.0, 350.0)
        history = store.get_live_equity_history("s1")
        assert len(history) == 2
        assert history[0]["equity"] == 10500.0
        assert history[1]["equity"] == 10550.0
