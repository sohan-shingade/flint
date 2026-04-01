"""EquityMonitor — real-time equity monitoring with kill switch.

Runs as a background async task alongside the strategy tick loop.
Checks drawdown against peak equity every check_interval_s seconds.
Auto-flattens all positions and halts the strategy if kill switch triggers.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger("flint.risk.monitor")


class EquityMonitor:
    """Real-time equity monitor with kill switch.

    Args:
        context: LiveExecutionContext instance to monitor and control.
        kill_switch_pct: Drawdown percentage that triggers kill switch.
        warning_pct: Drawdown percentage that triggers a warning alert.
        check_interval_s: How often to check equity (seconds).
        notification_manager: Optional NotificationManager for alerts.
        pyth_feed: Optional PythWebSocketFeed for live oracle prices.
    """

    def __init__(self, context, kill_switch_pct=0.15, warning_pct=0.075,
                 check_interval_s=5.0, notification_manager=None, pyth_feed=None):
        self._context = context
        self._kill_switch_pct = kill_switch_pct
        self._warning_pct = warning_pct
        self._check_interval_s = check_interval_s
        self._notification_manager = notification_manager
        self._pyth_feed = pyth_feed
        self._peak_equity = 0.0
        self._tripped = False
        self._warning_fired = False
        self._running = False

    @property
    def tripped(self):
        return self._tripped

    @property
    def peak_equity(self):
        return self._peak_equity

    @property
    def current_drawdown_pct(self):
        if self._peak_equity <= 0:
            return 0.0
        equity = self._context.account.equity
        return (self._peak_equity - equity) / self._peak_equity

    def reset(self):
        self._tripped = False
        self._warning_fired = False
        self._peak_equity = self._context.account.equity
        logger.info("EquityMonitor reset. Peak equity = %.2f", self._peak_equity)

    async def run(self):
        self._running = True
        logger.info("EquityMonitor started (kill=%.1f%%, warn=%.1f%%, interval=%.1fs)",
                     self._kill_switch_pct * 100, self._warning_pct * 100, self._check_interval_s)
        while self._running and not self._tripped:
            try:
                await self._check_once_async()
            except Exception as e:
                logger.error("EquityMonitor check failed: %s", e)
            await asyncio.sleep(self._check_interval_s)

    async def stop(self):
        self._running = False

    def _check_once(self):
        equity = self._context.account.equity
        if equity > self._peak_equity:
            self._peak_equity = equity
        if self._peak_equity <= 0:
            return
        drawdown = (self._peak_equity - equity) / self._peak_equity
        if drawdown >= self._kill_switch_pct and not self._tripped:
            self._tripped = True
            logger.critical("KILL SWITCH: drawdown %.2f%% >= %.2f%%. Flattening all positions.",
                          drawdown * 100, self._kill_switch_pct * 100)
            self._flatten()

    async def _check_once_async(self):
        equity = self._context.account.equity
        if equity > self._peak_equity:
            self._peak_equity = equity
        if self._peak_equity <= 0:
            return
        drawdown = (self._peak_equity - equity) / self._peak_equity
        if drawdown >= self._warning_pct and not self._warning_fired and not self._tripped:
            self._warning_fired = True
            logger.warning("Drawdown warning: %.2f%%", drawdown * 100)
            await self._fire_alert("drawdown_warning",
                f"Drawdown {drawdown*100:.1f}% from peak ${self._peak_equity:.2f}")
        if drawdown >= self._kill_switch_pct and not self._tripped:
            self._tripped = True
            logger.critical("KILL SWITCH: drawdown %.2f%% >= %.2f%%. Flattening all positions.",
                          drawdown * 100, self._kill_switch_pct * 100)
            self._flatten()
            await self._fire_alert("kill_switch",
                f"Kill switch triggered at {drawdown*100:.1f}% drawdown. "
                f"Peak: ${self._peak_equity:.2f}, Current: ${equity:.2f}. "
                f"All positions flattened. Manual restart required.")

    def _flatten(self):
        ctx = self._context
        ctx.cancel_all()
        for pos in ctx.positions:
            ctx.close_position(pos.market, venue=pos.venue)
        ctx._running = False
        logger.info("All positions flattened, strategy halted")

    async def _fire_alert(self, event_type, message):
        if self._notification_manager:
            from ..notifications.base import TradingEvent
            event = TradingEvent(event_type=event_type, message=message, timestamp=int(time.time()))
            await self._notification_manager.notify(event)
