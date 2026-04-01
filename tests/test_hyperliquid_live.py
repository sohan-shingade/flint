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


class TestPlaceOrder:
    def test_market_order_uses_ioc_with_slippage(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        from flint.connectors.hyperliquid import HyperliquidClient
        from flint.models import Candle, Order
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")

        mock_client = MagicMock(spec=HyperliquidClient)
        mock_client.address = "0x1234"
        mock_client._coin_to_asset_index = {"SOL": 2}
        mock_client._asset_info = {"SOL": {"szDecimals": 2}}
        mock_client.format_size.return_value = "10.00"
        mock_client.format_price.return_value = "150.45"
        mock_client.place_order = AsyncMock(return_value={
            "status": "ok",
            "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": 99}}]}},
        })
        mock_client.parse_order_id = HyperliquidClient.parse_order_id
        ctx._client = mock_client
        ctx._current_candle = Candle(ts=1000, open=150.0, high=151.0, low=149.0, close=150.0, volume=100.0, market="SOL-PERP", resolution_s=60)

        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET, size=10.0, order_id="test-1", ts=1000)
        tx_sig, venue_oid = run(ctx._place_order(order))
        assert venue_oid == 99
        call_args = mock_client.place_order.call_args
        assert call_args.kwargs["order_type"] == {"limit": {"tif": "Ioc"}}

    def test_limit_order_uses_gtc(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        from flint.connectors.hyperliquid import HyperliquidClient
        from flint.models import Order
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")

        mock_client = MagicMock(spec=HyperliquidClient)
        mock_client._coin_to_asset_index = {"SOL": 2}
        mock_client._asset_info = {"SOL": {"szDecimals": 2}}
        mock_client.format_size.return_value = "10.00"
        mock_client.format_price.return_value = "150.00"
        mock_client.place_order = AsyncMock(return_value={
            "status": "ok",
            "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": 50}}]}},
        })
        mock_client.parse_order_id = HyperliquidClient.parse_order_id
        ctx._client = mock_client

        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.LIMIT, size=10.0, price=150.0, order_id="test-2", ts=1000)
        tx_sig, venue_oid = run(ctx._place_order(order))
        assert venue_oid == 50
        call_args = mock_client.place_order.call_args
        assert call_args.kwargs["order_type"] == {"limit": {"tif": "Gtc"}}

    def test_stop_order_uses_trigger(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        from flint.connectors.hyperliquid import HyperliquidClient
        from flint.models import Order
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")

        mock_client = MagicMock(spec=HyperliquidClient)
        mock_client._coin_to_asset_index = {"SOL": 2}
        mock_client._asset_info = {"SOL": {"szDecimals": 2}}
        mock_client.format_size.return_value = "5.00"
        mock_client.format_price.return_value = "140.00"
        mock_client.place_order = AsyncMock(return_value={
            "status": "ok",
            "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": 60}}]}},
        })
        mock_client.parse_order_id = HyperliquidClient.parse_order_id
        ctx._client = mock_client

        order = Order(market="SOL-PERP", side=Side.SHORT, order_type=OrderType.STOP_LOSS, size=5.0, price=140.0, order_id="test-3", ts=1000)
        tx_sig, venue_oid = run(ctx._place_order(order))
        call_args = mock_client.place_order.call_args
        assert "trigger" in call_args.kwargs["order_type"]


class TestFetchPositions:
    def test_parses_clearinghouse_positions(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")

        mock_client = MagicMock()
        mock_client.address = "0x1234"
        mock_client.get_clearinghouse_state = AsyncMock(return_value={
            "marginSummary": {"accountValue": "10000.0"},
            "assetPositions": [
                {"position": {"coin": "SOL", "szi": "10.0", "entryPx": "150.0", "unrealizedPnl": "50.0"}},
                {"position": {"coin": "BTC", "szi": "-0.5", "entryPx": "65000.0", "unrealizedPnl": "-100.0"}},
            ],
        })
        ctx._client = mock_client
        positions = run(ctx._fetch_positions())
        assert len(positions) == 2
        sol_pos = next(p for p in positions if p.market == "SOL-PERP")
        assert sol_pos.side == Side.LONG
        assert sol_pos.size == 10.0
        btc_pos = next(p for p in positions if p.market == "BTC-PERP")
        assert btc_pos.side == Side.SHORT
        assert btc_pos.size == 0.5

    def test_skips_zero_positions(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")
        mock_client = MagicMock()
        mock_client.address = "0x1234"
        mock_client.get_clearinghouse_state = AsyncMock(return_value={
            "marginSummary": {"accountValue": "10000.0"},
            "assetPositions": [{"position": {"coin": "SOL", "szi": "0.0", "entryPx": "0", "unrealizedPnl": "0"}}],
        })
        ctx._client = mock_client
        positions = run(ctx._fetch_positions())
        assert len(positions) == 0


class TestFetchBalance:
    def test_returns_account_value(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")
        mock_client = MagicMock()
        mock_client.address = "0x1234"
        mock_client.get_clearinghouse_state = AsyncMock(return_value={
            "marginSummary": {"accountValue": "12345.67"},
            "assetPositions": [],
        })
        ctx._client = mock_client
        balance = run(ctx._fetch_balance())
        assert balance == 12345.67


class TestCancelOrder:
    def test_cancel_calls_client(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")
        mock_client = MagicMock()
        mock_client.cancel_order = AsyncMock(return_value={"status": "ok"})
        ctx._client = mock_client
        ctx._venue_order_to_asset = {42: 2}
        result = run(ctx._cancel_order(42))
        assert result is True


class TestPollOrderStatus:
    def test_order_in_open_orders_returns_confirmed(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")
        mock_client = MagicMock()
        mock_client.address = "0x1234"
        mock_client.get_open_orders = AsyncMock(return_value=[{"oid": 42, "coin": "SOL"}])
        ctx._client = mock_client
        status = run(ctx._poll_order_status(42))
        assert status == OrderState.CONFIRMED

    def test_order_not_in_open_with_fill_returns_filled(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")
        mock_client = MagicMock()
        mock_client.address = "0x1234"
        mock_client.get_open_orders = AsyncMock(return_value=[])
        mock_client.get_user_fills = AsyncMock(return_value=[{"oid": 42, "px": "150.0"}])
        ctx._client = mock_client
        status = run(ctx._poll_order_status(42))
        assert status == OrderState.FILLED

    def test_order_not_in_open_or_fills_returns_cancelled(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")
        mock_client = MagicMock()
        mock_client.address = "0x1234"
        mock_client.get_open_orders = AsyncMock(return_value=[])
        mock_client.get_user_fills = AsyncMock(return_value=[])
        ctx._client = mock_client
        status = run(ctx._poll_order_status(42))
        assert status == OrderState.CANCELLED
