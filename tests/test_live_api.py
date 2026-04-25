"""Tests for live API endpoints."""
from flint.store import FlintStore


class TestLiveFillsStore:
    def test_fills_by_session(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        store.insert_live_fill(
            fill_id="f1", order_id="o1", session_id="s1",
            market="SOL-PERP", side="long", price=150.0, size=10.0,
            fee=0.075, tx_sig="tx1", venue="drift", is_partial=False, ts=1000,
        )
        fills = store.get_live_fills("s1")
        assert len(fills) == 1
        assert fills[0]["market"] == "SOL-PERP"
        store.close()

    def test_equity_insert_and_query(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        store.insert_live_equity("s1", 1000, 10500.0, 10000.0, 500.0)
        store.insert_live_equity("s1", 1060, 10600.0, 10000.0, 600.0)
        with store._lock:
            rows = store._conn.execute(
                "SELECT ts, equity FROM live_equity_history WHERE session_id = ? ORDER BY ts",
                ["s1"]
            ).fetchall()
        assert len(rows) == 2
        assert rows[1][1] == 10600.0
        store.close()


class TestLiveRouterImport:
    def test_router_importable(self):
        from flint.api.routes.live import router
        assert router.prefix == "/api/v1/live"
