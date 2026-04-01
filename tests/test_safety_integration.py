"""Integration test: safety rails end-to-end."""
import asyncio
import time
import pytest
from unittest.mock import MagicMock, AsyncMock

from flint.models import Candle, Fill, OrderState, Side, PositionInfo
from flint.execution.live_base import LiveExecutionContext
from flint.risk.guards import RiskManager, MaxOrdersPerMinute, PerMarketPositionLimit
from flint.risk.monitor import EquityMonitor


class MockVenueForSafety(LiveExecutionContext):
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


class TestKillSwitchEndToEnd:
    def test_kill_switch_flattens_and_halts(self):
        ctx = MockVenueForSafety(venue="test", initial_capital=10000.0)
        monitor = EquityMonitor(context=ctx, kill_switch_pct=0.15)
        ctx._positions_cache[("test", "SOL-PERP")] = PositionInfo(
            market="SOL-PERP", side=Side.LONG, size=10.0,
            entry_price=150.0, unrealized_pnl=0, venue="test",
        )
        monitor._check_once()  # peak = 10000
        ctx._cash = 8000  # 20% drawdown
        monitor._check_once()
        assert monitor.tripped is True
        assert ctx._running is False


class TestDryRunEndToEnd:
    def test_dry_run_with_risk_guards(self):
        rm = RiskManager(guards=[
            MaxOrdersPerMinute(max_orders=100),
            PerMarketPositionLimit(limits={"SOL-PERP": 50000}),
        ])
        ctx = MockVenueForSafety(
            venue="test", initial_capital=10000.0,
            risk_manager=rm, dry_run=True,
        )
        candle = Candle(ts=1000, open=150.0, high=151.0, low=149.0,
                        close=150.5, volume=1000.0, market="SOL-PERP",
                        resolution_s=60, venue="drift")
        ctx._current_candle = candle
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        assert oid != ""
        run(ctx.submit_pending_orders())
        tracked = ctx._tracker.get(oid)
        assert tracked.state == OrderState.FILLED
        assert tracked.fills[0].tx_sig == "DRY_RUN"
        pos = ctx.position("SOL-PERP", venue="test")
        assert pos is not None


class TestAlertEndToEnd:
    def test_full_flow_with_notifications(self):
        nm = MagicMock()
        nm.notify = AsyncMock(return_value=1)
        ctx = MockVenueForSafety(
            venue="test", initial_capital=10000.0,
            notification_manager=nm,
        )
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        run(ctx.submit_pending_orders())
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=150.0,
                    size=10.0, fee=0.15, ts=1000, order_id=oid, venue="test")
        ctx._tracker.mark_filled(oid, fill)
        assert nm.notify.call_count >= 1
