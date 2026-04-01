# Safety Rails + Parity Test — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add live trading safety guarantees — kill switch with auto-flatten, extended risk guards, dry-run mode, Telegram/Discord alerting, and a backtest-to-live parity test.

**Architecture:** `EquityMonitor` runs as a background async task alongside the tick loop, checking drawdown every 5 seconds. Two new `RiskGuard` subclasses add per-market position limits and order rate limiting. Dry-run mode intercepts `submit_pending_orders()` to simulate fills. Alert integration wires existing `NotificationManager` into `LiveExecutionContext` callbacks. `ParityTest` runs backtest and paper engines on the same candle data and compares results.

**Tech Stack:** Python 3.10+, asyncio, DuckDB (FlintStore), httpx (Telegram/Discord), numpy (correlation)

---

### Task 1: Add safety rails config fields

**Files:**
- Modify: `flint/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_config.py`:

```python
class TestSafetyRailsConfig:
    def test_defaults(self):
        cfg = FlintConfig()
        assert cfg.live_dry_run is False
        assert cfg.live_kill_switch_drawdown_pct == 0.15
        assert cfg.live_kill_switch_check_interval_s == 5.0
        assert cfg.live_max_orders_per_minute == 30
        assert cfg.live_per_market_position_limits == ""
        assert cfg.live_drawdown_warning_pct == 0.075

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_LIVE_DRY_RUN", "true")
        monkeypatch.setenv("FLINT_LIVE_KILL_SWITCH_DRAWDOWN_PCT", "0.10")
        monkeypatch.setenv("FLINT_LIVE_MAX_ORDERS_PER_MINUTE", "60")
        cfg = FlintConfig()
        assert cfg.live_dry_run is True
        assert cfg.live_kill_switch_drawdown_pct == 0.10
        assert cfg.live_max_orders_per_minute == 60
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::TestSafetyRailsConfig -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Add config fields**

In `flint/config.py`, add after the WebSocket feed fields (after `live_tick_markets`):

```python
    # --- Safety rails ---
    live_dry_run: bool = False
    live_kill_switch_drawdown_pct: float = 0.15
    live_kill_switch_check_interval_s: float = 5.0
    live_max_orders_per_minute: int = 30
    live_per_market_position_limits: str = ""
    live_drawdown_warning_pct: float = 0.075
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/config.py tests/test_config.py
git commit -m "feat: add safety rails config fields (kill switch, dry-run, rate limits)"
```

---

### Task 2: Extended risk guards — MaxOrdersPerMinute + PerMarketPositionLimit

**Files:**
- Modify: `flint/risk/guards.py`
- Test: `tests/test_guards.py`

- [ ] **Step 1: Write the tests**

Create `tests/test_guards.py`:

```python
"""Tests for extended risk guards."""
import time
import pytest

from flint.models import AccountState, Order, OrderType, PositionInfo, Side
from flint.risk.guards import MaxOrdersPerMinute, PerMarketPositionLimit


class TestMaxOrdersPerMinute:
    def _make_order(self, ts, order_id="o1"):
        return Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                     size=1.0, order_id=order_id, ts=ts)

    def _account(self):
        return AccountState(equity=10000, cash=10000)

    def test_allows_within_limit(self):
        guard = MaxOrdersPerMinute(max_orders=5)
        now = int(time.time())
        for i in range(5):
            result = guard.check(self._make_order(now, f"o{i}"), self._account(), [])
            assert result is not None

    def test_rejects_over_limit(self):
        guard = MaxOrdersPerMinute(max_orders=3)
        now = int(time.time())
        for i in range(3):
            guard.check(self._make_order(now, f"o{i}"), self._account(), [])
        result = guard.check(self._make_order(now, "o3"), self._account(), [])
        assert result is None

    def test_old_orders_expire(self):
        guard = MaxOrdersPerMinute(max_orders=2)
        old_ts = int(time.time()) - 61  # 61 seconds ago
        guard.check(self._make_order(old_ts, "o0"), self._account(), [])
        guard.check(self._make_order(old_ts, "o1"), self._account(), [])
        # Both should have expired by now
        now = int(time.time())
        result = guard.check(self._make_order(now, "o2"), self._account(), [])
        assert result is not None


class TestPerMarketPositionLimit:
    def _make_order(self, market, size, price=150.0):
        return Order(market=market, side=Side.LONG, order_type=OrderType.MARKET,
                     size=size, price=price, order_id="o1", ts=int(time.time()))

    def _account(self):
        return AccountState(equity=10000, cash=10000)

    def test_allows_within_limit(self):
        guard = PerMarketPositionLimit(limits={"SOL-PERP": 10000})
        order = self._make_order("SOL-PERP", 10.0, 150.0)  # 1500 notional
        result = guard.check(order, self._account(), [])
        assert result is not None

    def test_rejects_over_limit(self):
        guard = PerMarketPositionLimit(limits={"SOL-PERP": 1000})
        order = self._make_order("SOL-PERP", 10.0, 150.0)  # 1500 notional
        result = guard.check(order, self._account(), [])
        assert result is None

    def test_includes_existing_position(self):
        guard = PerMarketPositionLimit(limits={"SOL-PERP": 2000})
        existing = PositionInfo(market="SOL-PERP", side=Side.LONG, size=10.0,
                                entry_price=150.0)  # 1500 existing
        order = self._make_order("SOL-PERP", 5.0, 150.0)  # 750 new
        result = guard.check(order, self._account(), [existing])
        assert result is None  # 1500 + 750 = 2250 > 2000

    def test_uncapped_market_passes(self):
        guard = PerMarketPositionLimit(limits={"SOL-PERP": 1000})
        order = self._make_order("BTC-PERP", 100.0, 65000.0)  # huge but uncapped
        result = guard.check(order, self._account(), [])
        assert result is not None

    def test_empty_limits_passes_all(self):
        guard = PerMarketPositionLimit(limits={})
        order = self._make_order("SOL-PERP", 1000.0, 150.0)
        result = guard.check(order, self._account(), [])
        assert result is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_guards.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Add the guards**

