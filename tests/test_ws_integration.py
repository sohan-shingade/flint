"""Integration test: WebSocket feeds → CandleAggregator → event-driven tick loop."""
import asyncio
import time
import pytest
from unittest.mock import MagicMock

from flint.models import Candle, OrderState, Side
from flint.execution.live_base import LiveExecutionContext
from flint.providers.candle_aggregator import CandleAggregator
from flint.providers.pyth_ws import PythWebSocketFeed


class MockVenueForWS(LiveExecutionContext):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
    async def _connect(self): pass
    async def _disconnect(self): pass
    async def _place_order(self, order): return ("tx_1", 1)
    async def _cancel_order(self, venue_order_id): return True
    async def _fetch_positions(self): return []
    async def _fetch_balance(self): return 10000.0
    async def _poll_order_status(self, venue_order_id): return OrderState.CONFIRMED


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestCandleAggregatorToTickLoop:
    def test_aggregator_candle_triggers_strategy_tick(self):
        """Full pipeline: trade → aggregator → on_ws_candle → queue → tick."""
        ctx = MockVenueForWS(
            venue="test", initial_capital=10000.0,
            tick_mode="on_candle_close", tick_markets=["SOL-PERP"],
        )
        mock_strategy = MagicMock()

        agg = CandleAggregator(
            market="SOL-PERP", venue="drift", resolution_s=60,
            on_candle_close=ctx._on_ws_candle,
        )

        agg.process_trade(price=150.0, size=10.0, ts=0)
        agg.process_trade(price=152.0, size=5.0, ts=30)
        agg.process_trade(price=153.0, size=8.0, ts=60)  # closes bar at ts=0

        assert ctx._candle_queue.qsize() == 1

        async def process():
            candle = await asyncio.wait_for(ctx._candle_queue.get(), timeout=1.0)
            ctx._current_candle = candle
            ctx._tick_count += 1
            # Pass fetch_candle so _tick uses our candle instead of querying store
            async def fetch_candle():
                return candle
            await ctx._tick(mock_strategy, "SOL-PERP", fetch_candle=fetch_candle)

        run(process())
        mock_strategy.on_candle.assert_called_once()
        assert ctx._current_candle.venue == "drift"
        assert ctx._current_candle.close == 152.0


class TestPythFeedIntegration:
    def test_oracle_price_accessible_via_context(self):
        feed = PythWebSocketFeed(markets=["SOL-PERP"])
        run(feed._handle_message({
            "type": "price_update", "pair": "SOL/USD",
            "price": 150.25, "confidence": 0.05, "ts": 1000,
        }))

        ctx = MockVenueForWS(
            venue="test", initial_capital=10000.0,
        )
        ctx._pyth_feed = feed
        result = ctx.get_oracle_price("SOL-PERP")
        assert result == (150.25, 1000)
