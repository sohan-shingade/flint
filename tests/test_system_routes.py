"""Tests for /api/v1/system/ endpoints."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


def _make_app(candle_count: int = 0):
    mock_store = MagicMock()
    mock_store._conn = MagicMock()
    mock_store._lock = MagicMock()
    mock_store._conn.execute.return_value.fetchone.return_value = (candle_count,)

    with patch("flint.api.main.load_config") as mock_cfg, \
         patch("flint.api.main.FlintStore", return_value=mock_store), \
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
        # Explicitly set store on app.state so the endpoint sees the right mock
        app.state.store = mock_store
        client = TestClient(app)
    return client, mock_store


def test_system_status_uninitialized():
    client, _ = _make_app(candle_count=0)
    resp = client.get("/api/v1/system/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["initialized"] is False
    assert data["version"] == "0.3.0"


def test_system_status_initialized():
    client, _ = _make_app(candle_count=500)
    resp = client.get("/api/v1/system/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["initialized"] is True
    assert data["version"] == "0.3.0"


def test_system_config_saves_api_keys(tmp_path):
    client, _ = _make_app()
    env_file = tmp_path / ".env"
    with patch("flint.api.routes.system._get_env_path", return_value=str(env_file)):
        resp = client.post("/api/v1/system/config", json={
            "birdeye_api_key": "bird123",
            "helius_api_key": "hel456",
        })
    assert resp.status_code == 200
    assert resp.json()["saved"] is True
    content = env_file.read_text()
    assert "FLINT_BIRDEYE_API_KEY=bird123" in content
    assert "FLINT_HELIUS_API_KEY=hel456" in content


def test_system_config_preserves_existing_keys(tmp_path):
    client, _ = _make_app()
    env_file = tmp_path / ".env"
    env_file.write_text("MY_OTHER_KEY=keepme\nFLINT_BIRDEYE_API_KEY=old\n")
    with patch("flint.api.routes.system._get_env_path", return_value=str(env_file)):
        resp = client.post("/api/v1/system/config", json={
            "birdeye_api_key": "new_bird",
        })
    assert resp.status_code == 200
    content = env_file.read_text()
    assert "MY_OTHER_KEY=keepme" in content
    assert "FLINT_BIRDEYE_API_KEY=new_bird" in content


def test_system_config_skips_empty_values(tmp_path):
    client, _ = _make_app()
    env_file = tmp_path / ".env"
    with patch("flint.api.routes.system._get_env_path", return_value=str(env_file)):
        resp = client.post("/api/v1/system/config", json={
            "birdeye_api_key": "",
            "helius_api_key": "hel789",
        })
    assert resp.status_code == 200
    content = env_file.read_text()
    assert "FLINT_BIRDEYE_API_KEY" not in content
    assert "FLINT_HELIUS_API_KEY=hel789" in content
