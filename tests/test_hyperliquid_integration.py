"""Integration tests for Hyperliquid — end-to-end flows with mocks."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from flint.models import Candle, Fill, Order, OrderType, OrderState, PositionInfo, Side
from flint.store import FlintStore


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestWsCandleToTick:
    """WS candle close -> enqueue -> tick -> place order -> fill."""

    def test_candle_triggers_tick(self):
        from flint.providers.hyperliquid_ws import HyperliquidWebSocketFeed

        closed = []
        feed = HyperliquidWebSocketFeed(
            markets=["SOL-PERP"],
            on_candle_close=lambda c: closed.append(c),
        )

        run(feed._handle_message({
            "channel": "candle",
            "data": {"t": 1000000, "T": 1060000, "s": "SOL", "i": "1m",
                     "o": "150.0", "h": "155.0", "l": "148.0", "c": "153.0", "v": "1000.0"},
        }))
        run(feed._handle_message({
            "channel": "candle",
            "data": {"t": 1060000, "T": 1120000, "s": "SOL", "i": "1m",
                     "o": "153.0", "h": "157.0", "l": "152.0", "c": "156.0", "v": "900.0"},
        }))

        assert len(closed) == 1
        candle = closed[0]
        assert candle.venue == "hyperliquid"
        assert candle.market == "SOL-PERP"


class TestOrderFlowEndToEnd:
    """Strategy places order -> LiveHyperliquidContext submits -> mock fill."""

    def test_market_order_dry_run(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext

        ctx = LiveHyperliquidContext(
            private_key="0x" + "ab" * 32,
            network="testnet",
            dry_run=True,
            initial_capital=10000.0,
        )

        ctx._current_candle = Candle(
            ts=1000, open=150.0, high=155.0, low=148.0, close=153.0,
            volume=100.0, market="SOL-PERP", resolution_s=60,
        )

        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        assert oid != ""

        fills = run(ctx.submit_pending_orders())
        assert len(fills) == 1
        assert fills[0].venue == "hyperliquid"
        assert fills[0].tx_sig == "DRY_RUN"
        assert fills[0].price == 153.0
        assert fills[0].size == 10.0


class TestPositionParsing:
    """Clearinghouse state -> PositionInfo list."""

    def test_mixed_positions(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext

        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")
        mock_client = MagicMock()
        mock_client.address = "0x1234"
        mock_client.get_clearinghouse_state = AsyncMock(return_value={
            "marginSummary": {"accountValue": "15000.0"},
            "assetPositions": [
                {"position": {"coin": "SOL", "szi": "10.0", "entryPx": "150.0", "unrealizedPnl": "50.0"}},
                {"position": {"coin": "ETH", "szi": "-2.0", "entryPx": "3500.0", "unrealizedPnl": "-30.0"}},
                {"position": {"coin": "BTC", "szi": "0.0", "entryPx": "0", "unrealizedPnl": "0"}},
            ],
        })
        ctx._client = mock_client

        positions = run(ctx._fetch_positions())
        assert len(positions) == 2
        sol = next(p for p in positions if p.market == "SOL-PERP")
        assert sol.side == Side.LONG
        eth = next(p for p in positions if p.market == "ETH-PERP")
        assert eth.side == Side.SHORT
        assert eth.size == 2.0


class TestStoreIntegration:
    """Verify store persistence works with Hyperliquid data."""

    def test_candle_persists_with_venue(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        candle = Candle(
            ts=1700000000, open=150.0, high=155.0, low=148.0, close=153.0,
            volume=1000.0, market="SOL-PERP", resolution_s=60, venue="hyperliquid",
        )
        store.upsert_candles([candle])
        result = store.query_candles("SOL-PERP", 60, 1699999000, 1700001000)
        assert len(result) >= 1
        store.close()
