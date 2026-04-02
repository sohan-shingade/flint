# Transaction Cost Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-venue transaction cost modeling to capture priority fees, Jito tips, and network costs beyond exchange fees — deducted per fill in backtests and accessible to strategies via `ctx.estimate_cost()`.

**Architecture:** `TxCostModel` ABC with venue-specific implementations (Solana, Hyperliquid, CEX). `CostEstimate` dataclass provides typed breakdown. Integrated into `FillPipeline` via optional parameter, deducted in `BacktestContext._apply_fill()`, and exposed through `ctx.estimate_cost()`.

**Tech Stack:** Existing execution infrastructure, no new dependencies.

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `flint/execution/tx_costs.py` | CostEstimate, TxCostModel ABC, venue implementations, factory | Create |
| `flint/models.py` | Add `tx_cost` to Fill, `total_tx_costs` to BacktestResult | Modify |
| `flint/config.py` | 3 tx cost config fields | Modify |
| `flint/execution/fill_models.py` | Add `tx_cost_model` to FillPipeline | Modify |
| `flint/execution/backtest_context.py` | Deduct tx_cost in _apply_fill() | Modify |
| `flint/backtest/engine.py` | Add total_tx_costs to _build_result() | Modify |
| `flint/execution/context.py` | Add estimate_cost() default | Modify |
| `flint/execution/live_base.py` | Add tx_cost_model param, override estimate_cost() | Modify |
| `flint/execution/multi_venue_live.py` | Delegate estimate_cost() | Modify |
| `ROADMAP.md` | Mark §4.3 as implemented | Modify |
| `tests/test_tx_costs.py` | CostEstimate + model tests | Create |
| `tests/test_tx_cost_config.py` | Config field tests | Create |
| `tests/test_tx_cost_integration.py` | Fill pipeline + backtest integration tests | Create |

---

### Task 1: Config Additions

**Files:**
- Modify: `flint/config.py`
- Create: `tests/test_tx_cost_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tx_cost_config.py`:

```python
"""Tests for transaction cost config fields."""
from flint.config import FlintConfig


class TestTxCostConfig:
    def test_defaults(self):
        config = FlintConfig()
        assert config.tx_cost_priority_fee_lamports == 5000
        assert config.tx_cost_jito_tip_lamports == 10000
        assert config.tx_cost_sol_price_usd == 150.0

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_TX_COST_PRIORITY_FEE_LAMPORTS", "8000")
        monkeypatch.setenv("FLINT_TX_COST_JITO_TIP_LAMPORTS", "15000")
        monkeypatch.setenv("FLINT_TX_COST_SOL_PRICE_USD", "200.0")
        config = FlintConfig()
        assert config.tx_cost_priority_fee_lamports == 8000
        assert config.tx_cost_jito_tip_lamports == 15000
        assert config.tx_cost_sol_price_usd == 200.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tx_cost_config.py -v`
Expected: FAIL

- [ ] **Step 3: Add config fields**

In `flint/config.py`, add after the calibration section (after `calibration_min_fills`):

```python
    # --- Transaction costs ---
    tx_cost_priority_fee_lamports: int = 5000
    tx_cost_jito_tip_lamports: int = 10000
    tx_cost_sol_price_usd: float = 150.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tx_cost_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/config.py tests/test_tx_cost_config.py
git commit -m "feat: add transaction cost config fields (priority_fee, jito_tip, sol_price)"
```

---

### Task 2: CostEstimate + TxCostModel + Venue Implementations

**Files:**
- Create: `flint/execution/tx_costs.py`
- Create: `tests/test_tx_costs.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tx_costs.py`:

