"""Tests for store calibration query methods."""
from flint.store import FlintStore


class TestQueryLiveFillsByVenue:
    def test_returns_fills_for_venue_and_market(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        store.insert_live_fill(fill_id="f1", order_id="o1", session_id="s1",
            market="SOL-PERP", side="long", price=150.0, size=10.0,
            fee=0.075, tx_sig="tx1", venue="drift", is_partial=False, ts=1000)
        store.insert_live_fill(fill_id="f2", order_id="o2", session_id="s1",
            market="SOL-PERP", side="short", price=151.0, size=5.0,
            fee=0.04, tx_sig="tx2", venue="drift", is_partial=False, ts=1060)
        store.insert_live_fill(fill_id="f3", order_id="o3", session_id="s2",
            market="SOL-PERP", side="long", price=149.0, size=8.0,
            fee=0.06, tx_sig="tx3", venue="hyperliquid", is_partial=False, ts=1120)

        fills = store.query_live_fills_by_venue("drift", "SOL-PERP")
        assert len(fills) == 2
        assert all(f["venue"] == "drift" for f in fills)

        fills_hl = store.query_live_fills_by_venue("hyperliquid", "SOL-PERP")
        assert len(fills_hl) == 1

        fills_range = store.query_live_fills_by_venue("drift", "SOL-PERP", start_ts=1050)
        assert len(fills_range) == 1
        assert fills_range[0]["ts"] == 1060
        store.close()