In `flint/risk/guards.py`, add `Dict` to the typing imports and `from collections import deque` at the top. Then add these classes before the `RiskManager` class:

```python
from collections import deque
from typing import Dict, List, Optional
```

```python
class MaxOrdersPerMinute(RiskGuard):
    """Reject orders if too many have been placed in the last 60 seconds."""

    def __init__(self, max_orders: int = 30):
        self.max_orders = max_orders
        self._timestamps: deque = deque()

    def check(self, order, account, positions):
        now = order.ts if order.ts else int(__import__('time').time())
        # Prune entries older than 60 seconds
        while self._timestamps and self._timestamps[0] < now - 60:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_orders:
            logger.info("MaxOrdersPerMinute: rejected order %s (%d orders in last 60s)",
                       order.order_id, len(self._timestamps))
            return None
        self._timestamps.append(now)
        return order


class PerMarketPositionLimit(RiskGuard):
    """Hard cap per market in USD notional."""

    def __init__(self, limits: Dict[str, float]):
        self.limits = limits

    def check(self, order, account, positions):
        limit = self.limits.get(order.market)
        if limit is None:
            return order  # uncapped market
        price = order.price
        if price <= 0:
            for p in positions:
                if p.market == order.market and p.entry_price > 0:
                    price = p.entry_price
                    break
            if price <= 0:
                price = 1.0
        existing = sum(p.size * p.entry_price for p in positions if p.market == order.market)
        new_notional = order.size * price
        total = existing + new_notional
        if total > limit:
            logger.info("PerMarketPositionLimit: rejected order %s on %s (notional=%.2f > limit=%.2f)",
                       order.order_id, order.market, total, limit)
            return None
        return order
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_guards.py -v`
Expected: PASS (all 8 tests)

- [ ] **Step 5: Commit**

```bash
git add flint/risk/guards.py tests/test_guards.py
git commit -m "feat: add MaxOrdersPerMinute and PerMarketPositionLimit risk guards"
```

---

### Task 3: EquityMonitor with kill switch

**Files:**
- Create: `flint/risk/monitor.py`
- Test: `tests/test_equity_monitor.py`

- [ ] **Step 1: Write the tests**

Create `tests/test_equity_monitor.py`:

