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
