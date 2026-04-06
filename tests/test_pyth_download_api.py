"""Tests for Pyth-as-sole-price-source download endpoint refactor."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from flint.models import Candle


@pytest.fixture
def store(tmp_path):
    from flint.store import FlintStore
    s = FlintStore(str(tmp_path / "test.duckdb"))
    yield s
    s.close()


@pytest.fixture
def app_client(store):
    """FastAPI test client with a real store, bypassing lifespan's own store creation."""
    from flint.api.main import app

    with patch("flint.api.main.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            collector_enabled=False,
            db_path=":memory:",
            max_concurrent_backtests=5,
            default_markets=[],
        )
        with TestClient(app) as c:
            # Override the lifespan-created store with our test store
            app.state.store = store
            yield c


def _mock_candles(market: str = "SOL-PERP") -> list[Candle]:
    return [
        Candle(
            ts=1700000000,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000.0,
            market=market,
            resolution_s=3600,
            venue="pyth",
        ),
    ]


def test_download_uses_pyth_by_default(app_client):
    """POST /download should call _download_pyth_candles and return source='pyth'."""
    mock_candles = _mock_candles()
    with patch("flint.api.routes.data._download_pyth_candles", return_value=(mock_candles, None)), \
         patch("flint.api.routes.data._download_funding_all_venues", return_value=0):
        resp = app_client.post("/api/v1/data/download", json={
            "market": "SOL-PERP",
            "start_ts": 1700000000,
            "end_ts": 1700007200,
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("source") == "pyth"
    assert data.get("downloaded") == 1


def test_execution_venues_param_accepted(app_client):
    """execution_venues should be accepted without error."""
    with patch("flint.api.routes.data._download_pyth_candles", return_value=([], None)), \
         patch("flint.api.routes.data._download_funding_all_venues", return_value=0):
        resp = app_client.post("/api/v1/data/download", json={
            "market": "SOL-PERP",
            "start_ts": 1700000000,
            "end_ts": 1700007200,
            "execution_venues": ["drift", "hyperliquid"],
        })
    assert resp.status_code == 200


def test_funding_venues_alias_works(app_client):
    """funding_venues should be accepted as an alias for execution_venues."""
    with patch("flint.api.routes.data._download_pyth_candles", return_value=([], None)), \
         patch("flint.api.routes.data._download_funding_all_venues", return_value=0):
        resp = app_client.post("/api/v1/data/download", json={
            "market": "SOL-PERP",
            "start_ts": 1700000000,
            "end_ts": 1700007200,
            "funding_venues": ["drift"],
        })
    assert resp.status_code == 200


def test_deprecated_venue_param_logs_warning(app_client, caplog):
    """Passing 'venue' should log a deprecation warning but still succeed."""
    import logging
    mock_candles = _mock_candles()
    with patch("flint.api.routes.data._download_pyth_candles", return_value=(mock_candles, None)), \
         patch("flint.api.routes.data._download_funding_all_venues", return_value=0), \
         caplog.at_level(logging.WARNING, logger="flint.api.data"):
        resp = app_client.post("/api/v1/data/download", json={
            "market": "SOL-PERP",
            "start_ts": 1700000000,
            "end_ts": 1700007200,
            "venue": "drift",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("source") == "pyth"
    # Deprecation warning should have been issued
    assert any("deprecated" in record.message.lower() for record in caplog.records)


def test_download_range_helpers_still_exist():
    """_download_range and _download_range_for_venue must not be removed."""
    from flint.api.routes import data as data_module
    assert callable(getattr(data_module, "_download_range", None)), \
        "_download_range must remain in data.py"
    assert callable(getattr(data_module, "_download_range_for_venue", None)), \
        "_download_range_for_venue must remain in data.py"


def test_execution_venues_forwarded_to_funding(app_client, store):
    """execution_venues list should be passed through to _download_funding_all_venues."""
    mock_candles = _mock_candles()
    with patch("flint.api.routes.data._download_pyth_candles", return_value=(mock_candles, None)) as _pyth, \
         patch("flint.api.routes.data._download_funding_all_venues", return_value=5) as mock_funding:
        resp = app_client.post("/api/v1/data/download", json={
            "market": "SOL-PERP",
            "start_ts": 1700000000,
            "end_ts": 1700007200,
            "execution_venues": ["drift", "hyperliquid"],
        })
    assert resp.status_code == 200
    # The venues list must have been forwarded to the funding helper
    call_kwargs = mock_funding.call_args
    venues_arg = call_kwargs.kwargs.get("venues") or (
        call_kwargs.args[4] if len(call_kwargs.args) > 4 else None
    )
    assert venues_arg == ["drift", "hyperliquid"]


def test_cached_data_returns_pyth_source(app_client, store):
    """When candles are already cached, the response source should still be 'local' (unchanged)."""
    from flint.models import Candle
    candles = [
        Candle(
            ts=1700000000 + i * 3600,
            open=100.0, high=101.0, low=99.0, close=100.5, volume=1000.0,
            market="BTC-PERP", resolution_s=3600,
        )
        for i in range(3)
    ]
    store.upsert_candles(candles)

    with patch("flint.api.routes.data._download_pyth_candles") as mock_pyth, \
         patch("flint.api.routes.data._download_funding_all_venues", return_value=0):
        resp = app_client.post("/api/v1/data/download", json={
            "market": "BTC-PERP",
            "start_ts": 1700000000,
            "end_ts": 1700010800,
        })
    assert resp.status_code == 200
    data = resp.json()
    # Skipped — data was already cached
    assert data.get("skipped") is True
    assert data.get("source") == "local"
    # Pyth should NOT have been called
    mock_pyth.assert_not_called()