```python
"""Tests for CostEstimate, TxCostModel implementations."""
import pytest
from flint.execution.tx_costs import (
    CostEstimate, TxCostModel, SolanaTxCostModel,
    HyperliquidTxCostModel, CexTxCostModel, get_tx_cost_model,
)


class TestCostEstimate:
    def test_total_sums_all_components(self):
        est = CostEstimate(exchange_fee=0.75, network_fee=0.001, bundle_tip=0.01, impact_est=0.50)
        assert abs(est.total - 1.261) < 0.001

    def test_zero_components(self):
        est = CostEstimate(exchange_fee=0.0, network_fee=0.0, bundle_tip=0.0, impact_est=0.0)
        assert est.total == 0.0

    def test_to_dict(self):
        est = CostEstimate(exchange_fee=0.75, network_fee=0.001, bundle_tip=0.01, impact_est=0.50)
        d = est.to_dict()
        assert "exchange_fee" in d
        assert "total" in d
        assert d["total"] == est.total


class TestSolanaTxCostModel:
    def test_default_costs(self):
        model = SolanaTxCostModel(sol_price_usd=150.0)
        est = model.estimate("SOL-PERP", 10.0, 150.0)
        assert est.exchange_fee > 0  # 10 bps default
        assert est.network_fee > 0   # Priority fee
        assert est.bundle_tip > 0    # Jito tip

    def test_lamport_to_usd_conversion(self):
        model = SolanaTxCostModel(
            priority_fee_lamports=1_000_000_000,  # 1 SOL
            jito_tip_lamports=0,
            sol_price_usd=100.0,
            exchange_fee_bps=0,
        )
        est = model.estimate("SOL-PERP", 10.0, 150.0)
        assert abs(est.network_fee - 100.0) < 0.01  # 1 SOL * $100

    def test_urgent_uses_higher_percentile(self):
        historical = {"p50": 5000, "p90": 50000}
        model = SolanaTxCostModel(
            sol_price_usd=150.0, historical_fees=historical,
        )
        normal = model.estimate("SOL-PERP", 10.0, 150.0, urgency="normal")
        urgent = model.estimate("SOL-PERP", 10.0, 150.0, urgency="urgent")
        assert urgent.network_fee > normal.network_fee

    def test_venue_property(self):
        model = SolanaTxCostModel()
        assert model.venue == "drift"


class TestHyperliquidTxCostModel:
    def test_negligible_network_cost(self):
        model = HyperliquidTxCostModel()
        est = model.estimate("SOL-PERP", 10.0, 150.0)
        assert est.network_fee < 0.01  # Negligible
        assert est.bundle_tip == 0.0
        assert est.exchange_fee > 0

    def test_venue_property(self):
        model = HyperliquidTxCostModel()
        assert model.venue == "hyperliquid"


class TestCexTxCostModel:
    def test_no_network_costs(self):
        model = CexTxCostModel()
        est = model.estimate("SOL-PERP", 10.0, 150.0)
        assert est.network_fee == 0.0
        assert est.bundle_tip == 0.0
        assert est.exchange_fee > 0

    def test_venue_property(self):
        model = CexTxCostModel(venue_name="binance")
        assert model.venue == "binance"


class TestFactory:
    def test_drift_returns_solana(self):
        model = get_tx_cost_model("drift")
        assert isinstance(model, SolanaTxCostModel)

    def test_hyperliquid_returns_hl(self):
        model = get_tx_cost_model("hyperliquid")
        assert isinstance(model, HyperliquidTxCostModel)

    def test_binance_returns_cex(self):
        model = get_tx_cost_model("binance")
        assert isinstance(model, CexTxCostModel)

    def test_unknown_returns_cex(self):
        model = get_tx_cost_model("unknown_venue")
        assert isinstance(model, CexTxCostModel)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tx_costs.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement tx_costs.py**

Create `flint/execution/tx_costs.py`:

```python
"""Transaction cost models — per-venue cost estimation.

Each venue has its own cost profile:
- Solana (Drift): priority fees + Jito tips
- Hyperliquid: negligible L1 settlement
- CEXes: no network costs
"""
from __future__ import annotations

import abc
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger("flint.tx_costs")


@dataclass
class CostEstimate:
    """Typed cost breakdown for a single order."""
    exchange_fee: float      # Taker/maker fee in USD
    network_fee: float       # Priority fee / gas in USD
    bundle_tip: float        # Jito tip / MEV tip in USD
    impact_est: float        # Estimated market impact in USD

    @property
    def total(self) -> float:
        return self.exchange_fee + self.network_fee + self.bundle_tip + self.impact_est

    def to_dict(self) -> dict:
        return {
            "exchange_fee": self.exchange_fee,
            "network_fee": self.network_fee,
            "bundle_tip": self.bundle_tip,
            "impact_est": self.impact_est,
            "total": self.total,
        }