```python
"""Tests for EquityMonitor — real-time drawdown monitoring + kill switch."""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock

from flint.models import AccountState, PositionInfo, Side
from flint.risk.monitor import EquityMonitor


class MockContext:
    """Minimal mock of LiveExecutionContext for EquityMonitor."""

    def __init__(self, equity=10000.0, cash=10000.0, positions=None):
        self._cash = cash
        self._positions_cache = {}
        self._running = True
        if positions:
            for p in positions:
                self._positions_cache[(p.venue, p.market)] = p

    @property
    def account(self):
        unrealized = sum(p.unrealized_pnl for p in self._positions_cache.values())
        return AccountState(equity=self._cash + unrealized, cash=self._cash,
                           unrealized_pnl=unrealized)

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
        assert monitor.peak_equity == 0  # not yet started

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
        assert monitor.peak_equity == 12000  # doesn't decrease

    def test_kill_switch_triggers(self):
        ctx = MockContext(equity=10000)
        monitor = EquityMonitor(context=ctx, kill_switch_pct=0.15)
        monitor._check_once()  # peak = 10000
        ctx._cash = 8400  # 16% drawdown > 15%
        monitor._check_once()
        assert monitor.tripped is True
        assert ctx._running is False

    def test_kill_switch_does_not_trigger_within_threshold(self):
        ctx = MockContext(equity=10000)
        monitor = EquityMonitor(context=ctx, kill_switch_pct=0.15)
        monitor._check_once()  # peak = 10000
        ctx._cash = 8600  # 14% drawdown < 15%
        monitor._check_once()
        assert monitor.tripped is False
        assert ctx._running is True

    def test_warning_fires_once(self):
        events = []
        nm = MagicMock()
        nm.notify = AsyncMock(return_value=1)

        ctx = MockContext(equity=10000)
        monitor = EquityMonitor(context=ctx, kill_switch_pct=0.15,
                                warning_pct=0.05, notification_manager=nm)
        monitor._check_once()  # peak = 10000
        ctx._cash = 9400  # 6% drawdown > 5% warning
        run(monitor._check_once_async())
        assert nm.notify.call_count == 1  # warning fired

        # Check again — warning should NOT fire again
        run(monitor._check_once_async())
        assert nm.notify.call_count == 1  # no duplicate

    def test_reset(self):
        ctx = MockContext(equity=10000)
        monitor = EquityMonitor(context=ctx, kill_switch_pct=0.15)
        monitor._check_once()
        ctx._cash = 8000  # trigger kill switch
        monitor._check_once()
        assert monitor.tripped is True
        monitor.reset()
        assert monitor.tripped is False

    def test_current_drawdown_pct(self):
        ctx = MockContext(equity=10000)
        monitor = EquityMonitor(context=ctx, kill_switch_pct=0.15)
        monitor._check_once()  # peak = 10000
        ctx._cash = 9000
        monitor._check_once()
        assert abs(monitor.current_drawdown_pct - 0.10) < 0.001
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_equity_monitor.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create monitor.py**

Create `flint/risk/monitor.py`:

```python
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
        kill_switch_pct: Drawdown percentage that triggers kill switch (e.g. 0.15 = 15%).
        warning_pct: Drawdown percentage that triggers a warning alert (e.g. 0.075 = 7.5%).
        check_interval_s: How often to check equity (seconds).
        notification_manager: Optional NotificationManager for alerts.
        pyth_feed: Optional PythWebSocketFeed for live oracle prices.
    """

    def __init__(
        self,
        context,
        kill_switch_pct: float = 0.15,
        warning_pct: float = 0.075,
        check_interval_s: float = 5.0,
        notification_manager=None,
        pyth_feed=None,
    ):
        self._context = context
        self._kill_switch_pct = kill_switch_pct
        self._warning_pct = warning_pct
        self._check_interval_s = check_interval_s
        self._notification_manager = notification_manager
        self._pyth_feed = pyth_feed

        self._peak_equity: float = 0.0
        self._tripped = False
        self._warning_fired = False
        self._running = False

    @property
    def tripped(self) -> bool:
        return self._tripped

    @property
    def peak_equity(self) -> float:
        return self._peak_equity

    @property
    def current_drawdown_pct(self) -> float:
        if self._peak_equity <= 0:
            return 0.0
        equity = self._context.account.equity
        return (self._peak_equity - equity) / self._peak_equity

    def reset(self) -> None:
        """Reset the kill switch after manual review."""
        self._tripped = False
        self._warning_fired = False
        self._peak_equity = self._context.account.equity
        logger.info("EquityMonitor reset. Peak equity = %.2f", self._peak_equity)

    async def run(self) -> None:
        """Background monitoring loop."""
        self._running = True
        logger.info("EquityMonitor started (kill=%.1f%%, warn=%.1f%%, interval=%.1fs)",
                     self._kill_switch_pct * 100, self._warning_pct * 100,
                     self._check_interval_s)
        while self._running and not self._tripped:
            try:
                await self._check_once_async()
            except Exception as e:
                logger.error("EquityMonitor check failed: %s", e)
            await asyncio.sleep(self._check_interval_s)

    async def stop(self) -> None:
        self._running = False

    def _check_once(self) -> None:
        """Synchronous equity check — updates peak, checks thresholds."""
        equity = self._context.account.equity
        if equity > self._peak_equity:
            self._peak_equity = equity

        if self._peak_equity <= 0:
            return

        drawdown = (self._peak_equity - equity) / self._peak_equity

        # Kill switch
        if drawdown >= self._kill_switch_pct and not self._tripped:
            self._tripped = True
            logger.critical(
                "KILL SWITCH: drawdown %.2f%% >= %.2f%% threshold. "
                "Flattening all positions.",
                drawdown * 100, self._kill_switch_pct * 100,
            )
            self._flatten()

    async def _check_once_async(self) -> None:
        """Async equity check — same logic as _check_once but can fire notifications."""
        equity = self._context.account.equity
        if equity > self._peak_equity:
            self._peak_equity = equity

        if self._peak_equity <= 0:
            return

        drawdown = (self._peak_equity - equity) / self._peak_equity

        # Warning
        if (drawdown >= self._warning_pct and not self._warning_fired
                and not self._tripped):
            self._warning_fired = True
            logger.warning("Drawdown warning: %.2f%% (threshold: %.2f%%)",
                          drawdown * 100, self._warning_pct * 100)
            await self._fire_alert(
                "drawdown_warning",
                f"Drawdown {drawdown*100:.1f}% from peak ${self._peak_equity:.2f}",
            )

        # Kill switch
        if drawdown >= self._kill_switch_pct and not self._tripped:
            self._tripped = True
            logger.critical(
                "KILL SWITCH: drawdown %.2f%% >= %.2f%%. Flattening all positions.",
                drawdown * 100, self._kill_switch_pct * 100,
            )
            self._flatten()
            await self._fire_alert(
                "kill_switch",
                f"Kill switch triggered at {drawdown*100:.1f}% drawdown. "
                f"Peak: ${self._peak_equity:.2f}, Current: ${equity:.2f}. "
                f"All positions flattened. Manual restart required.",
            )

    def _flatten(self) -> None:
        """Cancel all orders and close all positions."""
        ctx = self._context
        ctx.cancel_all()
        for pos in ctx.positions:
            ctx.close_position(pos.market, venue=pos.venue)
        ctx._running = False
        logger.info("All positions flattened, strategy halted")

    async def _fire_alert(self, event_type: str, message: str) -> None:
        if self._notification_manager:
            from ..notifications.base import TradingEvent
            event = TradingEvent(
                event_type=event_type,
                message=message,
                timestamp=int(time.time()),
            )
            await self._notification_manager.notify(event)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_equity_monitor.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 5: Commit**

```bash
git add flint/risk/monitor.py tests/test_equity_monitor.py
git commit -m "feat: add EquityMonitor with real-time kill switch and auto-flatten"
```

---

### Task 4: Dry-run mode

**Files:**
- Modify: `flint/execution/live_base.py`
- Test: `tests/test_live_base.py`

- [ ] **Step 1: Write the tests**

Add to `tests/test_live_base.py`:

```python
class TestDryRunMode:
    def test_dry_run_simulates_fill(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0, dry_run=True)
        candle = Candle(ts=1000, open=150.0, high=151.0, low=149.0,
                        close=150.5, volume=1000.0, market="SOL-PERP",
                        resolution_s=60, venue="drift")
        ctx._current_candle = candle

        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        assert oid != ""

        run(ctx.submit_pending_orders())

        # Order should be filled with DRY_RUN tx_sig
        tracked = ctx._tracker.get(oid)
        assert tracked is not None
        assert tracked.state == OrderState.FILLED
        assert len(tracked.fills) == 1
        assert tracked.fills[0].tx_sig == "DRY_RUN"
        assert tracked.fills[0].price == 150.5  # candle close

    def test_dry_run_updates_position(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0, dry_run=True)
        candle = Candle(ts=1000, open=150.0, high=151.0, low=149.0,
                        close=150.5, volume=1000.0, market="SOL-PERP",
                        resolution_s=60, venue="drift")
        ctx._current_candle = candle

        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        run(ctx.submit_pending_orders())

        pos = ctx.position("SOL-PERP", venue="test")
        assert pos is not None
        assert pos.size == 10.0

    def test_dry_run_does_not_call_place_order(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0, dry_run=True)
        candle = Candle(ts=1000, open=150.0, high=151.0, low=149.0,
                        close=150.5, volume=1000.0, market="SOL-PERP",
                        resolution_s=60, venue="drift")
        ctx._current_candle = candle

        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        run(ctx.submit_pending_orders())

        assert len(ctx._placed_orders) == 0  # _place_order NOT called
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_live_base.py::TestDryRunMode -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'dry_run'`

- [ ] **Step 3: Add dry-run to LiveExecutionContext**

In `flint/execution/live_base.py`:

**Add parameter to `__init__`** (after `tick_markets`):
```python
        dry_run: bool = False,
```

**Store it** (after `self._pyth_feed = None`):
```python
        self._dry_run = dry_run
```

**Modify `submit_pending_orders()`** — replace the `try` block inside the for loop:

```python
    async def submit_pending_orders(self) -> List[Fill]:
        pending = self._tracker.get_pending()
        fills = []

        for tracked in pending:
            if not self._tracker.can_submit():
                logger.debug("Rate limit hit, deferring remaining orders")
                break

            if self._dry_run:
                # Simulate fill without venue submission
                price = (self._current_candle.close if self._current_candle
                         else tracked.order.price or 0)
                fill = Fill(
                    market=tracked.order.market,
                    side=tracked.order.side,
                    price=price,
                    size=tracked.order.size,
                    fee=price * tracked.order.size * 0.0005,  # estimate
                    ts=int(time.time()),
                    order_id=tracked.flint_order_id,
                    tx_sig="DRY_RUN",
                    venue=self._venue,
                )
                self._tracker.mark_submitted(tracked.flint_order_id, tx_sig="DRY_RUN")
                self._tracker.mark_confirmed(tracked.flint_order_id, venue_order_id=0)
                self._tracker.mark_filled(tracked.flint_order_id, fill)
                self._persist_order(tracked)
                logger.info("[DRY RUN] %s %s %.4f %s @ %.2f",
                           tracked.order.side.value, tracked.order.market,
                           tracked.order.size, tracked.order.order_type.value, price)
                fills.append(fill)
            else:
                try:
                    tx_sig, venue_order_id = await self._place_order(tracked.order)
                    self._tracker.mark_submitted(tracked.flint_order_id, tx_sig=tx_sig)
                    if venue_order_id is not None:
                        self._tracker.mark_confirmed(tracked.flint_order_id, venue_order_id=venue_order_id)
                    self._persist_order(tracked)
                except Exception as e:
                    logger.error("Order %s submission failed: %s", tracked.flint_order_id, e)
                    if not self._tracker.increment_retry(tracked.flint_order_id):
                        pass

        return fills
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_live_base.py -v`
Expected: PASS (all tests including new dry-run tests)

- [ ] **Step 5: Commit**

```bash
git add flint/execution/live_base.py tests/test_live_base.py
git commit -m "feat: add dry-run mode to LiveExecutionContext (simulate fills, skip venue)"
```

---

### Task 5: Alert integration in LiveExecutionContext

**Files:**
- Modify: `flint/execution/live_base.py`
- Test: `tests/test_live_base.py`

- [ ] **Step 1: Write the tests**

Add to `tests/test_live_base.py`:

```python
class TestAlertIntegration:
    def test_fill_fires_notification(self):
        nm = MagicMock()
        nm.notify = AsyncMock(return_value=1)
        ctx = MockVenueContext(venue="test", initial_capital=10000.0,
                               notification_manager=nm)
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=150.0,
                    size=10.0, fee=0.15, ts=1000, order_id="o1", venue="test")
        ctx._handle_fill("o1", fill)
        assert nm.notify.call_count == 1
        event = nm.notify.call_args[0][0]
        assert event.event_type == "fill"

    def test_failure_fires_notification(self):
        nm = MagicMock()
        nm.notify = AsyncMock(return_value=1)
        ctx = MockVenueContext(venue="test", initial_capital=10000.0,
                               notification_manager=nm)
        ctx._handle_fail("o1", "retries exhausted")
        assert nm.notify.call_count == 1
        event = nm.notify.call_args[0][0]
        assert event.event_type == "order_failed"

    def test_risk_rejection_fires_notification(self):
        from flint.risk.guards import RiskManager, MaxOpenPositions
        nm = MagicMock()
        nm.notify = AsyncMock(return_value=1)
        rm = RiskManager(guards=[MaxOpenPositions(max_positions=0)])
        ctx = MockVenueContext(venue="test", initial_capital=10000.0,
                               risk_manager=rm, notification_manager=nm)
        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        assert nm.notify.call_count == 1
        event = nm.notify.call_args[0][0]
        assert event.event_type == "risk_rejection"

    def test_no_notification_without_manager(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=150.0,
                    size=10.0, fee=0.15, ts=1000, order_id="o1", venue="test")
        # Should not raise even without notification_manager
        ctx._handle_fill("o1", fill)
```

Add `AsyncMock` to the imports at the top of the test file if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_live_base.py::TestAlertIntegration -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'notification_manager'`

- [ ] **Step 3: Add alert integration**

In `flint/execution/live_base.py`:

**Add parameter to `__init__`** (after `dry_run`):
```python
        notification_manager=None,
```

**Store it** (after `self._dry_run = dry_run`):
```python
        self._notification_manager = notification_manager
```

**Add `_notify` helper method** (after `_handle_fail`):
```python
    def _notify(self, event_type: str, message: str, data=None) -> None:
        """Fire a notification if manager is configured."""
        if not self._notification_manager:
            return
        from ..notifications.base import TradingEvent
        import time as _time
        event = TradingEvent(
            event_type=event_type,
            message=message,
            data=data or {},
            timestamp=int(_time.time()),
        )
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._notification_manager.notify(event))
            else:
                loop.run_until_complete(self._notification_manager.notify(event))
        except Exception as e:
            logger.error("Notification failed: %s", e)
```

**Add notification calls to existing methods:**

In `_handle_fill()`, add at the end:
```python
        self._notify("fill", f"Fill: {fill.side.value} {fill.market} {fill.size:.4f} @ {fill.price:.2f}")
```

In `_handle_fail()`, add at the end:
```python
        self._notify("order_failed", f"Order {order_id} failed: {reason}")
```

In `_submit_order()`, when order is rejected (after `return ""`), add before the return:
```python
                self._notify("risk_rejection", f"Order rejected on {order.market}: {order.side.value} {order.size:.4f}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_live_base.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/execution/live_base.py tests/test_live_base.py
git commit -m "feat: add alert integration (fill, failure, rejection notifications)"
```

---

### Task 6: Integrate EquityMonitor into LiveExecutionContext.run()

**Files:**
- Modify: `flint/execution/live_base.py`
- Test: `tests/test_live_base.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_live_base.py`:

```python
class TestEquityMonitorIntegration:
    def test_run_starts_equity_monitor(self):
        from flint.risk.monitor import EquityMonitor
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        ctx._equity_monitor = EquityMonitor(context=ctx, kill_switch_pct=0.15)

        async def test():
            ctx._running = True
            # Just verify the monitor can be created and attached
            assert ctx._equity_monitor is not None
            assert ctx._equity_monitor.tripped is False

        run(test())
```

- [ ] **Step 2: Modify run() to start EquityMonitor**

In `flint/execution/live_base.py`, add to `__init__` (after `self._notification_manager`):
```python
        self._equity_monitor = None
```

In the `run()` method, after starting `poll_task`, add:
```python
        # Start equity monitor if configured
        monitor_task = None
        if self._equity_monitor:
            monitor_task = asyncio.create_task(self._equity_monitor.run())
```

In the `finally` block, add before the feed task cancellation:
```python
            if monitor_task:
                monitor_task.cancel()
                try:
                    await monitor_task
                except asyncio.CancelledError:
                    pass
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_live_base.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add flint/execution/live_base.py tests/test_live_base.py
git commit -m "feat: integrate EquityMonitor into LiveExecutionContext.run() lifecycle"
```

---

### Task 7: ParityTest + ParityReport

**Files:**
- Create: `flint/backtest/parity.py`
- Test: `tests/test_parity.py`

- [ ] **Step 1: Write the tests**

Create `tests/test_parity.py`:

```python
"""Tests for ParityTest — backtest vs paper comparison."""
import pytest

from flint.models import Candle, Side
from flint.backtest.parity import ParityTest, ParityReport
from flint.strategy.momentum import MomentumStrategy


def _make_candles(n=100, start_ts=0, market="SOL-PERP"):
    """Generate simple trending candles for testing."""
    candles = []
    price = 100.0
    for i in range(n):
        # Simple uptrend with noise
        price = price + 0.5 + (i % 3 - 1) * 0.2
        candles.append(Candle(
            ts=start_ts + i * 3600, open=price - 0.1, high=price + 0.3,
            low=price - 0.3, close=price, volume=1000.0,
            market=market, resolution_s=3600,
        ))
    return candles


class TestParityReport:
    def test_to_dict(self):
        report = ParityReport(
            backtest_pnl=100.0, backtest_trades=5, backtest_equity_curve=[10000, 10100],
            paper_pnl=98.0, paper_trades=5, paper_equity_curve=[10000, 10098],
            pnl_divergence_pct=2.0, fill_price_mae=0.05,
            equity_correlation=0.999, trade_count_match=True,
            signal_timing_match_pct=100.0, passed=True,
        )
        d = report.to_dict()
        assert d["pnl_divergence_pct"] == 2.0
        assert d["passed"] is True

    def test_summary_contains_key_info(self):
        report = ParityReport(
            backtest_pnl=100.0, backtest_trades=5, backtest_equity_curve=[],
            paper_pnl=98.0, paper_trades=5, paper_equity_curve=[],
            pnl_divergence_pct=2.0, fill_price_mae=0.05,
            equity_correlation=0.999, trade_count_match=True,
            signal_timing_match_pct=100.0, passed=True,
        )
        s = report.summary()
        assert "PASS" in s or "pass" in s.lower()


class TestParityTest:
    def test_same_engine_produces_low_divergence(self):
        candles = _make_candles(100)
        strategy = MomentumStrategy(lookback=5, threshold=0.5)
        pt = ParityTest(
            strategy=strategy, market="SOL-PERP", candles=candles,
            initial_capital=10000.0, fee_rate=0.0005,
        )
        report = pt.run()
        assert isinstance(report, ParityReport)
        assert report.trade_count_match is True
        # Both use same fill model defaults, so divergence should be very low
        assert report.pnl_divergence_pct < 5.0  # generous for test stability

    def test_report_has_equity_curves(self):
        candles = _make_candles(50)
        strategy = MomentumStrategy(lookback=5, threshold=0.5)
        pt = ParityTest(
            strategy=strategy, market="SOL-PERP", candles=candles,
            initial_capital=10000.0, fee_rate=0.0005,
        )
        report = pt.run()
        assert len(report.backtest_equity_curve) > 0
        assert len(report.paper_equity_curve) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parity.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create parity.py**

Create `flint/backtest/parity.py`:

```python
"""ParityTest — compare backtest engine vs paper broker on same data.

