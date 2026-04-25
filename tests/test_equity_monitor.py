"""Tests for EquityMonitor — real-time drawdown monitoring + kill switch."""
import asyncio
from unittest.mock import MagicMock, AsyncMock
from flint.models import AccountState
from flint.risk.monitor import EquityMonitor


class MockContext:
    def __init__(self, equity=10000.0, cash=None, positions=None):
        self._cash = cash if cash is not None else equity
        self._positions_cache = {}
        self._running = True
        if positions:
            for p in positions:
                self._positions_cache[(p.venue, p.market)] = p

    @property
    def account(self):
        unrealized = sum(p.unrealized_pnl for p in self._positions_cache.values())
        return AccountState(equity=self._cash + unrealized, cash=self._cash, unrealized_pnl=unrealized)

    @property
    def positions(self):
        return list(self._positions_cache.values())

    def cancel_all(self, market=None):
        return 0

    def close_position(self, market, venue="default"):
        return "close-1"

    async def submit_pending_orders(self):
        return []


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestEquityMonitor:
    def test_initial_state(self):
        ctx = MockContext(equity=10000)
        monitor = EquityMonitor(context=ctx, kill_switch_pct=0.15)
        assert monitor.tripped is False
        assert monitor.peak_equity == 0

    def test_tracks_peak_equity(self):
        ctx = MockContext(equity=11000)
        monitor = EquityMonitor(context=ctx, kill_switch_pct=0.15)
        monitor._check_once()
        assert monitor.peak_equity == 11000
        ctx._cash = 12000
        monitor._check_once()
        assert monitor.peak_equity == 12000
        ctx._cash = 11500
        monitor._check_once()
        assert monitor.peak_equity == 12000

    def test_kill_switch_triggers(self):
        ctx = MockContext(equity=10000)
        monitor = EquityMonitor(context=ctx, kill_switch_pct=0.15)
        monitor._check_once()
        ctx._cash = 8400
        monitor._check_once()
        assert monitor.tripped is True
        assert ctx._running is False

    def test_kill_switch_does_not_trigger_within_threshold(self):
        ctx = MockContext(equity=10000)
        monitor = EquityMonitor(context=ctx, kill_switch_pct=0.15)
        monitor._check_once()
        ctx._cash = 8600
        monitor._check_once()
        assert monitor.tripped is False
        assert ctx._running is True

    def test_warning_fires_once(self):
        nm = MagicMock()
        nm.notify = AsyncMock(return_value=1)
        ctx = MockContext(equity=10000)
        monitor = EquityMonitor(context=ctx, kill_switch_pct=0.15,
                                warning_pct=0.05, notification_manager=nm)
        monitor._check_once()
        ctx._cash = 9400
        run(monitor._check_once_async())
        assert nm.notify.call_count == 1
        run(monitor._check_once_async())
        assert nm.notify.call_count == 1  # no duplicate

    def test_reset(self):
        ctx = MockContext(equity=10000)
        monitor = EquityMonitor(context=ctx, kill_switch_pct=0.15)
        monitor._check_once()
        ctx._cash = 8000
        monitor._check_once()
        assert monitor.tripped is True
        monitor.reset()
        assert monitor.tripped is False

    def test_current_drawdown_pct(self):
        ctx = MockContext(equity=10000)
        monitor = EquityMonitor(context=ctx, kill_switch_pct=0.15)
        monitor._check_once()
        ctx._cash = 9000
        monitor._check_once()
        assert abs(monitor.current_drawdown_pct - 0.10) < 0.001