class TxCostModel(abc.ABC):
    """Per-venue transaction cost estimator."""

    @abc.abstractmethod
    def estimate(
        self,
        market: str,
        size: float,
        price: float,
        urgency: str = "normal",
    ) -> CostEstimate:
        ...

    @property
    @abc.abstractmethod
    def venue(self) -> str:
        ...


class SolanaTxCostModel(TxCostModel):
    """Transaction costs for Solana-based venues (Drift).

    Includes priority fees and Jito tips. Converts lamports to USD
    using SOL price.
    """

    def __init__(
        self,
        priority_fee_lamports: int = 5000,
        jito_tip_lamports: int = 10000,
        sol_price_usd: float = 150.0,
        exchange_fee_bps: float = 10.0,
        historical_fees: Optional[Dict[str, int]] = None,
    ):
        self._priority_fee_lamports = priority_fee_lamports
        self._jito_tip_lamports = jito_tip_lamports
        self._sol_price_usd = sol_price_usd
        self._exchange_fee_bps = exchange_fee_bps
        self._historical_fees = historical_fees

    @property
    def venue(self) -> str:
        return "drift"

    def estimate(self, market, size, price, urgency="normal"):
        exchange_fee = size * price * (self._exchange_fee_bps / 10000)

        if self._historical_fees:
            if urgency == "urgent":
                priority = self._historical_fees.get("p90", self._priority_fee_lamports)
            else:
                priority = self._historical_fees.get("p50", self._priority_fee_lamports)
        else:
            priority = self._priority_fee_lamports

        priority_usd = priority / 1e9 * self._sol_price_usd
        jito_usd = self._jito_tip_lamports / 1e9 * self._sol_price_usd

        return CostEstimate(
            exchange_fee=exchange_fee,
            network_fee=priority_usd,
            bundle_tip=jito_usd,
            impact_est=0.0,
        )


class HyperliquidTxCostModel(TxCostModel):
    """Transaction costs for Hyperliquid. Negligible L1 settlement."""

    def __init__(
        self,
        l1_cost_usd: float = 0.001,
        exchange_fee_bps: float = 3.5,
    ):
        self._l1_cost_usd = l1_cost_usd
        self._exchange_fee_bps = exchange_fee_bps

    @property
    def venue(self) -> str:
        return "hyperliquid"

    def estimate(self, market, size, price, urgency="normal"):
        return CostEstimate(
            exchange_fee=size * price * (self._exchange_fee_bps / 10000),
            network_fee=self._l1_cost_usd,
            bundle_tip=0.0,
            impact_est=0.0,
        )


class CexTxCostModel(TxCostModel):
    """Transaction costs for centralized exchanges. No network costs."""

    def __init__(self, exchange_fee_bps: float = 5.0, venue_name: str = "cex"):
        self._exchange_fee_bps = exchange_fee_bps
        self._venue_name = venue_name

    @property
    def venue(self) -> str:
        return self._venue_name

    def estimate(self, market, size, price, urgency="normal"):
        return CostEstimate(
            exchange_fee=size * price * (self._exchange_fee_bps / 10000),
            network_fee=0.0,
            bundle_tip=0.0,
            impact_est=0.0,
        )


def get_tx_cost_model(venue: str, **kwargs) -> TxCostModel:
    """Get the appropriate TxCostModel for a venue."""
    if venue == "drift":
        return SolanaTxCostModel(**kwargs)
    elif venue == "hyperliquid":
        return HyperliquidTxCostModel(**kwargs)
    elif venue in ("binance", "okx", "bybit"):
        return CexTxCostModel(venue_name=venue, **kwargs)
    else:
        return CexTxCostModel(venue_name=venue, **kwargs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tx_costs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/execution/tx_costs.py tests/test_tx_costs.py
git commit -m "feat: add CostEstimate, TxCostModel ABC, and venue implementations"
```

---

### Task 3: Fill.tx_cost + BacktestResult.total_tx_costs

**Files:**
- Modify: `flint/models.py`
- Create: `tests/test_tx_cost_integration.py` (partial)

- [ ] **Step 1: Write the failing test**

Create `tests/test_tx_cost_integration.py`:

```python
"""Tests for tx_cost integration across Fill, BacktestResult, pipeline."""
import pytest
from flint.models import Fill, BacktestResult, Side


class TestFillTxCost:
    def test_default_zero(self):
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=150.0, size=10.0, fee=0.075, ts=1000)
        assert fill.tx_cost == 0.0

    def test_with_tx_cost(self):
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=150.0, size=10.0,
                    fee=0.075, ts=1000, tx_cost=0.005)
        assert fill.tx_cost == 0.005