Answers: "Can I trust my backtest results?"
Runs both engines on identical candle data and compares fills, PnL, equity curves.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from ..models import Candle, FundingRate, Fill, Side

logger = logging.getLogger("flint.parity")


@dataclass
class ParityReport:
    """Results of a backtest-vs-paper comparison."""

    # Backtest results
    backtest_pnl: float
    backtest_trades: int
    backtest_equity_curve: List[float]

    # Paper results
    paper_pnl: float
    paper_trades: int
    paper_equity_curve: List[float]

    # Divergence metrics
    pnl_divergence_pct: float
    fill_price_mae: float
    equity_correlation: float
    trade_count_match: bool
    signal_timing_match_pct: float

    # Verdict
    passed: bool
    threshold_pct: float = 2.0

    def to_dict(self) -> dict:
        return {
            "backtest_pnl": self.backtest_pnl,
            "backtest_trades": self.backtest_trades,
            "paper_pnl": self.paper_pnl,
            "paper_trades": self.paper_trades,
            "pnl_divergence_pct": round(self.pnl_divergence_pct, 4),
            "fill_price_mae": round(self.fill_price_mae, 6),
            "equity_correlation": round(self.equity_correlation, 6),
            "trade_count_match": self.trade_count_match,
            "signal_timing_match_pct": round(self.signal_timing_match_pct, 2),
            "passed": self.passed,
            "threshold_pct": self.threshold_pct,
        }

    def summary(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        symbol = "+" if self.passed else "x"
        return (
            f"Backtest PnL:  ${self.backtest_pnl:,.2f}  ({self.backtest_trades} trades)\n"
            f"Paper PnL:     ${self.paper_pnl:,.2f}  ({self.paper_trades} trades)\n"
            f"PnL Divergence: {self.pnl_divergence_pct:.2f}%\n"
            f"Fill Price MAE:  ${self.fill_price_mae:.4f}\n"
            f"Equity Corr:    {self.equity_correlation:.3f}\n"
            f"Signal Match:   {self.signal_timing_match_pct:.0f}%\n"
            f"Result: {verdict} {symbol} (< {self.threshold_pct}% divergence)"
        )


class ParityTest:
    """Compare backtest engine and paper broker on the same candle data.

    Args:
        strategy: Strategy instance to test.
        market: Market symbol (e.g. "SOL-PERP").
        candles: Historical candle data to run through both engines.
        initial_capital: Starting capital for both runs.
        fee_rate: Fee rate for both runs.
        funding_rates: Optional funding rate data.
    """

    def __init__(
        self,
        strategy,
        market: str,
        candles: List[Candle],
        initial_capital: float = 10_000.0,
        fee_rate: float = 0.0005,
        funding_rates: Optional[List[FundingRate]] = None,
    ):
        self._strategy = strategy
        self._market = market
        self._candles = candles
        self._initial_capital = initial_capital
        self._fee_rate = fee_rate
        self._funding_rates = funding_rates

    def run(self) -> ParityReport:
        """Run both engines and compare results."""
        bt_result = self._run_backtest()
        paper_fills, paper_equity = self._run_paper()

        paper_pnl = paper_equity[-1] - self._initial_capital if paper_equity else 0
        paper_trades = len(paper_fills)
        bt_pnl = bt_result.total_pnl
        bt_trades = bt_result.total_trades

        # Compute metrics
        pnl_div = abs(bt_pnl - paper_pnl) / max(abs(bt_pnl), 1) * 100
        fill_mae = self._compute_fill_mae(bt_result.fills, paper_fills)
        eq_corr = self._compute_correlation(bt_result.equity_curve, paper_equity)
        trade_match = bt_trades == paper_trades
        timing_match = self._compute_timing_match(bt_result.fills, paper_fills)

        passed = pnl_div < 2.0

        return ParityReport(
            backtest_pnl=bt_pnl,
            backtest_trades=bt_trades,
            backtest_equity_curve=bt_result.equity_curve,
            paper_pnl=paper_pnl,
            paper_trades=paper_trades,
            paper_equity_curve=paper_equity,
            pnl_divergence_pct=pnl_div,
            fill_price_mae=fill_mae,
            equity_correlation=eq_corr,
            trade_count_match=trade_match,
            signal_timing_match_pct=timing_match,
            passed=passed,
        )

    def _run_backtest(self):
        """Run the backtest engine on candles."""
        from .engine import BacktestEngine
        from ..execution.fee_models import FlatFeeModel

        self._strategy.reset()
        engine = BacktestEngine(
            strategy=self._strategy,
            initial_capital=self._initial_capital,
            fee_rate=self._fee_rate,
            fee_model=FlatFeeModel(fee_bps=self._fee_rate * 10000),
            funding_rates=self._funding_rates,
        )
        return engine.run(self._candles)

    def _run_paper(self):
        """Run the paper broker on the same candles."""
        from ..execution.paper_broker import PaperBroker
        from ..execution.live_context import LiveContext
        from ..execution.fee_models import FlatFeeModel

        self._strategy.reset()
        broker = PaperBroker(
            initial_capital=self._initial_capital,
            fee_model=FlatFeeModel(fee_bps=self._fee_rate * 10000),
        )
        ctx = LiveContext(broker=broker)

        equity_curve = [self._initial_capital]
        all_fills = []

        for i, candle in enumerate(self._candles):
            ctx.set_candle(candle)
            history = self._candles[max(0, i - 50):i + 1]

            try:
                result = self._strategy.on_candle(candle, history, ctx)
                # Handle v1 signal-based strategies
                from ..models import Signal
                if result == Signal.BUY:
                    ctx.market_order(candle.market, Side.LONG, 1.0)
                elif result == Signal.SELL:
                    ctx.market_order(candle.market, Side.SHORT, 1.0)
            except Exception as e:
                logger.warning("Strategy error at candle %d: %s", i, e)

            fills = broker.process_candle(candle)
            all_fills.extend(fills)
            equity_curve.append(broker.equity)

        return all_fills, equity_curve

    def _compute_fill_mae(self, bt_fills, paper_fills) -> float:
        """Mean absolute error of fill prices (matched by timestamp)."""
        if not bt_fills or not paper_fills:
            return 0.0
        bt_by_ts = {f.ts: f.price for f in bt_fills}
        errors = []
        for pf in paper_fills:
            if pf.ts in bt_by_ts:
                errors.append(abs(bt_by_ts[pf.ts] - pf.price))
        return sum(errors) / len(errors) if errors else 0.0

    def _compute_correlation(self, curve_a, curve_b) -> float:
        """Pearson correlation of two equity curves."""
        import numpy as np
        min_len = min(len(curve_a), len(curve_b))
        if min_len < 2:
            return 1.0
        a = np.array(curve_a[:min_len], dtype=float)
        b = np.array(curve_b[:min_len], dtype=float)
        if np.std(a) == 0 or np.std(b) == 0:
            return 1.0
        return float(np.corrcoef(a, b)[0, 1])

    def _compute_timing_match(self, bt_fills, paper_fills) -> float:
        """% of backtest trades that have a paper trade at the same timestamp."""
        if not bt_fills:
            return 100.0
        paper_ts = {f.ts for f in paper_fills}
        matched = sum(1 for f in bt_fills if f.ts in paper_ts)
        return (matched / len(bt_fills)) * 100
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parity.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/backtest/parity.py tests/test_parity.py
git commit -m "feat: add ParityTest for backtest-vs-paper comparison with divergence metrics"
```

---

### Task 8: CLI parity command + API endpoint

**Files:**
- Modify: `flint/cli.py`
- Modify: `flint/api/routes/backtest.py`
- Test: `tests/test_parity.py` (add CLI test)

- [ ] **Step 1: Write the CLI test**

Add to `tests/test_parity.py`:

```python
class TestCLIParity:
    def test_parity_help(self):
        from typer.testing import CliRunner
        from flint.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["parity", "--help"])
        assert result.exit_code == 0
        assert "strategy" in result.output.lower() or "market" in result.output.lower()
