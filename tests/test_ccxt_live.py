"""Tests for LiveCCXTContext — CCXT-based CEX live execution."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from flint.models import Order, OrderType, OrderState, Side, PositionInfo
from flint.execution.ccxt_live import (
    LiveCCXTContext, _to_ccxt_symbol, _to_flint_symbol,
)


# ─── Symbol Mapping ──────────────────────────────────────────

class TestSymbolMapping:
    def test_perp_to_ccxt(self):
        assert _to_ccxt_symbol("SOL-PERP") == "SOL/USDT:USDT"
        assert _to_ccxt_symbol("BTC-PERP") == "BTC/USDT:USDT"

    def test_unknown_perp_fallback(self):
        assert _to_ccxt_symbol("UNKNOWN-PERP") == "UNKNOWN/USDT:USDT"

    def test_spot_fallback(self):
        assert _to_ccxt_symbol("SOL") == "SOL/USDT"

    def test_ccxt_to_flint(self):
        assert _to_flint_symbol("SOL/USDT:USDT") == "SOL-PERP"
        assert _to_flint_symbol("BTC/USDT:USDT") == "BTC-PERP"

    def test_unknown_ccxt_fallback(self):
        assert _to_flint_symbol("UNKNOWN/USDT:USDT") == "UNKNOWN-PERP"


# ─── Constructor ─────────────────────────────────────────────

class TestConstructor:
    def test_requires_credentials(self):
        with pytest.raises(ValueError, match="No API credentials"):
            LiveCCXTContext(exchange="binance", api_key="", secret="")

    def test_accepts_credentials(self):
        ctx = LiveCCXTContext(
            exchange="binance", api_key="test_key", secret="test_secret",
            sandbox=True,
        )
        assert ctx._exchange_name == "binance"
        assert ctx._sandbox is True
        assert ctx._venue == "binance"

    def test_env_vars(self):
        with patch.dict("os.environ", {
            "FLINT_CCXT_EXCHANGE": "okx",
            "FLINT_CCXT_API_KEY": "key",
            "FLINT_CCXT_SECRET": "secret",
            "FLINT_CCXT_PASSWORD": "pass",
            "FLINT_CCXT_SANDBOX": "false",
        }):
            ctx = LiveCCXTContext()
            assert ctx._exchange_name == "okx"
            assert ctx._password == "pass"
            assert ctx._sandbox is False


# ─── Connection ──────────────────────────────────────────────

class TestConnection:
    @pytest.mark.asyncio
    async def test_connect_creates_exchange(self):
        ctx = LiveCCXTContext(exchange="binance", api_key="k", secret="s", sandbox=True)
        mock_exchange = AsyncMock()
        mock_exchange.markets = {"SOL/USDT:USDT": {}}
        mock_exchange.load_markets = AsyncMock()

        with patch("ccxt.async_support.binance", return_value=mock_exchange):
            await ctx._connect()

        mock_exchange.set_sandbox_mode.assert_called_once_with(True)
        mock_exchange.load_markets.assert_called_once()
        assert ctx._exchange is mock_exchange

    @pytest.mark.asyncio
    async def test_disconnect(self):
        ctx = LiveCCXTContext(exchange="binance", api_key="k", secret="s")
        ctx._exchange = AsyncMock()
        await ctx._disconnect()
        ctx._exchange is None


# ─── Order Placement ─────────────────────────────────────────

class TestPlaceOrder:
    @pytest.mark.asyncio
    async def test_market_order(self):
        ctx = LiveCCXTContext(exchange="binance", api_key="k", secret="s")
        ctx._exchange = AsyncMock()
        ctx._exchange.create_order = AsyncMock(return_value={"id": "12345"})

        order = Order(
            order_id="test1", market="SOL-PERP", side=Side.LONG,
            order_type=OrderType.MARKET, size=1.0, price=0, ts=0,
        )
        tx_sig, venue_id = await ctx._place_order(order)
        assert tx_sig == "12345"
        assert venue_id is not None
        ctx._exchange.create_order.assert_called_once()
        call_args = ctx._exchange.create_order.call_args
        assert call_args.kwargs["type"] == "market"
        assert call_args.kwargs["side"] == "buy"

    @pytest.mark.asyncio
    async def test_limit_order(self):
        ctx = LiveCCXTContext(exchange="binance", api_key="k", secret="s")
        ctx._exchange = AsyncMock()
        ctx._exchange.create_order = AsyncMock(return_value={"id": "67890"})

        order = Order(
            order_id="test2", market="BTC-PERP", side=Side.SHORT,
            order_type=OrderType.LIMIT, size=0.1, price=50000.0, ts=0,
        )
        tx_sig, venue_id = await ctx._place_order(order)
        assert tx_sig == "67890"
        call_args = ctx._exchange.create_order.call_args
        assert call_args.kwargs["type"] == "limit"
        assert call_args.kwargs["side"] == "sell"
        assert call_args.kwargs["price"] == 50000.0

    @pytest.mark.asyncio
    async def test_not_connected_raises(self):
        ctx = LiveCCXTContext(exchange="binance", api_key="k", secret="s")
        order = Order(
            order_id="test3", market="SOL-PERP", side=Side.LONG,
            order_type=OrderType.MARKET, size=1.0, price=0, ts=0,
        )
        with pytest.raises(RuntimeError, match="Not connected"):
            await ctx._place_order(order)


# ─── Position Fetching ───────────────────────────────────────

class TestFetchPositions:
    @pytest.mark.asyncio
    async def test_fetch_positions(self):
        ctx = LiveCCXTContext(exchange="binance", api_key="k", secret="s")
        ctx._exchange = AsyncMock()
        ctx._exchange.fetch_positions = AsyncMock(return_value=[
            {"symbol": "SOL/USDT:USDT", "side": "long", "contracts": 10.0,
             "entryPrice": 150.0, "unrealizedPnl": 50.0},
            {"symbol": "BTC/USDT:USDT", "side": "short", "contracts": 0.5,
             "entryPrice": 60000.0, "unrealizedPnl": -100.0},
        ])

        positions = await ctx._fetch_positions()
        assert len(positions) == 2
        assert positions[0].market == "SOL-PERP"
        assert positions[0].side == Side.LONG
        assert positions[0].size == 10.0
        assert positions[1].market == "BTC-PERP"
        assert positions[1].side == Side.SHORT

    @pytest.mark.asyncio
    async def test_skips_zero_positions(self):
        ctx = LiveCCXTContext(exchange="binance", api_key="k", secret="s")
        ctx._exchange = AsyncMock()
        ctx._exchange.fetch_positions = AsyncMock(return_value=[
            {"symbol": "SOL/USDT:USDT", "side": "long", "contracts": 0,
             "entryPrice": 150.0, "unrealizedPnl": 0},
        ])
        positions = await ctx._fetch_positions()
        assert len(positions) == 0


# ─── Balance ─────────────────────────────────────────────────

class TestFetchBalance:
    @pytest.mark.asyncio
    async def test_fetch_balance(self):
        ctx = LiveCCXTContext(exchange="binance", api_key="k", secret="s")
        ctx._exchange = AsyncMock()
        ctx._exchange.fetch_balance = AsyncMock(return_value={
            "USDT": {"total": 10500.0, "free": 8000.0},
        })
        balance = await ctx._fetch_balance()
        assert balance == 10500.0

    @pytest.mark.asyncio
    async def test_balance_not_connected(self):
        ctx = LiveCCXTContext(exchange="binance", api_key="k", secret="s")
        balance = await ctx._fetch_balance()
        assert balance == 0.0


# ─── Order Status ────────────────────────────────────────────

class TestPollOrderStatus:
    @pytest.mark.asyncio
    async def test_filled_order(self):
        ctx = LiveCCXTContext(exchange="binance", api_key="k", secret="s")
        ctx._exchange = AsyncMock()
        ctx._venue_order_map["12345"] = "SOL-PERP"
        ctx._exchange.fetch_order = AsyncMock(return_value={"status": "closed"})
        status = await ctx._poll_order_status(hash("12345") & 0x7FFFFFFF)
        assert status == OrderState.FILLED

    @pytest.mark.asyncio
    async def test_cancelled_order(self):
        ctx = LiveCCXTContext(exchange="binance", api_key="k", secret="s")
        ctx._exchange = AsyncMock()
        ctx._venue_order_map["999"] = "BTC-PERP"
        ctx._exchange.fetch_order = AsyncMock(return_value={"status": "canceled"})
        status = await ctx._poll_order_status(hash("999") & 0x7FFFFFFF)
        assert status == OrderState.CANCELLED

    @pytest.mark.asyncio
    async def test_unknown_order(self):
        ctx = LiveCCXTContext(exchange="binance", api_key="k", secret="s")
        ctx._exchange = AsyncMock()
        status = await ctx._poll_order_status(99999)
        assert status == OrderState.FAILED
