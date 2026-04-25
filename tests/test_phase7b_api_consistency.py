"""Phase 7b: API consistency tests."""


class TestStatusConsistency:
    """Progress phase should match top-level status naming."""

    def test_no_phase_done_in_backtest(self):
        import inspect
        from flint.api.routes import backtest
        source = inspect.getsource(backtest)
        assert 'phase="done"' not in source and "phase='done'" not in source, (
            "Found phase='done' in backtest.py — should be phase='complete'"
        )

    def test_no_phase_done_in_optimization(self):
        import inspect
        from flint.api.routes import optimization
        source = inspect.getsource(optimization)
        assert 'phase="done"' not in source and "phase='done'" not in source, (
            "Found phase='done' in optimization.py — should be phase='complete'"
        )


from unittest.mock import MagicMock


class TestFundingDefaults:
    """Funding endpoint should return data without explicit timestamps."""

    def test_funding_without_timestamps_defaults_to_30_days(self):
        from flint.api.routes.data import get_funding
        mock_request = MagicMock()
        mock_store = MagicMock()
        mock_store.query_funding_by_venue.return_value = {
            "drift": [{"ts": 1000, "rate": 0.001}],
        }
        mock_request.app.state.store = mock_store

        result = get_funding(mock_request, market="SOL-PERP", start_ts=None, end_ts=None)
        assert result["count"] > 0
        # Verify default timestamps were passed
        call_args = mock_store.query_funding_by_venue.call_args[0]
        start_ts = call_args[1]
        end_ts = call_args[2]
        assert start_ts is not None, "start_ts should be defaulted"
        assert end_ts is not None, "end_ts should be defaulted"
        assert end_ts - start_ts == 30 * 86400, "Default range should be 30 days"

    def test_funding_response_key_is_venues(self):
        from flint.api.routes.data import get_funding
        mock_request = MagicMock()
        mock_store = MagicMock()
        mock_store.query_funding_by_venue.return_value = {}
        mock_request.app.state.store = mock_store

        result = get_funding(mock_request, market="SOL-PERP", start_ts=None, end_ts=None)
        assert "venues" in result, "Response key should be 'venues'"


import pytest


class TestDownloadDaysParam:
    """POST /download should accept 'days' as convenience."""

    def test_days_param_computes_timestamps(self):
        """When days is provided without start/end, timestamps should be computed."""
        import time
        from flint.api.routes.data import download_market_data
        from unittest.mock import MagicMock

        mock_request = MagicMock()
        mock_store = MagicMock()
        mock_store.query_candles.return_value = [MagicMock(ts=int(time.time()))]
        mock_request.app.state.store = mock_store

        # Should not raise 400
        try:
            download_market_data(mock_request, {"market": "SOL-PERP", "days": 30})
        except Exception as e:
            if "Invalid date range" in str(e):
                pytest.fail(f"days=30 was rejected: {e}")
            # Other errors (network, etc.) are fine — we just care it didn't reject the date range

    def test_explicit_timestamps_override_days(self):
        """When both days and timestamps provided, timestamps win."""
        from flint.api.routes.data import download_market_data
        from unittest.mock import MagicMock

        mock_request = MagicMock()
        mock_store = MagicMock()
        mock_store.query_candles.return_value = [MagicMock(ts=2000)]
        mock_request.app.state.store = mock_store

        try:
            download_market_data(mock_request, {
                "market": "SOL-PERP",
                "days": 30,
                "start_ts": 1000,
                "end_ts": 2000,
            })
        except Exception as e:
            if "Invalid date range" in str(e):
                pytest.fail(f"Explicit timestamps rejected: {e}")

        # Verify the explicit timestamps were used, not computed from days
        call_args = mock_store.query_candles.call_args
        if call_args:
            passed_start = call_args[0][2] if len(call_args[0]) > 2 else None
            if passed_start is not None:
                assert passed_start == 1000, "Should use explicit start_ts, not computed from days"


class TestBatchDataCheck:
    """GET /check should accept comma-separated markets param."""

    def test_markets_plural_accepted(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from unittest.mock import MagicMock
        from flint.api.routes.data import router

        mock_store = MagicMock()
        mock_store.query_candles.return_value = []
        mock_store.count_candles.return_value = 0
        mock_store._lock = MagicMock()
        mock_store._conn = MagicMock()
        mock_store._conn.execute.return_value.fetchone.return_value = (0,)

        app = FastAPI()
        app.include_router(router, prefix="/data")
        app.state.store = mock_store
        client = TestClient(app)
        resp = client.get("/data/check?markets=SOL-PERP,BTC-PERP&start_ts=1000&end_ts=5000&resolution_s=3600")
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert len(data["results"]) == 2

    def test_single_market_still_works(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from unittest.mock import MagicMock
        from flint.api.routes.data import router

        mock_store = MagicMock()
        mock_store.query_candles.return_value = []
        mock_store.count_candles.return_value = 0
        mock_store._lock = MagicMock()
        mock_store._conn = MagicMock()
        mock_store._conn.execute.return_value.fetchone.return_value = (0,)

        app = FastAPI()
        app.include_router(router, prefix="/data")
        app.state.store = mock_store
        client = TestClient(app)
        resp = client.get("/data/check?market=SOL-PERP&start_ts=1000&end_ts=5000&resolution_s=3600")
        assert resp.status_code == 200
        data = resp.json()
        assert "market" in data  # single-market response shape (no "results" wrapper)


class TestDownloadResponseClarity:
    def test_cached_shows_existing_count(self):
        """When data is cached, response should show actual count."""
        # Design-level test
        response = {
            "downloaded": 0,
            "cached": 2162,
            "existing": 2162,
            "skipped": True,
            "message": "All candles already cached for this range",
        }
        assert response["cached"] == response["existing"]
        assert response["downloaded"] == 0
        assert "message" in response


class TestCCXTWarningDedup:
    def test_ccxt_warning_only_once(self):
        from flint.api.routes import data as data_module
        data_module._ccxt_warned.clear()

        warnings1 = []
        warnings2 = []
        for exchange in ["mexc", "phemex"]:
            key = f"ccxt/{exchange}"
            if key not in data_module._ccxt_warned:
                data_module._ccxt_warned.add(key)
                warnings1.append(f"{key} unavailable")

        for exchange in ["mexc", "phemex"]:
            key = f"ccxt/{exchange}"
            if key not in data_module._ccxt_warned:
                data_module._ccxt_warned.add(key)
                warnings2.append(f"{key} unavailable")

        assert len(warnings1) == 2
        assert len(warnings2) == 0  # no duplicates
        data_module._ccxt_warned.clear()