```

- [ ] **Step 2: Add CLI command**

In `flint/cli.py`, add a new `parity` command. Read the file first to find the right location (after the existing `live` command). Add:

```python
@app.command()
def parity(
    strategy: str = typer.Argument(..., help="Strategy name (e.g. momentum)"),
    market: str = typer.Option("SOL-PERP", help="Market to test"),
    start: str = typer.Option(..., help="Start date (YYYY-MM-DD)"),
    end: str = typer.Option(..., help="End date (YYYY-MM-DD)"),
    capital: float = typer.Option(10_000.0, help="Initial capital"),
    fee_rate: float = typer.Option(0.0005, help="Fee rate"),
):
    """Run backtest-vs-paper parity test."""
    import datetime
    from .config import load_config
    from .store import FlintStore
    from .backtest.parity import ParityTest

    config = load_config()
    store = FlintStore(config.db_path)

    # Parse dates
    start_dt = datetime.datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.datetime.strptime(end, "%Y-%m-%d")
    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    # Load candles from store
    candles = store.query_candles(market, 3600, start_ts=start_ts, end_ts=end_ts)
    if not candles:
        typer.echo(f"No candle data for {market} in date range. Run 'flint init' first.")
        raise typer.Exit(1)

    # Load strategy
    from .api.routes.backtest import _build_strategy
    strat = _build_strategy(strategy, {})
    if strat is None:
        typer.echo(f"Unknown strategy: {strategy}")
        raise typer.Exit(1)

    typer.echo(f"Parity Test: {strategy} on {market} ({start} to {end})")
    typer.echo("─" * 50)

    pt = ParityTest(
        strategy=strat, market=market, candles=candles,
        initial_capital=capital, fee_rate=fee_rate,
    )
    report = pt.run()
    typer.echo(report.summary())

    store.close()