class TestBacktestResultTxCosts:
    def test_default_zero(self):
        result = BacktestResult(
            total_pnl=100, win_rate=0.5, max_drawdown=0.1, sharpe_ratio=1.5,
            total_trades=10, winning_trades=5, losing_trades=5,
        )
        assert result.total_tx_costs == 0.0

    def test_with_tx_costs(self):
        result = BacktestResult(
            total_pnl=100, win_rate=0.5, max_drawdown=0.1, sharpe_ratio=1.5,
            total_trades=10, winning_trades=5, losing_trades=5,
            total_tx_costs=0.05,
        )
        assert result.total_tx_costs == 0.05
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tx_cost_integration.py -v`
Expected: FAIL — Fill has no `tx_cost` field

- [ ] **Step 3: Add fields to models.py**

In `flint/models.py`:

Add `tx_cost: float = 0.0` to the Fill dataclass after `impact_bps`:
```python
    impact_bps: float = 0.0
    tx_cost: float = 0.0
```

Add `total_tx_costs: float = 0.0` to BacktestResult after `per_venue_funding_income`:
```python
    per_venue_funding_income: Dict[str, float] = field(default_factory=dict)
    total_tx_costs: float = 0.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tx_cost_integration.py -v`
Expected: PASS

- [ ] **Step 5: Run existing tests for regressions**

Run: `pytest tests/ -k "fill or backtest or models" --tb=short -q`
Expected: All pass — new fields have defaults

- [ ] **Step 6: Commit**

```bash
git add flint/models.py tests/test_tx_cost_integration.py
git commit -m "feat: add tx_cost field to Fill and total_tx_costs to BacktestResult"
```

---

### Task 4: BacktestContext + Engine Integration

**Files:**
- Modify: `flint/execution/backtest_context.py`
- Modify: `flint/backtest/engine.py`
- Modify: `tests/test_tx_cost_integration.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tx_cost_integration.py`:

```python
from flint.backtest.engine import BacktestEngine
from flint.models import Candle, Signal
from flint.strategy.base import Strategy
from flint.execution.tx_costs import SolanaTxCostModel
from flint.execution.fill_models import FillPipeline


class SimpleStrategy(Strategy):
    @property
    def name(self):
        return "simple"

    def reset(self):
        self._bought = False

    def on_candle(self, candle, history, ctx=None):
        if ctx and not self._bought and len(history) >= 2:
            ctx.market_order(candle.market, Side.LONG, 1.0)
            self._bought = True
        return Signal.HOLD


class TestBacktestTxCostDeduction:
    def test_tx_cost_deducted_from_equity(self):
        strategy = SimpleStrategy()
        tx_model = SolanaTxCostModel(
            priority_fee_lamports=1_000_000_000,  # 1 SOL = $150
            jito_tip_lamports=1_000_000_000,      # 1 SOL = $150
            sol_price_usd=150.0,
            exchange_fee_bps=0,  # Zero exchange fee to isolate tx cost
        )
        pipeline = FillPipeline(
            impact_coefficient=0.0,
            latency_enabled=False,
            tx_cost_model=tx_model,
        )
        engine = BacktestEngine(
            strategy=strategy,
            initial_capital=10000.0,
            fill_model=pipeline,
            fee_rate=0.0,
        )
        candles = [
            Candle(ts=1000 + i * 60, open=150.0, high=151.0, low=149.0,
                   close=150.0, volume=10000.0, market="SOL-PERP", resolution_s=60)
            for i in range(10)
        ]
        result = engine.run(candles)
        # Should have tx_costs > 0 (priority + jito = $300)
        assert result.total_tx_costs > 0

    def test_no_tx_cost_without_model(self):
        strategy = SimpleStrategy()
        engine = BacktestEngine(
            strategy=strategy,
            initial_capital=10000.0,
        )
        candles = [
            Candle(ts=1000 + i * 60, open=150.0, high=151.0, low=149.0,
                   close=150.0, volume=10000.0, market="SOL-PERP", resolution_s=60)
            for i in range(10)
        ]
        result = engine.run(candles)
        assert result.total_tx_costs == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tx_cost_integration.py::TestBacktestTxCostDeduction -v`
Expected: FAIL

- [ ] **Step 3: Integrate tx_cost into BacktestContext and Engine**

**In `flint/execution/backtest_context.py`:**

In `__init__`, add after `self._total_fees = 0.0`:
```python
        self._total_tx_costs = 0.0
