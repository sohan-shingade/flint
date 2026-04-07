"""Tests for the /api/v1/data/borrow-rates endpoint."""
import os
import tempfile
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from flint.models import BorrowSnapshot


def _make_client_with_store():
    """Create a TestClient with a real FlintStore injected into app.state."""
    from flint.store import FlintStore

    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)  # DuckDB needs a fresh path
    store = FlintStore(path)

    with patch("flint.api.main.load_config") as mock_cfg, \
         patch("flint.api.main.FlintStore", return_value=store), \
         patch("flint.api.main.CollectorService"), \
         patch("flint.api.main.PaperTradingEngine") as mock_pe, \
         patch("flint.api.main.PriceTicker") as mock_pt:
        mock_cfg.return_value = MagicMock(
            db_path=path,
            collector_enabled=False,
            max_concurrent_backtests=1,
            default_markets=["SOL-PERP"],
            cors_origins=["http://localhost:5173"],
        )
        mock_pe.return_value = MagicMock()
        mock_pt.return_value = MagicMock()
        from flint.api.main import app
        app.state.store = store
        client = TestClient(app)

    return client, store, path


def test_borrow_rates_endpoint():
    client, store, path = _make_client_with_store()
    try:
        store.upsert_borrow_rates([
            BorrowSnapshot("SOL-PERP", 1000, 0.00008, 0.65, 1.001, "rpc"),
            BorrowSnapshot("SOL-PERP", 2000, 0.00009, 0.70, 1.002, "rpc"),
        ])
        resp = client.get("/api/v1/data/borrow-rates", params={"market": "SOL-PERP"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["market"] == "SOL-PERP"
        assert data["count"] == 2
        assert len(data["rates"]) == 2
        assert data["rates"][0]["ts"] == 1000
        assert data["rates"][0]["rate_hourly"] == 0.00008
        assert data["rates"][1]["ts"] == 2000
    finally:
        store.close()
        if os.path.exists(path):
            os.unlink(path)


def test_borrow_rates_empty():
    client, store, path = _make_client_with_store()
    try:
        resp = client.get("/api/v1/data/borrow-rates", params={"market": "SOL-PERP"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["rates"] == []
    finally:
        store.close()
        if os.path.exists(path):
            os.unlink(path)


def test_borrow_rates_no_store():
    """When store is absent from app.state, endpoint returns empty result."""
    with patch("flint.api.main.load_config") as mock_cfg, \
         patch("flint.api.main.FlintStore"), \
         patch("flint.api.main.CollectorService"), \
         patch("flint.api.main.PaperTradingEngine") as mock_pe, \
         patch("flint.api.main.PriceTicker") as mock_pt:
        mock_cfg.return_value = MagicMock(
            db_path=":memory:",
            collector_enabled=False,
            max_concurrent_backtests=1,
            default_markets=["SOL-PERP"],
            cors_origins=["http://localhost:5173"],
        )
        mock_pe.return_value = MagicMock()
        mock_pt.return_value = MagicMock()
        from flint.api.main import app
        # Remove store from state so _get_store returns None
        if hasattr(app.state, "store"):
            del app.state.store
        client = TestClient(app)

    resp = client.get("/api/v1/data/borrow-rates", params={"market": "SOL-PERP"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["market"] == "SOL-PERP"