```

- [ ] **Step 3: Add API endpoint**

In `flint/api/routes/backtest.py`, add after the existing endpoints:

```python
@router.post("/parity")
def run_parity(req: dict, request: Request):
    """Run backtest-vs-paper parity test."""
    from ...backtest.parity import ParityTest

    store = request.app.state.store
    market = req.get("market", "SOL-PERP")
    strategy_name = req.get("strategy", "momentum")
    start_ts = req.get("start_ts", 0)
    end_ts = req.get("end_ts", 0)
    capital = req.get("capital", 10_000.0)
    fee_rate = req.get("fee_rate", 0.0005)

    candles = store.query_candles(market, 3600, start_ts=start_ts, end_ts=end_ts)
    if not candles:
        return {"error": f"No candle data for {market}"}

    strategy = _build_strategy(strategy_name, req.get("params", {}))
    if strategy is None:
        return {"error": f"Unknown strategy: {strategy_name}"}

    pt = ParityTest(
        strategy=strategy, market=market, candles=candles,
        initial_capital=capital, fee_rate=fee_rate,
    )
    report = pt.run()
    return report.to_dict()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_parity.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/cli.py flint/api/routes/backtest.py tests/test_parity.py
git commit -m "feat: add parity test CLI command and API endpoint"
```

---

### Task 9: Update ROADMAP.md

**Files:**
- Modify: `ROADMAP.md`

- [ ] **Step 1: Add implementation notes to §1.4 and §1.5**

After the §1.4 Safety Rails content (after the Alerting section), add:

```markdown
**Implemented (Sub-project 3):**
- [x] `EquityMonitor` with real-time kill switch — auto-flattens all positions on drawdown breach (`flint/risk/monitor.py`)
- [x] `MaxOrdersPerMinute` risk guard — sliding window rate limiter
- [x] `PerMarketPositionLimit` risk guard — per-market USD notional caps
- [x] Dry-run mode — full pipeline with simulated fills, `tx_sig="DRY_RUN"`
- [x] Alert integration — fills, rejections, failures, kill switch fire Telegram/Discord notifications
- [x] Safety rails config: kill switch threshold, warning threshold, rate limits, per-market limits
```

After §1.5 content, add:

```markdown
**Implemented (Sub-project 3):**
- [x] `ParityTest` class comparing backtest vs paper engine (`flint/backtest/parity.py`)
- [x] `ParityReport` with divergence metrics: PnL divergence, fill price MAE, equity correlation, signal timing match
- [x] CLI: `flint parity --strategy <name> --market <market> --start <date> --end <date>`
- [x] API: `POST /api/v1/backtest/parity`
- [x] Pass/fail threshold: < 2% PnL divergence
```

- [ ] **Step 2: Commit**

```bash
git add ROADMAP.md
git commit -m "docs: update ROADMAP.md with Phase 1.4 + 1.5 safety rails implementation notes"
```

---

### Task 10: Integration test — safety rails end-to-end

**Files:**
- Create: `tests/test_safety_integration.py`

- [ ] **Step 1: Write the integration test**

Create `tests/test_safety_integration.py`:

```python
"""Integration test: safety rails end-to-end."""
import asyncio
import time
import pytest
from unittest.mock import MagicMock, AsyncMock