```

In `_apply_fill()`, after `self._debit_cash(fill.fee, ...)` line, add:
```python
        if fill.tx_cost > 0:
            self._total_tx_costs += fill.tx_cost
            # Debit from venue cash
            if self._allocator:
                self._allocator.debit(fill.venue or "default", fill.tx_cost)
            else:
                self._cash -= fill.tx_cost
```

Add property after `total_fees`:
```python
    @property
    def total_tx_costs(self) -> float:
        return self._total_tx_costs
```

**In `flint/execution/fill_models.py`:**

Add `tx_cost_model=None` parameter to `FillPipeline.__init__()`:
```python
    def __init__(
        self,
        impact_coefficient: float = 0.005,
        fallback_bps: float = 5.0,
        base_latency_s: float = 1.0,
        latency_jitter_s: float = 0.5,
        latency_seed: Optional[int] = None,
        latency_enabled: bool = True,
        tx_cost_model=None,
    ):
        # ... existing init ...
        self._tx_cost_model = tx_cost_model
```

In `fill_market()`, when creating the Fill, compute tx_cost:
```python
        tx_cost = 0.0
        if self._tx_cost_model:
            cost_est = self._tx_cost_model.estimate(
                order.market, decision.fill_size, decision.fill_price,
            )
            tx_cost = cost_est.network_fee + cost_est.bundle_tip
```

Add `tx_cost=tx_cost` to the Fill constructor call.

**In `flint/backtest/engine.py`:**

In `_build_result()`, compute total_tx_costs and add to BacktestResult:
```python
        total_tx_costs = sum(f.tx_cost for f in ctx.all_fills)
```
Add `total_tx_costs=total_tx_costs,` to the BacktestResult constructor.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tx_cost_integration.py -v`
Expected: PASS

- [ ] **Step 5: Run existing tests for regressions**

Run: `pytest tests/ -k "backtest" --tb=short -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add flint/execution/backtest_context.py flint/execution/fill_models.py flint/backtest/engine.py tests/test_tx_cost_integration.py
git commit -m "feat: integrate tx_cost into BacktestContext, FillPipeline, and BacktestEngine"
```

---

### Task 5: estimate_cost() on ExecutionContext + LiveExecutionContext + MultiVenueLiveContext

**Files:**
- Modify: `flint/execution/context.py`
- Modify: `flint/execution/live_base.py`
- Modify: `flint/execution/multi_venue_live.py`
- Modify: `tests/test_tx_cost_integration.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tx_cost_integration.py`:

```python
from unittest.mock import MagicMock, PropertyMock, AsyncMock
from flint.execution.live_base import LiveExecutionContext
from flint.models import AccountState, PositionInfo


def _make_mock_venue(venue_name, cash=5000.0):
    ctx = MagicMock(spec=LiveExecutionContext)
    ctx._venue = venue_name
    type(ctx).account = PropertyMock(return_value=AccountState(equity=cash, cash=cash))
    type(ctx).positions = PropertyMock(return_value=[])
    type(ctx).pending_orders = PropertyMock(return_value=[])
    type(ctx).current_candle = PropertyMock(return_value=None)
    type(ctx).timestamp = PropertyMock(return_value=1000)
    ctx.estimate_cost = MagicMock(return_value=None)
    ctx.connect = AsyncMock()
    ctx.disconnect = AsyncMock()
    ctx.submit_pending_orders = AsyncMock(return_value=[])
    ctx._poll_orders_loop = AsyncMock()
    return ctx


class TestEstimateCost:
    def test_default_returns_none(self):
        from flint.execution.context import ExecutionContext
        # ExecutionContext is ABC, but estimate_cost has a default
        # Test via a concrete subclass
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        ctx = MultiVenueLiveContext(contexts={"drift": drift})
        # Mock returns None by default
        result = ctx.estimate_cost("SOL-PERP", 10.0, venue="drift")
        assert result is None

    def test_multi_venue_delegates_to_venue(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        from flint.execution.tx_costs import CostEstimate
        drift = _make_mock_venue("drift")
        drift.estimate_cost.return_value = CostEstimate(
            exchange_fee=0.75, network_fee=0.001, bundle_tip=0.01, impact_est=0.0,
        )
        ctx = MultiVenueLiveContext(contexts={"drift": drift})
        result = ctx.estimate_cost("SOL-PERP", 10.0, venue="drift")
        assert result is not None
        assert result.total > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tx_cost_integration.py::TestEstimateCost -v`
