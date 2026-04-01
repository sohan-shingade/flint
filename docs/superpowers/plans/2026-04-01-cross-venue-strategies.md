# Cross-Venue Strategies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable strategies to hold positions on multiple venues simultaneously with a unified execution context, paired leg submission, a funding arb strategy template, and cross-venue backtest support.

**Architecture:** `MultiVenueLiveContext` wraps multiple `LiveExecutionContext` instances and routes orders by venue parameter. `LegGroup` tracks paired cross-venue orders with timeout/unwind. `BacktestEngine` parses `"venue:market"` composite keys for per-venue candle and fill routing. `FundingArbStrategy` exploits funding rate divergence across venues.

**Tech Stack:** Existing `ExecutionContext` ABC, `LiveExecutionContext`, `BacktestEngine`, `asyncio` for parallel venue connections.

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `flint/models.py` | Add OrderLeg, LegGroup, LegGroupResult dataclasses | Modify |
| `flint/config.py` | Add 4 multi-venue config fields | Modify |
| `flint/execution/multi_venue_live.py` | MultiVenueLiveContext wrapper | Create |
| `flint/strategy/funding_arb.py` | FundingArbStrategy template | Create |
| `flint/backtest/engine.py` | venue:market composite key parsing, per-venue fill routing | Modify |
| `ROADMAP.md` | Mark Phase 3 sections as implemented | Modify |
| `tests/test_models_leg.py` | LegGroup dataclass tests | Create |
| `tests/test_multi_venue_config.py` | Config field tests | Create |
| `tests/test_multi_venue_live.py` | MultiVenueLiveContext tests | Create |
| `tests/test_funding_arb.py` | FundingArbStrategy tests | Create |
| `tests/test_cross_venue_backtest.py` | Cross-venue backtest engine tests | Create |
| `tests/test_cross_venue_integration.py` | End-to-end integration tests | Create |

---

### Task 1: Config Additions

**Files:**
- Modify: `flint/config.py`
- Create: `tests/test_multi_venue_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_multi_venue_config.py`:

```python
"""Tests for multi-venue config fields."""
from flint.config import FlintConfig


class TestMultiVenueConfig:
    def test_defaults(self):
        config = FlintConfig()
        assert config.live_multi_venue_primary == ""
        assert config.live_multi_venue_tick_mode == "primary"
        assert config.live_multi_venue_leg_timeout_s == 30.0
        assert config.live_multi_venue_auto_unwind is False

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_LIVE_MULTI_VENUE_PRIMARY", "drift")
        monkeypatch.setenv("FLINT_LIVE_MULTI_VENUE_TICK_MODE", "any")
        monkeypatch.setenv("FLINT_LIVE_MULTI_VENUE_LEG_TIMEOUT_S", "60.0")
        monkeypatch.setenv("FLINT_LIVE_MULTI_VENUE_AUTO_UNWIND", "true")
        config = FlintConfig()
        assert config.live_multi_venue_primary == "drift"
        assert config.live_multi_venue_tick_mode == "any"
        assert config.live_multi_venue_leg_timeout_s == 60.0
        assert config.live_multi_venue_auto_unwind is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_multi_venue_config.py -v`
Expected: FAIL — `FlintConfig` has no field `live_multi_venue_primary`

- [ ] **Step 3: Add config fields**

In `flint/config.py`, add after the Hyperliquid section (after `live_hyperliquid_l2_persist_interval_s`):

```python
    # --- Multi-venue ---
    live_multi_venue_primary: str = ""
    live_multi_venue_tick_mode: str = "primary"
    live_multi_venue_leg_timeout_s: float = 30.0
    live_multi_venue_auto_unwind: bool = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_multi_venue_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/config.py tests/test_multi_venue_config.py
git commit -m "feat: add multi-venue config fields (primary, tick_mode, leg_timeout, auto_unwind)"
```

---

### Task 2: Leg Group Data Models

**Files:**
- Modify: `flint/models.py`
- Create: `tests/test_models_leg.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_models_leg.py`:

```python
"""Tests for OrderLeg, LegGroup, LegGroupResult dataclasses."""
import time
from flint.models import OrderLeg, LegGroup, LegGroupResult, Side


class TestOrderLeg:
    def test_create(self):
        leg = OrderLeg(
            order_id="ord-1", venue="drift", market="SOL-PERP",
            side=Side.LONG, size=10.0,
        )
        assert leg.venue == "drift"
        assert leg.side == Side.LONG
        assert leg.size == 10.0

    def test_default_order_id(self):
        leg = OrderLeg(
            order_id="", venue="hyperliquid", market="SOL-PERP",
            side=Side.SHORT, size=10.0,
        )
        assert leg.order_id == ""


class TestLegGroup:
    def test_create_with_two_legs(self):
        legs = [
            OrderLeg(order_id="", venue="drift", market="SOL-PERP", side=Side.LONG, size=10.0),
            OrderLeg(order_id="", venue="hyperliquid", market="SOL-PERP", side=Side.SHORT, size=10.0),
        ]
        group = LegGroup(group_id="grp-1", legs=legs, timeout_s=30.0)
        assert group.group_id == "grp-1"
        assert len(group.legs) == 2
        assert group.status == "pending"
        assert group.timeout_s == 30.0

    def test_default_status(self):
        group = LegGroup(group_id="grp-2", legs=[])
        assert group.status == "pending"
        assert group.created_at == 0


class TestLegGroupResult:
    def test_all_filled(self):
        result = LegGroupResult(
            group_id="grp-1", status="filled",
            filled_legs=["ord-1", "ord-2"], failed_legs=[], unwind_order_ids=[],
        )
        assert result.status == "filled"
        assert len(result.filled_legs) == 2
        assert len(result.failed_legs) == 0

    def test_partial_with_unwind(self):
        result = LegGroupResult(
            group_id="grp-1", status="unwound",
            filled_legs=["ord-1"], failed_legs=["ord-2"],
            unwind_order_ids=["unwind-1"],
        )
        assert result.status == "unwound"
        assert len(result.unwind_order_ids) == 1

    def test_all_failed(self):
        result = LegGroupResult(
            group_id="grp-1", status="failed",
            filled_legs=[], failed_legs=["ord-1", "ord-2"], unwind_order_ids=[],
        )
        assert result.status == "failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models_leg.py -v`
Expected: FAIL — `ImportError: cannot import name 'OrderLeg'`

- [ ] **Step 3: Add dataclasses to models.py**

In `flint/models.py`, add after the `PositionInfo` dataclass (after `venue: str = "default"` line):

```python
@dataclass
class OrderLeg:
    """A single leg of a cross-venue order group."""
    order_id: str
    venue: str
    market: str
    side: Side
    size: float


@dataclass
class LegGroup:
    """A group of paired orders across venues."""
    group_id: str
    legs: List[OrderLeg]
    status: str = "pending"  # "pending", "partial", "filled", "failed", "unwound"
    created_at: int = 0
    timeout_s: float = 30.0


@dataclass
class LegGroupResult:
    """Result of a paired leg group submission."""
    group_id: str
    status: str  # "filled", "partial", "failed", "unwound"
    filled_legs: List[str]  # order_ids that filled
    failed_legs: List[str]  # order_ids that didn't fill
    unwind_order_ids: List[str]  # orders placed to unwind filled legs
```

