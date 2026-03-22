"""Tests for LiveDriftContext — mocked, no real Drift connection."""
from __future__ import annotations

import pytest
from unittest.mock import patch

from flint.models import Side, OrderType


class TestLiveDriftContextImport:
    def test_import_fails_without_driftpy(self):
        """LiveDriftContext should raise ImportError when driftpy not installed."""
        with patch("flint.execution.drift_live._check_driftpy", return_value=False):
            from flint.execution.drift_live import LiveDriftContext
            with pytest.raises(ImportError, match="driftpy is required"):
                LiveDriftContext(private_key="fake_key")

    def test_no_key_raises(self):
        """Should raise ValueError when no private key is provided."""
        with patch("flint.execution.drift_live._check_driftpy", return_value=True):
            from flint.execution.drift_live import LiveDriftContext
            with pytest.raises(ValueError, match="No private key"):
                LiveDriftContext(private_key="")

    def test_creates_with_key(self):
        """Should create context when key is provided and driftpy available."""
        with patch("flint.execution.drift_live._check_driftpy", return_value=True):
            from flint.execution.drift_live import LiveDriftContext
            ctx = LiveDriftContext(private_key="fake_base58_key_for_testing")
            assert ctx.timestamp > 0
            assert ctx.positions == []
            assert ctx.pending_orders == []

    def test_market_order_queues(self):
        """Market orders should queue in pending."""
        with patch("flint.execution.drift_live._check_driftpy", return_value=True):
            from flint.execution.drift_live import LiveDriftContext
            ctx = LiveDriftContext(private_key="fake_key")
            oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
            assert len(ctx.pending_orders) == 1
            assert ctx.pending_orders[0].order_id == oid

    def test_cancel_order(self):
        with patch("flint.execution.drift_live._check_driftpy", return_value=True):
            from flint.execution.drift_live import LiveDriftContext
            ctx = LiveDriftContext(private_key="fake_key")
            oid = ctx.limit_order("SOL-PERP", Side.LONG, 10.0, 150.0)
            assert ctx.cancel(oid) is True
            assert len(ctx.pending_orders) == 0

    def test_cancel_all(self):
        with patch("flint.execution.drift_live._check_driftpy", return_value=True):
            from flint.execution.drift_live import LiveDriftContext
            ctx = LiveDriftContext(private_key="fake_key")
            ctx.market_order("SOL-PERP", Side.LONG, 5.0)
            ctx.market_order("BTC-PERP", Side.SHORT, 0.1)
            assert ctx.cancel_all() == 2

    def test_market_index_mapping(self):
        from flint.execution.drift_live import MARKET_TO_INDEX
        assert MARKET_TO_INDEX["SOL-PERP"] == 0
        assert MARKET_TO_INDEX["BTC-PERP"] == 1
        assert MARKET_TO_INDEX["ETH-PERP"] == 2
        assert len(MARKET_TO_INDEX) >= 30


class TestCLILiveCommand:
    def test_live_help(self):
        from typer.testing import CliRunner
        from flint.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["live", "--help"])
        assert result.exit_code == 0
        assert "paper" in result.output.lower() or "PAPER" in result.output