Expected: FAIL — `estimate_cost` not defined or wrong signature

- [ ] **Step 3: Add estimate_cost() to all three classes**

**In `flint/execution/context.py`**, add after `log()` method:
```python
    def estimate_cost(self, market: str, size: float, venue: str = "default"):
        """Estimate total execution cost before trading. Returns CostEstimate or None."""
        return None
```

**In `flint/execution/live_base.py`**, add `tx_cost_model=None` to constructor params (after `notification_manager`):
```python
        tx_cost_model=None,
```
Store it:
```python
        self._tx_cost_model = tx_cost_model
```
Override estimate_cost:
```python
    def estimate_cost(self, market: str, size: float, venue: str = "default"):
        if self._tx_cost_model is None:
            return None
        price = self._current_candle.close if self._current_candle else 0
        if price <= 0:
            return None
        return self._tx_cost_model.estimate(market, size, price)
```

**In `flint/execution/multi_venue_live.py`**, add after existing methods:
```python
    def estimate_cost(self, market: str, size: float, venue: str = "default"):
        target = self._resolve_venue(venue)
        ctx = self._contexts.get(target)
        if ctx:
            return ctx.estimate_cost(market, size, venue=target)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tx_cost_integration.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite for regressions**

Run: `pytest tests/ --tb=short -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add flint/execution/context.py flint/execution/live_base.py flint/execution/multi_venue_live.py tests/test_tx_cost_integration.py
git commit -m "feat: add estimate_cost() to ExecutionContext, LiveExecutionContext, MultiVenueLiveContext"
```

---

### Task 6: ROADMAP Update

**Files:**
- Modify: `ROADMAP.md`

- [ ] **Step 1: Update ROADMAP.md §4.3**

Find the §4.3 Transaction Cost Model section and add after the existing checklist items:

```markdown
**Implemented:**
- [x] `TxCostModel` ABC with per-venue implementations (`flint/execution/tx_costs.py`)
- [x] `SolanaTxCostModel` — priority fees + Jito tips with lamport→USD conversion
- [x] `HyperliquidTxCostModel` — negligible L1 settlement cost
- [x] `CexTxCostModel` — zero network costs for CEXes
- [x] `CostEstimate` dataclass with typed breakdown and `.total` property
- [x] `get_tx_cost_model()` factory for venue-based model selection
- [x] `Fill.tx_cost` field for per-fill network cost tracking
- [x] `BacktestResult.total_tx_costs` for aggregate cost reporting
- [x] `FillPipeline` integration via optional `tx_cost_model` parameter
- [x] `ctx.estimate_cost()` for pre-trade cost estimation on all context types
- [x] Config: priority fee, Jito tip, SOL price defaults
```

- [ ] **Step 2: Commit**

```bash
git add ROADMAP.md
git commit -m "docs: update ROADMAP §4.3 with transaction cost model implementation notes"
```

---

## Task Dependencies

```
Task 1 (Config) ──────────────────┐
                                   ├──→ Task 4 (Backtest Integration)
Task 2 (TxCostModel + impls) ────┤                    │
                                   │                    ├──→ Task 5 (estimate_cost)
Task 3 (Fill + Result fields) ────┘                    │
                                                       └──→ Task 6 (ROADMAP)
```

**Parallelizable:** Tasks 1, 2, 3 have no dependencies between them.
**Sequential:** Task 4 needs 1+2+3. Task 5 needs 4. Task 6 is last.
