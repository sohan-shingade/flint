"""Tests for LiveHyperliquidContext — mocked, no real connections."""
import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from flint.models import Side, OrderType, OrderState


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestConstruction:
    def test_creates_with_key(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")
        assert ctx._venue == "hyperliquid"
        assert ctx.timestamp > 0
        assert ctx.positions == []

    def test_no_key_raises(self, monkeypatch):
        monkeypatch.delenv("FLINT_HYPERLIQUID_PRIVATE_KEY", raising=False)
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        with pytest.raises(ValueError, match="No private key"):
            LiveHyperliquidContext(private_key="", network="testnet")

    def test_key_from_env(self, monkeypatch):
        monkeypatch.setenv("FLINT_HYPERLIQUID_PRIVATE_KEY", "0x" + "cd" * 32)
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(network="testnet")
        assert ctx._venue == "hyperliquid"

    def test_testnet_default(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32)
        assert ctx._network == "testnet"

    def test_mainnet_network(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="mainnet")
        assert ctx._network == "mainnet"


class TestMarketMapping:
    def test_flint_to_hl_symbol(self):
        from flint.execution.hyperliquid_live import FLINT_TO_HL
        assert FLINT_TO_HL["SOL-PERP"] == "SOL"
        assert FLINT_TO_HL["BTC-PERP"] == "BTC"

    def test_hl_to_flint_symbol(self):
        from flint.execution.hyperliquid_live import HL_TO_FLINT
        assert HL_TO_FLINT["SOL"] == "SOL-PERP"
        assert HL_TO_FLINT["BTC"] == "BTC-PERP"


class TestOrderQueuing:
    def _make_ctx(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        return LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")

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

    def test_stop_order_queues(self):
        ctx = self._make_ctx()
        oid = ctx.stop_order("SOL-PERP", Side.SHORT, 5.0, 140.0)
        tracked = ctx._tracker.get(oid)
        assert tracked.order.order_type == OrderType.STOP_LOSS

    def test_cancel_all(self):
        ctx = self._make_ctx()
        ctx.market_order("SOL-PERP", Side.LONG, 5.0)
        ctx.market_order("BTC-PERP", Side.SHORT, 0.1)
        assert ctx.cancel_all() == 2