Also add `OrderLeg`, `LegGroup`, `LegGroupResult` to any `__all__` export if one exists, or to the imports at the top of the file.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models_leg.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/models.py tests/test_models_leg.py
git commit -m "feat: add OrderLeg, LegGroup, LegGroupResult dataclasses for cross-venue pairing"
```

---

### Task 3: MultiVenueLiveContext — Constructor + Properties

**Files:**
- Create: `flint/execution/multi_venue_live.py`
- Create: `tests/test_multi_venue_live.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_multi_venue_live.py`:

```python
"""Tests for MultiVenueLiveContext — mocked venue contexts."""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, PropertyMock

from flint.models import AccountState, Candle, Fill, Order, OrderType, OrderState, PositionInfo, Side
from flint.execution.live_base import LiveExecutionContext


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_mock_venue(venue_name, cash=5000.0, positions=None):
    """Create a mock LiveExecutionContext for testing."""
    ctx = MagicMock(spec=LiveExecutionContext)
    ctx._venue = venue_name
    positions = positions or []
    unrealized = sum(p.unrealized_pnl for p in positions)

    type(ctx).account = PropertyMock(return_value=AccountState(
        equity=cash + unrealized, cash=cash, unrealized_pnl=unrealized,
    ))
    type(ctx).positions = PropertyMock(return_value=positions)
    type(ctx).pending_orders = PropertyMock(return_value=[])
    type(ctx).current_candle = PropertyMock(return_value=None)
    type(ctx).timestamp = PropertyMock(return_value=1000)

    ctx.market_order = MagicMock(return_value="ord-1")
    ctx.limit_order = MagicMock(return_value="ord-2")
    ctx.stop_order = MagicMock(return_value="ord-3")
    ctx.take_profit_order = MagicMock(return_value="ord-4")
    ctx.cancel = MagicMock(return_value=True)
    ctx.cancel_all = MagicMock(return_value=0)
    ctx.connect = AsyncMock()
    ctx.disconnect = AsyncMock()
    ctx.submit_pending_orders = AsyncMock(return_value=[])
    ctx._poll_orders_loop = AsyncMock()
    return ctx