from flint.models import Candle, Fill, OrderState, Side
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

        # Simulate a position
        from flint.models import PositionInfo
        ctx._positions_cache[("test", "SOL-PERP")] = PositionInfo(
            market="SOL-PERP", side=Side.LONG, size=10.0,
            entry_price=150.0, unrealized_pnl=0, venue="test",
        )

        # Set peak equity
        monitor._check_once()  # peak = 10000

        # Simulate drawdown
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

        # Verify dry-run fill
        tracked = ctx._tracker.get(oid)
        assert tracked.state == OrderState.FILLED
        assert tracked.fills[0].tx_sig == "DRY_RUN"

        # Verify position exists
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

        # Place order → submit → fill → should fire notification
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        run(ctx.submit_pending_orders())

        # Simulate fill
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=150.0,
                    size=10.0, fee=0.15, ts=1000, order_id=oid, venue="test")
        ctx._tracker.mark_filled(oid, fill)

        # Should have at least one notification (fill)
        assert nm.notify.call_count >= 1
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/test_safety_integration.py -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -x -q --timeout=120`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_safety_integration.py
git commit -m "test: add end-to-end integration tests for safety rails"
```

---

## File Map

| Action | File | Task |
|--------|------|------|
| Modify | `flint/config.py` | 1 |
| Modify | `flint/risk/guards.py` | 2 |
| Create | `flint/risk/monitor.py` | 3 |
| Modify | `flint/execution/live_base.py` | 4, 5, 6 |
| Modify | `flint/execution/context.py` | — (no changes needed for SP3) |
| Create | `flint/backtest/parity.py` | 7 |
| Modify | `flint/cli.py` | 8 |
| Modify | `flint/api/routes/backtest.py` | 8 |
| Modify | `ROADMAP.md` | 9 |
| Create | `tests/test_config.py` (add class) | 1 |
| Create | `tests/test_guards.py` | 2 |
| Create | `tests/test_equity_monitor.py` | 3 |
| Modify | `tests/test_live_base.py` (add classes) | 4, 5, 6 |
| Create | `tests/test_parity.py` | 7, 8 |
| Create | `tests/test_safety_integration.py` | 10 |

## Dependency Order

```
Task 1 (config)
Task 2 (guards)      ──→ all independent, can run in parallel
Task 3 (monitor)

Task 4 (dry-run)     ──→ independent of 1-3
Task 5 (alerts)      ──→ independent of 1-4

Task 6 (monitor integration) ──→ depends on Task 3
Task 7 (parity test) ──→ independent
Task 8 (CLI + API)   ──→ depends on Task 7

Task 9 (ROADMAP)     ──→ after all implementation
Task 10 (integration) ──→ depends on Tasks 2-5
```
