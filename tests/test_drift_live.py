"""Tests for LiveDriftContext — mocked, no real Drift connection."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock

from flint.models import Side, OrderType, OrderState, PositionInfo


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestLiveDriftContextImport:
    def test_import_fails_without_driftpy(self):
        with patch("flint.execution.drift_live._check_driftpy", return_value=False):
            from flint.execution.drift_live import LiveDriftContext
            with pytest.raises(ImportError, match="driftpy is required"):
                LiveDriftContext(private_key="fake_key")

    def test_no_key_raises(self):
        with patch("flint.execution.drift_live._check_driftpy", return_value=True):
            from flint.execution.drift_live import LiveDriftContext
            with pytest.raises(ValueError, match="No private key"):
                LiveDriftContext(private_key="")

    def test_creates_with_key(self):
        with patch("flint.execution.drift_live._check_driftpy", return_value=True):
            with patch("flint.execution.drift_live.KeypairAdapter") as MockAdapter:
                MockAdapter.return_value = MagicMock()
                from flint.execution.drift_live import LiveDriftContext
                ctx = LiveDriftContext(private_key="fake_base58_key_for_testing")
                assert ctx.timestamp > 0
                assert ctx.positions == []


class TestMarketMapping:
    def test_market_to_index(self):
        from flint.execution.drift_live import MARKET_TO_INDEX, INDEX_TO_MARKET
        assert MARKET_TO_INDEX["SOL-PERP"] == 0
        assert MARKET_TO_INDEX["BTC-PERP"] == 1
        assert INDEX_TO_MARKET[0] == "SOL-PERP"
        assert INDEX_TO_MARKET[1] == "BTC-PERP"


class TestOrderMethods:
    def _make_ctx(self):
        with patch("flint.execution.drift_live._check_driftpy", return_value=True):
            with patch("flint.execution.drift_live.KeypairAdapter") as MockAdapter:
                MockAdapter.return_value = MagicMock()
                from flint.execution.drift_live import LiveDriftContext
                return LiveDriftContext(private_key="fake_key", network="devnet")

    def test_market_order_queues(self):
        ctx = self._make_ctx()
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        assert oid != ""
        assert len(ctx._tracker.active_orders) == 1

    def test_limit_order_queues(self):
        ctx = self._make_ctx()
        oid = ctx.limit_order("SOL-PERP", Side.LONG, 10.0, 150.0)
        tracked = ctx._tracker.get(oid)
        assert tracked.order.order_type == OrderType.LIMIT
        assert tracked.order.price == 150.0

    def test_cancel_order(self):
        ctx = self._make_ctx()
        oid = ctx.limit_order("SOL-PERP", Side.LONG, 10.0, 150.0)
        assert ctx.cancel(oid) is True
        tracked = ctx._tracker.get(oid)
        assert tracked.state == OrderState.CANCELLED

    def test_cancel_all(self):
        ctx = self._make_ctx()
        ctx.market_order("SOL-PERP", Side.LONG, 5.0)
        ctx.market_order("BTC-PERP", Side.SHORT, 0.1)
        assert ctx.cancel_all() == 2


class TestNetworkConfig:
    def test_devnet_rpc(self):
        with patch("flint.execution.drift_live._check_driftpy", return_value=True):
            with patch("flint.execution.drift_live.KeypairAdapter") as MockAdapter:
                MockAdapter.return_value = MagicMock()
                from flint.execution.drift_live import LiveDriftContext
                ctx = LiveDriftContext(private_key="fake", network="devnet")
                assert "devnet" in ctx._rpc_url

    def test_mainnet_rpc(self):
        with patch("flint.execution.drift_live._check_driftpy", return_value=True):
            with patch("flint.execution.drift_live.KeypairAdapter") as MockAdapter:
                MockAdapter.return_value = MagicMock()
                from flint.execution.drift_live import LiveDriftContext
                ctx = LiveDriftContext(private_key="fake", network="mainnet")
                assert "mainnet" in ctx._rpc_url

    def test_rpc_url_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_RPC_URL", "https://custom-rpc.example.com")
        with patch("flint.execution.drift_live._check_driftpy", return_value=True):
            with patch("flint.execution.drift_live.KeypairAdapter") as MockAdapter:
                MockAdapter.return_value = MagicMock()
                from flint.execution.drift_live import LiveDriftContext
                ctx = LiveDriftContext(private_key="fake", network="devnet")
                assert ctx._rpc_url == "https://custom-rpc.example.com"


class TestCLILiveCommand:
    def test_live_help(self):
        from typer.testing import CliRunner
        from flint.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["live", "--help"])
        assert result.exit_code == 0