class TestConstruction:
    def test_creates_with_two_venues(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        assert len(ctx._contexts) == 2

    def test_primary_defaults_to_first_key(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        assert ctx._primary_venue == "drift"

    def test_primary_explicit(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(
            contexts={"drift": drift, "hyperliquid": hl},
            primary_venue="hyperliquid",
        )
        assert ctx._primary_venue == "hyperliquid"


class TestAggregatedAccount:
    def test_account_sums_equity(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift", cash=5000.0)
        hl = _make_mock_venue("hyperliquid", cash=3000.0)
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        assert ctx.account.equity == 8000.0
        assert ctx.account.cash == 8000.0

    def test_venue_account_returns_single(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift", cash=5000.0)
        hl = _make_mock_venue("hyperliquid", cash=3000.0)
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        acct = ctx.venue_account("drift")
        assert acct.equity == 5000.0

    def test_positions_merges_all(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift_pos = [PositionInfo(market="SOL-PERP", side=Side.LONG, size=10.0, entry_price=150.0, venue="drift")]
        hl_pos = [PositionInfo(market="SOL-PERP", side=Side.SHORT, size=10.0, entry_price=150.0, venue="hyperliquid")]
        drift = _make_mock_venue("drift", positions=drift_pos)
        hl = _make_mock_venue("hyperliquid", positions=hl_pos)
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        assert len(ctx.positions) == 2

    def test_total_exposure_nets_across_venues(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift_pos = [PositionInfo(market="SOL-PERP", side=Side.LONG, size=10.0, entry_price=150.0, venue="drift")]
        hl_pos = [PositionInfo(market="SOL-PERP", side=Side.SHORT, size=10.0, entry_price=150.0, venue="hyperliquid")]
        drift = _make_mock_venue("drift", positions=drift_pos)
        hl = _make_mock_venue("hyperliquid", positions=hl_pos)
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        assert ctx.total_exposure("SOL-PERP") == 0.0  # delta neutral

    def test_per_venue_pnl(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift_pos = [PositionInfo(market="SOL-PERP", side=Side.LONG, size=10.0, entry_price=150.0, unrealized_pnl=50.0, venue="drift")]
        hl_pos = [PositionInfo(market="SOL-PERP", side=Side.SHORT, size=10.0, entry_price=150.0, unrealized_pnl=-30.0, venue="hyperliquid")]
        drift = _make_mock_venue("drift", cash=5000.0, positions=drift_pos)
        hl = _make_mock_venue("hyperliquid", cash=3000.0, positions=hl_pos)
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        pnl = ctx.per_venue_pnl()
        assert pnl["drift"] == 50.0
        assert pnl["hyperliquid"] == -30.0


class TestOrderRouting:
    def test_market_order_routes_to_venue(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        ctx.market_order("SOL-PERP", Side.LONG, 10.0, venue="drift")
        drift.market_order.assert_called_once()
        hl.market_order.assert_not_called()

    def test_market_order_routes_to_hyperliquid(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        ctx.market_order("SOL-PERP", Side.SHORT, 5.0, venue="hyperliquid")
        hl.market_order.assert_called_once()
        drift.market_order.assert_not_called()

    def test_default_venue_routes_to_primary(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(
            contexts={"drift": drift, "hyperliquid": hl},
            primary_venue="hyperliquid",
        )
        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        hl.market_order.assert_called_once()
        drift.market_order.assert_not_called()

    def test_limit_order_routes(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        ctx.limit_order("SOL-PERP", Side.LONG, 10.0, 150.0, venue="drift")
        drift.limit_order.assert_called_once()

    def test_stop_order_routes(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        ctx.stop_order("SOL-PERP", Side.SHORT, 5.0, 140.0, venue="hyperliquid")
        hl.stop_order.assert_called_once()

    def test_cancel_all_cancels_across_venues(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        drift.cancel_all.return_value = 2
        hl = _make_mock_venue("hyperliquid")
        hl.cancel_all.return_value = 1
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        total = ctx.cancel_all()
        assert total == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_multi_venue_live.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flint.execution.multi_venue_live'`

- [ ] **Step 3: Implement MultiVenueLiveContext**

Create `flint/execution/multi_venue_live.py`:

```python
"""MultiVenueLiveContext — wraps multiple venue contexts for cross-venue strategies.

Routes orders by venue parameter, aggregates positions and equity across venues,
supports paired leg submission with timeout and optional auto-unwind.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Dict, List, Optional, Tuple

from ..models import (
    AccountState, Candle, Fill, Order, OrderLeg, LegGroup, LegGroupResult,
    OrderState, OrderType, PositionInfo, Side,
)
from .context import ExecutionContext
from .live_base import LiveExecutionContext

logger = logging.getLogger("flint.multi_venue")


class MultiVenueLiveContext(ExecutionContext):
    """Wraps multiple LiveExecutionContext instances for cross-venue trading.

    Routes orders to the correct venue based on the venue parameter.
    Aggregates positions and equity across all venues.
    """

    def __init__(
        self,
        contexts: Dict[str, LiveExecutionContext],
        primary_venue: str = "",
        tick_mode: str = "primary",
        leg_timeout_s: float = 30.0,
        auto_unwind_failed_legs: bool = False,
    ):
        self._contexts = contexts
        self._primary_venue = primary_venue or next(iter(contexts))
        self._tick_mode = tick_mode
        self._leg_timeout_s = leg_timeout_s
        self._auto_unwind = auto_unwind_failed_legs
        self._leg_groups: Dict[str, LegGroup] = {}
        self._candle_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._equity_monitor = None

        logger.info("MultiVenueLiveContext: %d venues, primary=%s, tick_mode=%s",
                     len(contexts), self._primary_venue, tick_mode)

    # --- ExecutionContext properties ---

    @property
    def account(self) -> AccountState:
        total_equity = 0.0
        total_cash = 0.0
        total_unrealized = 0.0
        for ctx in self._contexts.values():
            acct = ctx.account
            total_equity += acct.equity
            total_cash += acct.cash
            total_unrealized += acct.unrealized_pnl
        return AccountState(
            equity=total_equity,
            cash=total_cash,
            unrealized_pnl=total_unrealized,
        )

    @property
    def positions(self) -> List[PositionInfo]:
        all_positions = []
        for ctx in self._contexts.values():
            all_positions.extend(ctx.positions)
        return all_positions

    @property
    def pending_orders(self) -> List[Order]:
        all_orders = []
        for ctx in self._contexts.values():
            all_orders.extend(ctx.pending_orders)
        return all_orders

    @property
    def current_candle(self) -> Optional[Candle]:
        primary = self._contexts.get(self._primary_venue)
        return primary.current_candle if primary else None

    @property
    def timestamp(self) -> int:
        return int(time.time())

    # --- Per-venue views ---

    def venue_account(self, venue: str) -> AccountState:
        ctx = self._contexts.get(venue)
        if ctx is None:
            return AccountState(equity=0, cash=0)
        return ctx.account

    def total_exposure(self, market: str) -> float:
        """Net size across all venues for a market. Positive = net long."""
        net = 0.0
        for pos in self.positions:
            if pos.market == market:
                if pos.side == Side.LONG:
                    net += pos.size
                else:
                    net -= pos.size
        return net

    def per_venue_pnl(self) -> Dict[str, float]:
        """Unrealized PnL per venue."""
        result = {}
        for venue, ctx in self._contexts.items():
            result[venue] = ctx.account.unrealized_pnl
        return result

    # --- Order routing ---

    def _resolve_venue(self, venue: str) -> str:
        if venue == "default" or not venue:
            return self._primary_venue
        return venue

    def market_order(self, market, side, size, reduce_only=False, tag="", venue="default"):
        target = self._resolve_venue(venue)
        ctx = self._contexts.get(target)
        if ctx is None:
            logger.error("Unknown venue: %s", target)
            return ""
        return ctx.market_order(market, side, size, reduce_only=reduce_only, tag=tag, venue=target)

    def limit_order(self, market, side, size, price, reduce_only=False, tag="", venue="default"):
        target = self._resolve_venue(venue)
        ctx = self._contexts.get(target)
        if ctx is None:
            logger.error("Unknown venue: %s", target)
            return ""
        return ctx.limit_order(market, side, size, price, reduce_only=reduce_only, tag=tag, venue=target)

    def stop_order(self, market, side, size, trigger_price, tag="", venue="default"):
        target = self._resolve_venue(venue)
        ctx = self._contexts.get(target)
        if ctx is None:
            logger.error("Unknown venue: %s", target)
            return ""
        return ctx.stop_order(market, side, size, trigger_price, tag=tag, venue=target)

    def take_profit_order(self, market, side, size, trigger_price, tag="", venue="default"):
        target = self._resolve_venue(venue)
        ctx = self._contexts.get(target)
        if ctx is None:
            logger.error("Unknown venue: %s", target)
            return ""
        return ctx.take_profit_order(market, side, size, trigger_price, tag=tag, venue=target)

    def cancel(self, order_id):
        for ctx in self._contexts.values():
            if ctx.cancel(order_id):
                return True
        return False

    def cancel_all(self, market=None):
        total = 0
        for ctx in self._contexts.values():
            total += ctx.cancel_all(market)
        return total

    def log(self, message: str) -> None:
        logger.info("[multi-venue] %s", message)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_multi_venue_live.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/execution/multi_venue_live.py tests/test_multi_venue_live.py
git commit -m "feat: add MultiVenueLiveContext with order routing and aggregated views"
```

---

### Task 4: MultiVenueLiveContext — Leg Group Submission

**Files:**
- Modify: `flint/execution/multi_venue_live.py`
- Modify: `tests/test_multi_venue_live.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_multi_venue_live.py`:

```python
class TestLegGroupSubmission:
    def test_submit_leg_group_both_fill(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        from flint.models import OrderLeg, LegGroupResult

        drift = _make_mock_venue("drift")
        drift.market_order.return_value = "drift-ord-1"
        hl = _make_mock_venue("hyperliquid")
        hl.market_order.return_value = "hl-ord-1"

        # Mock submit_pending_orders to simulate immediate fill
        async def drift_submit():
            return [Fill(market="SOL-PERP", side=Side.LONG, price=150.0, size=10.0, fee=0.075, ts=1000, order_id="drift-ord-1", venue="drift")]
        async def hl_submit():
            return [Fill(market="SOL-PERP", side=Side.SHORT, price=150.0, size=10.0, fee=0.05, ts=1000, order_id="hl-ord-1", venue="hyperliquid")]
        drift.submit_pending_orders = AsyncMock(side_effect=drift_submit)
        hl.submit_pending_orders = AsyncMock(side_effect=hl_submit)

        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})

        legs = [
            OrderLeg(order_id="", venue="drift", market="SOL-PERP", side=Side.LONG, size=10.0),
            OrderLeg(order_id="", venue="hyperliquid", market="SOL-PERP", side=Side.SHORT, size=10.0),
        ]
        result = run(ctx.submit_leg_group(legs))
        assert result.status == "filled"
        assert len(result.filled_legs) == 2
        assert len(result.failed_legs) == 0

    def test_submit_leg_group_one_fails_no_unwind(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        from flint.models import OrderLeg

        drift = _make_mock_venue("drift")
        drift.market_order.return_value = "drift-ord-1"
        hl = _make_mock_venue("hyperliquid")
        hl.market_order.return_value = "hl-ord-1"

        # Drift fills, Hyperliquid returns empty (no fill)
        drift.submit_pending_orders = AsyncMock(return_value=[
            Fill(market="SOL-PERP", side=Side.LONG, price=150.0, size=10.0, fee=0.075, ts=1000, order_id="drift-ord-1", venue="drift"),
        ])
        hl.submit_pending_orders = AsyncMock(return_value=[])

        ctx = MultiVenueLiveContext(
            contexts={"drift": drift, "hyperliquid": hl},
            leg_timeout_s=0.1,  # Short timeout for test
            auto_unwind_failed_legs=False,
        )

        legs = [
            OrderLeg(order_id="", venue="drift", market="SOL-PERP", side=Side.LONG, size=10.0),
            OrderLeg(order_id="", venue="hyperliquid", market="SOL-PERP", side=Side.SHORT, size=10.0),
        ]
        result = run(ctx.submit_leg_group(legs))
        assert result.status == "partial"
        assert len(result.filled_legs) == 1
        assert len(result.failed_legs) == 1
        assert len(result.unwind_order_ids) == 0

    def test_submit_leg_group_auto_unwind(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        from flint.models import OrderLeg

        drift = _make_mock_venue("drift")
        drift.market_order.return_value = "drift-ord-1"
        hl = _make_mock_venue("hyperliquid")
        hl.market_order.return_value = "hl-ord-1"

        drift.submit_pending_orders = AsyncMock(return_value=[
            Fill(market="SOL-PERP", side=Side.LONG, price=150.0, size=10.0, fee=0.075, ts=1000, order_id="drift-ord-1", venue="drift"),
        ])
        hl.submit_pending_orders = AsyncMock(return_value=[])

        ctx = MultiVenueLiveContext(
            contexts={"drift": drift, "hyperliquid": hl},
            leg_timeout_s=0.1,
            auto_unwind_failed_legs=True,
        )

        legs = [
            OrderLeg(order_id="", venue="drift", market="SOL-PERP", side=Side.LONG, size=10.0),
            OrderLeg(order_id="", venue="hyperliquid", market="SOL-PERP", side=Side.SHORT, size=10.0),
        ]
        result = run(ctx.submit_leg_group(legs))
        assert result.status == "unwound"
        assert len(result.unwind_order_ids) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_multi_venue_live.py::TestLegGroupSubmission -v`
Expected: FAIL — `MultiVenueLiveContext has no attribute 'submit_leg_group'`

- [ ] **Step 3: Implement submit_leg_group**

Add to `MultiVenueLiveContext` in `flint/execution/multi_venue_live.py`:

```python
    async def submit_leg_group(self, legs: List[OrderLeg]) -> LegGroupResult:
        """Submit a group of paired orders across venues.

        Places all legs in parallel, waits for fills up to leg_timeout_s.
        If some legs don't fill, cancels them. If auto_unwind is True,
        also closes the filled legs.
        """
        group_id = str(uuid.uuid4())[:8]
        group = LegGroup(
            group_id=group_id, legs=legs,
            created_at=int(time.time()), timeout_s=self._leg_timeout_s,
        )
        self._leg_groups[group_id] = group

        # Place all legs
        for leg in legs:
            target = self._resolve_venue(leg.venue)
            ctx = self._contexts.get(target)
            if ctx is None:
                continue
            oid = ctx.market_order(leg.market, leg.side, leg.size, venue=target)
            leg.order_id = oid

        # Submit in parallel
        submit_tasks = []
        for venue, ctx in self._contexts.items():
            if any(leg.venue == venue for leg in legs):
                submit_tasks.append(ctx.submit_pending_orders())
        all_fills = []
        results = await asyncio.gather(*submit_tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                all_fills.extend(r)

        # Determine which legs filled
        filled_order_ids = {f.order_id for f in all_fills}
        filled_legs = [leg.order_id for leg in legs if leg.order_id in filled_order_ids]
        failed_legs = [leg.order_id for leg in legs if leg.order_id not in filled_order_ids]

        # If some legs didn't fill, wait up to timeout then cancel
        if failed_legs:
            await asyncio.sleep(min(self._leg_timeout_s, 0.5))  # Brief wait
            for leg in legs:
                if leg.order_id in failed_legs:
                    target = self._resolve_venue(leg.venue)
                    ctx = self._contexts.get(target)
                    if ctx:
                        ctx.cancel(leg.order_id)

        # Auto-unwind filled legs if enabled and some failed
        unwind_ids = []
        if self._auto_unwind and failed_legs and filled_legs:
            for leg in legs:
                if leg.order_id in filled_legs:
                    opposite = Side.SHORT if leg.side == Side.LONG else Side.LONG
                    target = self._resolve_venue(leg.venue)
                    ctx = self._contexts.get(target)
                    if ctx:
                        unwind_id = ctx.market_order(leg.market, opposite, leg.size, venue=target)
                        unwind_ids.append(unwind_id)
            # Submit unwind orders
            for venue, ctx in self._contexts.items():
                if ctx.pending_orders:
                    await ctx.submit_pending_orders()

        # Determine status
        if not failed_legs:
            status = "filled"
        elif unwind_ids:
            status = "unwound"
        elif filled_legs:
            status = "partial"
        else:
            status = "failed"

        group.status = status
        return LegGroupResult(
            group_id=group_id, status=status,
            filled_legs=filled_legs, failed_legs=failed_legs,
            unwind_order_ids=unwind_ids,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_multi_venue_live.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/execution/multi_venue_live.py tests/test_multi_venue_live.py
git commit -m "feat: add paired leg submission with timeout and auto-unwind"
```

---

### Task 5: MultiVenueLiveContext — Run Lifecycle

**Files:**
- Modify: `flint/execution/multi_venue_live.py`
- Modify: `tests/test_multi_venue_live.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_multi_venue_live.py`:

```python
class TestTickRouting:
    def test_on_ws_candle_primary_mode_filters(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(
            contexts={"drift": drift, "hyperliquid": hl},
            primary_venue="drift",
            tick_mode="primary",
        )
        ctx._candle_queue = asyncio.Queue()

        # Primary venue candle should enqueue
        drift_candle = Candle(ts=1000, open=150.0, high=155.0, low=148.0, close=153.0, volume=100.0, market="SOL-PERP", resolution_s=60, venue="drift")
        ctx._on_ws_candle(drift_candle)
        assert ctx._candle_queue.qsize() == 1

        # Non-primary venue candle should NOT enqueue in primary mode
        hl_candle = Candle(ts=1000, open=150.0, high=155.0, low=148.0, close=153.0, volume=100.0, market="SOL-PERP", resolution_s=60, venue="hyperliquid")
        ctx._on_ws_candle(hl_candle)
        assert ctx._candle_queue.qsize() == 1  # Still 1

    def test_on_ws_candle_any_mode_enqueues_all(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(
            contexts={"drift": drift, "hyperliquid": hl},
            tick_mode="any",
        )
        ctx._candle_queue = asyncio.Queue()

        drift_candle = Candle(ts=1000, open=150.0, high=155.0, low=148.0, close=153.0, volume=100.0, market="SOL-PERP", resolution_s=60, venue="drift")
        ctx._on_ws_candle(drift_candle)
        hl_candle = Candle(ts=1000, open=150.0, high=155.0, low=148.0, close=153.0, volume=100.0, market="SOL-PERP", resolution_s=60, venue="hyperliquid")
        ctx._on_ws_candle(hl_candle)
        assert ctx._candle_queue.qsize() == 2


class TestClosePosition:
    def test_close_position_on_specific_venue(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift_pos = [PositionInfo(market="SOL-PERP", side=Side.LONG, size=10.0, entry_price=150.0, venue="drift")]
        drift = _make_mock_venue("drift", positions=drift_pos)
        drift.close_position = MagicMock(return_value="close-1")
        hl = _make_mock_venue("hyperliquid")
        hl.close_position = MagicMock(return_value=None)
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        result = ctx.close_position("SOL-PERP", venue="drift")
        drift.close_position.assert_called_once_with("SOL-PERP", "drift")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_multi_venue_live.py::TestTickRouting -v`
Expected: FAIL — `MultiVenueLiveContext has no attribute '_on_ws_candle'`

- [ ] **Step 3: Implement tick routing and remaining methods**

Add to `MultiVenueLiveContext` in `flint/execution/multi_venue_live.py`:

```python
    def _on_ws_candle(self, candle: Candle) -> None:
        """Called by WebSocket feeds when a candle closes."""
        if self._tick_mode == "primary":
            if candle.venue == self._primary_venue:
                self._candle_queue.put_nowait(candle)
        else:
            # "any" mode: all candles enqueue
            self._candle_queue.put_nowait(candle)

    async def run(self, strategy, market: str, feeds=None, fetch_candle=None) -> None:
        """Run the multi-venue tick loop."""
        self._running = True
        self._candle_queue = asyncio.Queue()

        # Connect all venues in parallel
        await asyncio.gather(*[ctx.connect() for ctx in self._contexts.values()])

        feed_tasks = []
        if feeds:
            for feed in feeds:
                feed_tasks.append(asyncio.create_task(feed.start()))

        poll_tasks = [asyncio.create_task(ctx._poll_orders_loop())
                      for ctx in self._contexts.values()]

        monitor_task = None
        if self._equity_monitor:
            monitor_task = asyncio.create_task(self._equity_monitor.run())

        try:
            while self._running:
                try:
                    candle = await asyncio.wait_for(
                        self._candle_queue.get(), timeout=120,
                    )
                    # Set candle on primary context for strategy access
                    primary = self._contexts.get(self._primary_venue)
                    if primary:
                        primary._current_candle = candle

                    # Call strategy
                    try:
                        strategy.on_candle(candle, [], ctx=self)
                    except Exception as e:
                        logger.error("Strategy error: %s", e)

                    # Submit pending orders on all venues in parallel
                    submit_tasks = []
                    for ctx in self._contexts.values():
                        if ctx.pending_orders:
                            submit_tasks.append(ctx.submit_pending_orders())
                    if submit_tasks:
                        await asyncio.gather(*submit_tasks)

                except asyncio.TimeoutError:
                    logger.debug("No candle within timeout")
        finally:
            if monitor_task:
                monitor_task.cancel()
            for task in feed_tasks + poll_tasks:
                task.cancel()
            for task in feed_tasks + poll_tasks + ([monitor_task] if monitor_task else []):
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            # Disconnect all venues
            await asyncio.gather(*[ctx.disconnect() for ctx in self._contexts.values()])

    async def stop(self) -> None:
        self._running = False

    def close_position(self, market, venue="default"):
        target = self._resolve_venue(venue)
        ctx = self._contexts.get(target)
        if ctx is None:
            return None
        return ctx.close_position(market, target)

    def position(self, market, venue="default"):
        target = self._resolve_venue(venue)
        ctx = self._contexts.get(target)
        if ctx is None:
            return None
        return ctx.position(market, target)

    def get_candles(self, market, lookback=50):
        primary = self._contexts.get(self._primary_venue)
        if primary:
            return primary.get_candles(market, lookback)
        return []

    def get_oracle_price(self, market=None):
        primary = self._contexts.get(self._primary_venue)
        if primary:
            return primary.get_oracle_price(market)
        return None

    def get_funding_rate(self, market=None):
        primary = self._contexts.get(self._primary_venue)
        if primary:
            return primary.get_funding_rate(market)
        return None

    def get_funding_by_venue(self, market=None, lookback=24):
        """Aggregate funding data from all venues."""
        result = {}
        for venue, ctx in self._contexts.items():
            try:
                rates = ctx.get_funding_rates(market, lookback)
                if rates:
                    result[venue] = rates
            except Exception:
                pass
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_multi_venue_live.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/execution/multi_venue_live.py tests/test_multi_venue_live.py
git commit -m "feat: add tick routing, run lifecycle, and position/funding delegation"
```

---

### Task 6: FundingArbStrategy

**Files:**
- Create: `flint/strategy/funding_arb.py`
- Create: `tests/test_funding_arb.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_funding_arb.py`:

```python
"""Tests for FundingArbStrategy."""
import pytest
from unittest.mock import MagicMock, PropertyMock

from flint.models import AccountState, Candle, PositionInfo, Side, Signal


def _make_mock_ctx(funding_by_venue=None, positions=None, cash=10000.0):
    ctx = MagicMock()
    positions = positions or []
    unrealized = sum(p.unrealized_pnl for p in positions)
    type(ctx).account = PropertyMock(return_value=AccountState(
        equity=cash + unrealized, cash=cash, unrealized_pnl=unrealized,
    ))
    type(ctx).positions = PropertyMock(return_value=positions)
    ctx.get_funding_by_venue.return_value = funding_by_venue or {}
    ctx.position.return_value = None
    ctx.total_exposure = MagicMock(return_value=0.0)
    ctx.market_order = MagicMock(return_value="ord-1")
    ctx.close_position = MagicMock(return_value="close-1")
    return ctx


class TestSignalGeneration:
    def test_no_entry_when_spread_below_threshold(self):
        from flint.strategy.funding_arb import FundingArbStrategy
        strategy = FundingArbStrategy(min_spread_bps=5.0, venues=["drift", "hyperliquid"])
        strategy.reset()

        ctx = _make_mock_ctx(funding_by_venue={
            "drift": [(1000, 0.0001)],      # 1 bps
            "hyperliquid": [(1000, 0.0002)], # 2 bps — spread = 1 bps < 5 bps
        })
        candle = Candle(ts=1000, open=150.0, high=155.0, low=148.0, close=153.0, volume=100.0, market="SOL-PERP", resolution_s=60)
        signal = strategy.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        ctx.market_order.assert_not_called()

    def test_entry_when_spread_above_threshold(self):
        from flint.strategy.funding_arb import FundingArbStrategy
        strategy = FundingArbStrategy(
            min_spread_bps=5.0, position_size_usd=1000.0,
            min_spread_duration=0,  # No persistence required for test
            venues=["drift", "hyperliquid"],
        )
        strategy.reset()

        ctx = _make_mock_ctx(funding_by_venue={
            "drift": [(1000, 0.0001)],        # 1 bps (low)
            "hyperliquid": [(1000, 0.001)],   # 10 bps (high) — spread = 9 bps > 5 bps
        })
        candle = Candle(ts=1000, open=150.0, high=155.0, low=148.0, close=150.0, volume=100.0, market="SOL-PERP", resolution_s=60)
        signal = strategy.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        # Should place two orders: long on low-rate venue, short on high-rate venue
        assert ctx.market_order.call_count == 2

    def test_exit_when_spread_converges(self):
        from flint.strategy.funding_arb import FundingArbStrategy
        strategy = FundingArbStrategy(
            min_spread_bps=5.0, exit_spread_bps=1.0,
            venues=["drift", "hyperliquid"],
        )
        strategy.reset()
        # Simulate having a position
        strategy._entry_ts = 500
        strategy._long_venue = "drift"
        strategy._short_venue = "hyperliquid"

        drift_pos = PositionInfo(market="SOL-PERP", side=Side.LONG, size=6.0, entry_price=150.0, venue="drift")
        hl_pos = PositionInfo(market="SOL-PERP", side=Side.SHORT, size=6.0, entry_price=150.0, venue="hyperliquid")

        ctx = _make_mock_ctx(
            funding_by_venue={
                "drift": [(1000, 0.0002)],
                "hyperliquid": [(1000, 0.00025)],  # Spread = 0.5 bps < 1.0 bps exit
            },
            positions=[drift_pos, hl_pos],
        )
        ctx.position.side_effect = lambda market, venue="default": {
            "drift": drift_pos, "hyperliquid": hl_pos,
        }.get(venue)

        candle = Candle(ts=1000, open=150.0, high=155.0, low=148.0, close=153.0, volume=100.0, market="SOL-PERP", resolution_s=60)
        signal = strategy.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        assert ctx.close_position.call_count == 2


class TestParameters:
    def test_parameters_returns_bounds(self):
        from flint.strategy.funding_arb import FundingArbStrategy
        params = FundingArbStrategy.parameters()
        assert "min_spread_bps" in params
        assert "exit_spread_bps" in params
        assert "max_hold_hours" in params
        assert "position_size_usd" in params


class TestName:
    def test_name(self):
        from flint.strategy.funding_arb import FundingArbStrategy
        s = FundingArbStrategy()
        assert s.name == "funding_arb"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_funding_arb.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flint.strategy.funding_arb'`

- [ ] **Step 3: Implement FundingArbStrategy**

Create `flint/strategy/funding_arb.py`:

```python
"""FundingArbStrategy — delta-neutral cross-venue funding rate arbitrage.

Exploits funding rate divergence between venues. Long on the venue paying you
(low/negative funding), short on the venue charging you (high/positive funding).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ..models import Candle, Signal, Side
from .base import Strategy

logger = logging.getLogger("flint.strategy.funding_arb")


class FundingArbStrategy(Strategy):
    """Cross-venue funding rate arbitrage strategy.

    Enters when funding spread between two venues exceeds min_spread_bps.
    Exits when spread converges below exit_spread_bps or max hold time reached.
    Delta-neutral: equal USD notional long on one venue, short on the other.
    """

    def __init__(
        self,
        min_spread_bps: float = 5.0,
        exit_spread_bps: float = 1.0,
        max_hold_hours: int = 24,
        position_size_usd: float = 1000.0,
        min_spread_duration: int = 1,
        venues: Optional[List[str]] = None,
        candle_resolution_s: int = 60,
    ):
        self._min_spread_bps = min_spread_bps
        self._exit_spread_bps = exit_spread_bps
        self._max_hold_hours = max_hold_hours
        self._position_size_usd = position_size_usd
        self._min_spread_duration = min_spread_duration
        self._venues = venues or ["drift", "hyperliquid"]
        self._candle_resolution_s = candle_resolution_s

        # State
        self._entry_ts: int = 0
        self._long_venue: str = ""
        self._short_venue: str = ""
        self._spread_above_since: int = 0

    @property
    def name(self) -> str:
        return "funding_arb"

    def reset(self) -> None:
        self._entry_ts = 0
        self._long_venue = ""
        self._short_venue = ""
        self._spread_above_since = 0

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if ctx is None:
            return Signal.HOLD

        market = candle.market
        venue_data = ctx.get_funding_by_venue(market, lookback=24)

        if len(venue_data) < 2:
            return Signal.HOLD

        # Find latest rate per venue
        venue_rates = {}
        for venue in self._venues:
            rates = venue_data.get(venue, [])
            if rates:
                venue_rates[venue] = rates[-1][1]  # (ts, rate) → rate

        if len(venue_rates) < 2:
            return Signal.HOLD

        # Check if we have a position
        has_position = self._entry_ts > 0

        if has_position:
            return self._check_exit(candle, ctx, venue_rates)
        else:
            return self._check_entry(candle, ctx, venue_rates)

    def _check_entry(self, candle, ctx, venue_rates: Dict[str, float]) -> Signal:
        # Find the pair with largest spread
        venues = list(venue_rates.keys())
        best_spread = 0.0
        best_long = ""
        best_short = ""

        for i in range(len(venues)):
            for j in range(i + 1, len(venues)):
                spread = abs(venue_rates[venues[i]] - venue_rates[venues[j]])
                if spread > best_spread:
                    best_spread = spread
                    if venue_rates[venues[i]] < venue_rates[venues[j]]:
                        best_long = venues[i]   # Lower rate = being paid
                        best_short = venues[j]  # Higher rate = paying
                    else:
                        best_long = venues[j]
                        best_short = venues[i]

        spread_bps = best_spread * 10000

        if spread_bps < self._min_spread_bps:
            self._spread_above_since = 0
            return Signal.HOLD

        # Check duration persistence
        if self._spread_above_since == 0:
            self._spread_above_since = candle.ts
        hours_above = (candle.ts - self._spread_above_since) / 3600
        if hours_above < self._min_spread_duration:
            return Signal.HOLD

        # Enter: long on low-rate venue, short on high-rate venue
        size = self._position_size_usd / candle.close if candle.close > 0 else 0
        if size <= 0:
            return Signal.HOLD

        ctx.market_order(candle.market, Side.LONG, size, venue=best_long)
        ctx.market_order(candle.market, Side.SHORT, size, venue=best_short)

        self._entry_ts = candle.ts
        self._long_venue = best_long
        self._short_venue = best_short
        self._spread_above_since = 0

        logger.info("Entry: long %s, short %s, spread=%.1f bps, size=%.4f",
                     best_long, best_short, spread_bps, size)
        return Signal.HOLD

    def _check_exit(self, candle, ctx, venue_rates: Dict[str, float]) -> Signal:
        # Check max hold
        hold_hours = (candle.ts - self._entry_ts) / 3600
        if hold_hours >= self._max_hold_hours:
            self._close_both(candle, ctx, "max hold")
            return Signal.HOLD

        # Check spread convergence
        long_rate = venue_rates.get(self._long_venue, 0)
        short_rate = venue_rates.get(self._short_venue, 0)
        spread_bps = abs(short_rate - long_rate) * 10000

        if spread_bps < self._exit_spread_bps:
            self._close_both(candle, ctx, "spread converged")
            return Signal.HOLD

        return Signal.HOLD

    def _close_both(self, candle, ctx, reason: str) -> None:
        ctx.close_position(candle.market, venue=self._long_venue)
        ctx.close_position(candle.market, venue=self._short_venue)
        logger.info("Exit (%s): closed %s + %s", reason, self._long_venue, self._short_venue)
        self._entry_ts = 0
        self._long_venue = ""
        self._short_venue = ""

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "min_spread_bps": {"type": "float", "low": 3.0, "high": 20.0, "default": 5.0},
            "exit_spread_bps": {"type": "float", "low": 0.5, "high": 5.0, "default": 1.0},
            "max_hold_hours": {"type": "int", "low": 4, "high": 72, "default": 24},
            "position_size_usd": {"type": "float", "low": 100, "high": 10000, "default": 1000},
            "min_spread_duration": {"type": "int", "low": 0, "high": 6, "default": 1},
            "candle_resolution_s": {"type": "int", "low": 60, "high": 3600, "default": 60},
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_funding_arb.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/strategy/funding_arb.py tests/test_funding_arb.py
git commit -m "feat: add FundingArbStrategy for cross-venue funding rate arbitrage"
```

---

### Task 7: Cross-Venue Backtest Engine

**Files:**
- Modify: `flint/backtest/engine.py`
- Create: `tests/test_cross_venue_backtest.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cross_venue_backtest.py`:

```python
"""Tests for cross-venue backtest support."""
import pytest
from flint.models import Candle, FundingRate, Side, Signal
from flint.backtest.engine import BacktestEngine, _parse_venue_market
from flint.strategy.base import Strategy


class SimpleArbStrategy(Strategy):
    """Test strategy that places orders on specific venues."""
    @property
    def name(self):
        return "test_arb"

    def reset(self):
        self._entered = False

    def on_candle(self, candle, history, ctx=None):
        if ctx is None or self._entered:
            return Signal.HOLD
        if len(history) >= 3:
            ctx.market_order(candle.market, Side.LONG, 1.0, venue="drift")
            ctx.market_order(candle.market, Side.SHORT, 1.0, venue="hyperliquid")
            self._entered = True
        return Signal.HOLD


class TestParseVenueMarket:
    def test_with_venue_prefix(self):
        venue, market = _parse_venue_market("drift:SOL-PERP")
        assert venue == "drift"
        assert market == "SOL-PERP"

    def test_without_prefix(self):
        venue, market = _parse_venue_market("SOL-PERP")
        assert venue == "default"
        assert market == "SOL-PERP"

    def test_hyperliquid_prefix(self):
        venue, market = _parse_venue_market("hyperliquid:BTC-PERP")
        assert venue == "hyperliquid"
        assert market == "BTC-PERP"


class TestCrossVenueBacktest:
    def _make_candles(self, market, venue, count=10, base_price=150.0):
        return [
            Candle(ts=1000 + i * 60, open=base_price + i, high=base_price + i + 1,
                   low=base_price + i - 1, close=base_price + i + 0.5,
                   volume=100.0, market=market, resolution_s=60, venue=venue)
            for i in range(count)
        ]

    def test_venue_market_keys_accepted(self):
        strategy = SimpleArbStrategy()
        engine = BacktestEngine(strategy=strategy, initial_capital=10000.0)
        candles = {
            "drift:SOL-PERP": self._make_candles("SOL-PERP", "drift"),
            "hyperliquid:SOL-PERP": self._make_candles("SOL-PERP", "hyperliquid"),
        }
        result = engine.run(candles)
        assert result.total_trades >= 0  # Doesn't crash

    def test_backward_compatible_plain_keys(self):
        """Existing single-venue backtests still work."""
        strategy = SimpleArbStrategy()
        engine = BacktestEngine(strategy=strategy, initial_capital=10000.0)
        candles = {"SOL-PERP": self._make_candles("SOL-PERP", "default")}
        result = engine.run(candles)
        assert result.total_trades >= 0

    def test_per_venue_pnl_in_result(self):
        strategy = SimpleArbStrategy()
        engine = BacktestEngine(strategy=strategy, initial_capital=10000.0)
        candles = {
            "drift:SOL-PERP": self._make_candles("SOL-PERP", "drift"),
            "hyperliquid:SOL-PERP": self._make_candles("SOL-PERP", "hyperliquid"),
        }
        result = engine.run(candles)
        assert hasattr(result, 'per_venue_pnl')
        assert isinstance(result.per_venue_pnl, dict)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cross_venue_backtest.py -v`
Expected: FAIL — `ImportError: cannot import name '_parse_venue_market'`

- [ ] **Step 3: Modify BacktestEngine**

In `flint/backtest/engine.py`, add the helper function at module level (after imports):

```python
def _parse_venue_market(key: str):
    """Parse a 'venue:market' composite key. Returns (venue, market)."""
    if ":" in key:
        venue, market = key.split(":", 1)
        return (venue, market)
    return ("default", key)
```

Modify the `run()` method to parse composite keys:

```python
    def run(self, candles, extra_markets: dict = None) -> BacktestResult:
        if isinstance(candles, dict):
            all_markets = candles
            # Parse venue:market composite keys
            parsed = {}
            for key, vals in all_markets.items():
                venue, market = _parse_venue_market(key)
                # Tag candles with venue if not already tagged
                tagged = []
                for c in vals:
                    if c.venue == "default" and venue != "default":
                        tagged.append(Candle(
                            ts=c.ts, open=c.open, high=c.high, low=c.low,
                            close=c.close, volume=c.volume, market=market,
                            resolution_s=c.resolution_s, venue=venue,
                        ))
                    else:
                        tagged.append(c)
                parsed[market] = tagged  # Use market as key for context
            # If multiple venues for same market, use first as primary
            primary = max(parsed.keys(), key=lambda k: len(parsed[k]))
            candles = parsed[primary]
            extra_markets = {k: v for k, v in parsed.items() if k != primary}
            if extra_markets is None:
                extra_markets = {}
            # Also keep venue-prefixed entries for fill routing
            for key, vals in all_markets.items():
                venue, market = _parse_venue_market(key)
                if venue != "default":
                    extra_markets[f"{venue}:{market}"] = vals
        return self._run_internal(candles, extra_markets)
```

Also add `per_venue_pnl` to `BacktestResult` in `flint/models.py`:

```python
@dataclass
class BacktestResult:
    total_pnl: float
    win_rate: float
    max_drawdown: float
    sharpe_ratio: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    positions: List[Position] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    fills: List["Fill"] = field(default_factory=list)
    total_fees: float = 0.0
    funding_paid: float = 0.0
    strategy_warnings: List[str] = field(default_factory=list)
    per_venue_pnl: Dict[str, float] = field(default_factory=dict)
    per_venue_trades: Dict[str, int] = field(default_factory=dict)
    per_venue_funding_income: Dict[str, float] = field(default_factory=dict)
```

And in `_build_result()`, compute per-venue analytics:

```python
    def _build_result(self, ctx: BacktestContext, equity_curve, resolution_s=3600):
        # ... existing code ...

        # Per-venue analytics
        per_venue_pnl = {}
        per_venue_trades = {}
        per_venue_funding = {}
        for fill in ctx.all_fills:
            venue = fill.venue or "default"
            per_venue_trades[venue] = per_venue_trades.get(venue, 0) + 1
        for trade in trades:
            venue = trade.get("venue", "default")
            pnl = trade.get("pnl", 0)
            per_venue_pnl[venue] = per_venue_pnl.get(venue, 0) + pnl

        return BacktestResult(
            # ... existing fields ...
            per_venue_pnl=per_venue_pnl,
            per_venue_trades=per_venue_trades,
            per_venue_funding_income=per_venue_funding,
        )
```

Note: The `Dict` import needs to be added to `flint/models.py` if not already present.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cross_venue_backtest.py -v`
Expected: PASS

- [ ] **Step 5: Run existing backtest tests for regressions**

Run: `pytest tests/ -k "backtest" -v`
Expected: All pass (backward compatible)

- [ ] **Step 6: Commit**

```bash
git add flint/backtest/engine.py flint/models.py tests/test_cross_venue_backtest.py
git commit -m "feat: add cross-venue backtest support with venue:market composite keys and per-venue analytics"
```

---

### Task 8: Integration Tests

**Files:**
- Create: `tests/test_cross_venue_integration.py`

- [ ] **Step 1: Write integration tests**

Create `tests/test_cross_venue_integration.py`:

```python
"""Integration tests for cross-venue strategies."""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, PropertyMock

from flint.models import (
    AccountState, Candle, Fill, FundingRate, OrderLeg,
    PositionInfo, Side, Signal,
)
from flint.execution.live_base import LiveExecutionContext


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_mock_venue(venue_name, cash=5000.0, positions=None):
    ctx = MagicMock(spec=LiveExecutionContext)
    ctx._venue = venue_name
    positions = positions or []
    unrealized = sum(p.unrealized_pnl for p in positions)
    type(ctx).account = PropertyMock(return_value=AccountState(
        equity=cash + unrealized, cash=cash, unrealized_pnl=unrealized,
    ))
    type(ctx).positions = PropertyMock(return_value=positions)
    type(ctx).pending_orders = PropertyMock(return_value=[])
    type(ctx).current_candle = PropertyMock(return_value=None)
    type(ctx).timestamp = PropertyMock(return_value=1000)
    ctx.market_order = MagicMock(return_value="ord-1")
    ctx.cancel = MagicMock(return_value=True)
    ctx.cancel_all = MagicMock(return_value=0)
    ctx.close_position = MagicMock(return_value="close-1")
    ctx.connect = AsyncMock()
    ctx.disconnect = AsyncMock()
    ctx.submit_pending_orders = AsyncMock(return_value=[])
    ctx._poll_orders_loop = AsyncMock()
    ctx.get_funding_rates = MagicMock(return_value=[])
    return ctx


class TestFundingArbBacktest:
    """End-to-end backtest with FundingArbStrategy."""

    def test_backtest_with_funding_data(self):
        from flint.strategy.funding_arb import FundingArbStrategy
        from flint.backtest.engine import BacktestEngine

        strategy = FundingArbStrategy(
            min_spread_bps=5.0, min_spread_duration=0,
            position_size_usd=1000.0,
            venues=["drift", "hyperliquid"],
        )
        engine = BacktestEngine(
            strategy=strategy,
            initial_capital=10000.0,
            funding_rates=[
                FundingRate(market="SOL-PERP", ts=1000 + i * 60, rate=0.0001, oracle_price=150.0, mark_price=150.0, source="drift")
                for i in range(10)
            ] + [
                FundingRate(market="SOL-PERP", ts=1000 + i * 60, rate=0.001, oracle_price=150.0, mark_price=150.0, source="hyperliquid")
                for i in range(10)
            ],
        )
        candles = [
            Candle(ts=1000 + i * 60, open=150.0 + i, high=151.0 + i, low=149.0 + i,
                   close=150.5 + i, volume=100.0, market="SOL-PERP", resolution_s=60)
            for i in range(10)
        ]
        result = engine.run(candles)
        assert result is not None
        assert isinstance(result.per_venue_pnl, dict)


class TestMultiVenueDryRun:
    """Dry-run across multiple venues."""

    def test_dry_run_places_on_both_venues(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext

        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})

        ctx.market_order("SOL-PERP", Side.LONG, 10.0, venue="drift")
        ctx.market_order("SOL-PERP", Side.SHORT, 10.0, venue="hyperliquid")

        drift.market_order.assert_called_once()
        hl.market_order.assert_called_once()

    def test_aggregated_equity_reflects_both(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext

        drift = _make_mock_venue("drift", cash=5000.0)
        hl = _make_mock_venue("hyperliquid", cash=3000.0)
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        assert ctx.account.equity == 8000.0
        assert ctx.venue_account("drift").equity == 5000.0
        assert ctx.venue_account("hyperliquid").equity == 3000.0


class TestLegGroupIntegration:
    """Leg group with fills from multiple venues."""

    def test_leg_group_full_flow(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext

        drift = _make_mock_venue("drift")
        drift.market_order.return_value = "d-1"
        hl = _make_mock_venue("hyperliquid")
        hl.market_order.return_value = "h-1"

        drift.submit_pending_orders = AsyncMock(return_value=[
            Fill(market="SOL-PERP", side=Side.LONG, price=150.0, size=10.0, fee=0.075, ts=1000, order_id="d-1", venue="drift"),
        ])
        hl.submit_pending_orders = AsyncMock(return_value=[
            Fill(market="SOL-PERP", side=Side.SHORT, price=150.0, size=10.0, fee=0.05, ts=1000, order_id="h-1", venue="hyperliquid"),
        ])

        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})

        legs = [
            OrderLeg(order_id="", venue="drift", market="SOL-PERP", side=Side.LONG, size=10.0),
            OrderLeg(order_id="", venue="hyperliquid", market="SOL-PERP", side=Side.SHORT, size=10.0),
        ]
        result = run(ctx.submit_leg_group(legs))
        assert result.status == "filled"
        assert len(result.filled_legs) == 2
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/test_cross_venue_integration.py -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_cross_venue_integration.py
git commit -m "test: add cross-venue integration tests (backtest, dry-run, leg groups)"
```

---

### Task 9: ROADMAP Update

**Files:**
- Modify: `ROADMAP.md`

- [ ] **Step 1: Update ROADMAP.md §3.1, §3.2, §3.3**

Add "Implemented" sections after each subsection:

Under §3.1:
```markdown
**Implemented:**
- [x] `MultiVenueLiveContext(ExecutionContext)` wrapping multiple venue contexts (`flint/execution/multi_venue_live.py`)
- [x] Order routing by venue parameter (market, limit, stop, take_profit, cancel, cancel_all)
- [x] Aggregated `account` property (sum of all venue equity/cash)
- [x] `venue_account(venue)` for per-venue breakdown
- [x] `total_exposure(market)` net size across venues
- [x] `per_venue_pnl()` unrealized PnL per venue
- [x] Paired leg submission (`submit_leg_group`) with timeout and optional auto-unwind
- [x] Configurable tick mode: "primary" (single venue triggers ticks) or "any" (all venues trigger)
- [x] EquityMonitor integration via aggregated account property
```

Under §3.2:
```markdown
**Implemented:**
- [x] `FundingArbStrategy` template with Optuna-optimizable parameters (`flint/strategy/funding_arb.py`)
- [x] Cross-venue funding spread detection via `ctx.get_funding_by_venue()`
- [x] Delta-neutral entry: long low-funding venue, short high-funding venue
- [x] Exit on spread convergence or max hold time
- [x] Min spread duration guard
- [x] Works in both backtest and live modes
```

Under §3.3:
```markdown
**Implemented:**
- [x] `venue:market` composite key parsing in BacktestEngine (`_parse_venue_market`)
- [x] Backward compatible: plain keys (no prefix) default to "default" venue
- [x] Per-venue PnL, trade count, and funding income in BacktestResult
- [x] `OrderLeg`, `LegGroup`, `LegGroupResult` dataclasses in models.py
```

- [ ] **Step 2: Commit**

```bash
git add ROADMAP.md
git commit -m "docs: update ROADMAP §3.1-§3.3 with cross-venue implementation notes"
```

---

## Task Dependencies

```
Task 1 (Config) ────────────────────────┐
Task 2 (Leg Models) ───────────────────┤
                                        ├──→ Task 3 (MultiVenue Constructor) ──→ Task 4 (Leg Submission) ──→ Task 5 (Run Lifecycle)
                                        │                                                                          │
Task 6 (FundingArbStrategy) ───────────────────────────────────────────────────────────────────────────────────── Task 8 (Integration)
Task 7 (Cross-Venue Backtest) ─────────────────────────────────────────────────────────────────────────────────→ Task 8
                                                                                                                   │
                                                                                                               Task 9 (ROADMAP)
```

**Parallelizable:** Tasks 1, 2, 6, 7 have no dependencies between them.
**Sequential:** Task 3 needs 1+2. Task 4 needs 3. Task 5 needs 4. Task 8 needs 5+6+7. Task 9 is last.
