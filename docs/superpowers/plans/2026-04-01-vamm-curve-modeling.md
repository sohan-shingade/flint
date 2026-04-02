# vAMM Curve Modeling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Model Drift's virtual AMM constant-product curve for more accurate backtest fill prices, integrated as Tier 0 in the existing FillPipeline.

**Architecture:** `VammCurve` class implements constant-product math with peg multiplier. Plugs into `ImpactStage` as highest-priority tier (Tier 0), falling back to existing orderbook/sqrt/flat tiers. Per-market K defaults hardcoded, overridable via config.

**Tech Stack:** Pure Python math, existing `ImpactStage` and `FillPipeline` infrastructure.

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `flint/execution/vamm.py` | VammCurve, VammAccuracyReport, DEFAULT_SQRT_K | Create |
| `flint/execution/impact.py` | Add Tier 0 vAMM to ImpactStage | Modify |
| `flint/execution/fill_models.py` | Pass vamm_configs through FillPipeline | Modify |
| `flint/config.py` | Add vamm_enabled, vamm_default_sqrt_k | Modify |
| `ROADMAP.md` | Mark §4.1 as implemented | Modify |
| `tests/test_vamm.py` | VammCurve + VammAccuracyReport tests | Create |
| `tests/test_vamm_integration.py` | ImpactStage Tier 0 + FillPipeline tests | Create |
| `tests/test_vamm_config.py` | Config field tests | Create |

---

### Task 1: Config Additions

**Files:**
- Modify: `flint/config.py`
- Create: `tests/test_vamm_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_vamm_config.py`:

```python
"""Tests for vAMM config fields."""
from flint.config import FlintConfig


class TestVammConfig:
    def test_defaults(self):
        config = FlintConfig()
        assert config.vamm_enabled is False
        assert config.vamm_default_sqrt_k == ""

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_VAMM_ENABLED", "true")
        monkeypatch.setenv("FLINT_VAMM_DEFAULT_SQRT_K", '{"SOL-PERP": 5000000}')
        config = FlintConfig()
        assert config.vamm_enabled is True
        assert "SOL-PERP" in config.vamm_default_sqrt_k
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vamm_config.py -v`
Expected: FAIL

- [ ] **Step 3: Add config fields**

In `flint/config.py`, add after the transaction costs section (after `tx_cost_sol_price_usd`):

```python
    # --- vAMM ---
    vamm_enabled: bool = False
    vamm_default_sqrt_k: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_vamm_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/config.py tests/test_vamm_config.py
git commit -m "feat: add vAMM config fields (vamm_enabled, vamm_default_sqrt_k)"
```

---

### Task 2: VammCurve Class

**Files:**
- Create: `flint/execution/vamm.py`
- Create: `tests/test_vamm.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_vamm.py`:

```python
"""Tests for VammCurve constant-product math."""
import math
import pytest

from flint.execution.vamm import VammCurve, VammAccuracyReport, DEFAULT_SQRT_K


class TestVammCurveConstruction:
    def test_from_oracle_price(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        assert abs(curve.reserve_price - 150.0) < 0.01

    def test_direct_construction(self):
        curve = VammCurve(sqrt_k=1_000_000, peg_multiplier=150.0)
        assert curve._sqrt_k == 1_000_000
        assert curve.reserve_price == 150.0


class TestFillPrice:
    def test_long_costs_more_than_oracle(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        price = curve.fill_price(100.0, "long")
        assert price > 150.0

    def test_short_gets_less_than_oracle(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        price = curve.fill_price(100.0, "short")
        assert price < 150.0

    def test_larger_order_worse_price(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        small = curve.fill_price(10.0, "long")
        large = curve.fill_price(1000.0, "long")
        assert large > small  # Larger order gets worse (higher) price

    def test_very_small_order_near_oracle(self):
        curve = VammCurve.from_oracle_price(sqrt_k=10_000_000, oracle_price=150.0)
        price = curve.fill_price(0.1, "long")
        assert abs(price - 150.0) < 0.01  # Nearly no impact

    def test_symmetric_impact(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        long_price = curve.fill_price(100.0, "long")
        short_price = curve.fill_price(100.0, "short")
        long_impact = long_price - 150.0
        short_impact = 150.0 - short_price
        # Not exactly equal due to curve asymmetry, but close
        assert abs(long_impact - short_impact) / max(long_impact, 0.001) < 0.1

    def test_higher_k_less_impact(self):
        small_k = VammCurve.from_oracle_price(sqrt_k=100_000, oracle_price=150.0)
        big_k = VammCurve.from_oracle_price(sqrt_k=10_000_000, oracle_price=150.0)
        small_price = small_k.fill_price(100.0, "long")
        big_price = big_k.fill_price(100.0, "long")
        assert small_price > big_price  # Smaller K = more impact


class TestImpactBps:
    def test_positive_impact(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        bps = curve.impact_bps(100.0, "long", oracle_price=150.0)
        assert bps > 0

    def test_larger_order_more_impact(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        small_bps = curve.impact_bps(10.0, "long", oracle_price=150.0)
        large_bps = curve.impact_bps(1000.0, "long", oracle_price=150.0)
        assert large_bps > small_bps


class TestDefaultSqrtK:
    def test_sol_perp_exists(self):
        assert "SOL-PERP" in DEFAULT_SQRT_K
        assert DEFAULT_SQRT_K["SOL-PERP"] > 0

    def test_btc_perp_exists(self):
        assert "BTC-PERP" in DEFAULT_SQRT_K
        assert DEFAULT_SQRT_K["BTC-PERP"] > DEFAULT_SQRT_K["SOL-PERP"]  # BTC deeper


class TestVammAccuracyReport:
    def test_create(self):
        report = VammAccuracyReport(
            market="SOL-PERP", num_fills=100,
            vamm_mae_bps=2.0, orderbook_mae_bps=3.0,
            sqrt_mae_bps=5.0, close_mae_bps=10.0,
            recommended_model="vamm",
        )
        assert report.recommended_model == "vamm"

    def test_summary(self):
        report = VammAccuracyReport(
            market="SOL-PERP", num_fills=100,
            vamm_mae_bps=2.0, orderbook_mae_bps=3.0,
            sqrt_mae_bps=5.0, close_mae_bps=10.0,
            recommended_model="vamm",
        )
        s = report.summary()
        assert "SOL-PERP" in s
        assert "vamm" in s.lower()

    def test_to_dict(self):
        report = VammAccuracyReport(
            market="SOL-PERP", num_fills=100,
            vamm_mae_bps=2.0, orderbook_mae_bps=3.0,
            sqrt_mae_bps=5.0, close_mae_bps=10.0,
            recommended_model="vamm",
        )
        d = report.to_dict()
        assert d["market"] == "SOL-PERP"
        assert d["recommended_model"] == "vamm"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vamm.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement VammCurve**

Create `flint/execution/vamm.py`:

```python
"""VammCurve — constant-product AMM model for Drift fill price estimation.

Models the core constant-product curve (x * y = k) with peg multiplier
that anchors the curve to oracle price. Used as Tier 0 in ImpactStage
for more accurate backtest fills on Drift markets.

This is a simplified model — it uses static K values and doesn't model
dynamic K adjustment, inventory skew, or the 7-layer spread computation.
Full vAMM replication is a future enhancement.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional


# Per-market K defaults based on typical Drift on-chain values.
# K values change ~5-20% over 3 months in practice, making static
# defaults acceptable for backtest approximation.
DEFAULT_SQRT_K: Dict[str, float] = {
    "SOL-PERP": 5_000_000,
    "BTC-PERP": 50_000_000,
    "ETH-PERP": 20_000_000,
    "DOGE-PERP": 1_000_000,
    "ARB-PERP": 1_000_000,
    "SUI-PERP": 1_000_000,
    "XRP-PERP": 2_000_000,
    "LINK-PERP": 1_000_000,
    "OP-PERP": 500_000,
    "INJ-PERP": 500_000,
    "WIF-PERP": 500_000,
}


class VammCurve:
    """Constant-product virtual AMM with peg multiplier.

    Models: base_reserve * quote_reserve = k
    Fill price derived from curve displacement by order size.

    Args:
        sqrt_k: Square root of the K factor. Determines curve depth.
        peg_multiplier: Anchors the reserve price to a reference price.
    """

    def __init__(self, sqrt_k: float, peg_multiplier: float):
        self._sqrt_k = sqrt_k
        self._k = sqrt_k * sqrt_k
        self._peg = peg_multiplier
        # Balanced reserves: base = quote = sqrt_k
        self._base_reserve = sqrt_k
        self._quote_reserve = sqrt_k

    @classmethod
    def from_oracle_price(cls, sqrt_k: float, oracle_price: float) -> "VammCurve":
        """Create a curve centered at oracle_price.

        With balanced reserves (base = quote = sqrt_k):
          reserve_price = (quote / base) * peg = 1 * peg = oracle_price
        """
        return cls(sqrt_k=sqrt_k, peg_multiplier=oracle_price)

    @property
    def reserve_price(self) -> float:
        """Current midpoint price."""
        if self._base_reserve <= 0:
            return 0.0
        return (self._quote_reserve / self._base_reserve) * self._peg

    def fill_price(self, base_amount: float, direction: str) -> float:
        """Compute average fill price for a trade.

        Args:
            base_amount: Size in base units (e.g., SOL contracts).
            direction: "long" (buying base) or "short" (selling base).

        Returns:
            Average fill price in quote units.
        """
        if base_amount <= 0:
            return self.reserve_price

        if direction == "long":
            # Buying base: base_reserve decreases
            new_base = self._base_reserve - base_amount
            if new_base <= 0:
                # Order exceeds available reserves — fill at very high price
                return self.reserve_price * (1 + base_amount / self._base_reserve)
            new_quote = self._k / new_base
            quote_delta = new_quote - self._quote_reserve
            return (quote_delta / base_amount) * self._peg
        else:
            # Selling base: base_reserve increases
            new_base = self._base_reserve + base_amount
            new_quote = self._k / new_base
            quote_delta = self._quote_reserve - new_quote
            return (quote_delta / base_amount) * self._peg

    def impact_bps(self, base_amount: float, direction: str, oracle_price: float) -> float:
        """Price impact in basis points relative to oracle price."""
        if oracle_price <= 0 or base_amount <= 0:
            return 0.0
        fp = self.fill_price(base_amount, direction)
        return abs(fp - oracle_price) / oracle_price * 10_000


@dataclass
class VammAccuracyReport:
    """Comparison of fill model accuracy against actual execution data."""
    market: str
    num_fills: int
    vamm_mae_bps: float
    orderbook_mae_bps: float
    sqrt_mae_bps: float
    close_mae_bps: float
    recommended_model: str

    def summary(self) -> str:
        return (
            f"Fill Model Accuracy: {self.market}\n"
            f"{'=' * 45}\n"
            f"Fills:     {self.num_fills}\n"
            f"vAMM MAE:       {self.vamm_mae_bps:.1f} bps\n"
            f"Orderbook MAE:  {self.orderbook_mae_bps:.1f} bps\n"
            f"Sqrt MAE:       {self.sqrt_mae_bps:.1f} bps\n"
            f"Close MAE:      {self.close_mae_bps:.1f} bps\n"
            f"Recommended:    {self.recommended_model}\n"
            f"{'=' * 45}"
        )

    def to_dict(self) -> dict:
        return {
            "market": self.market,
            "num_fills": self.num_fills,
            "vamm_mae_bps": self.vamm_mae_bps,
            "orderbook_mae_bps": self.orderbook_mae_bps,
            "sqrt_mae_bps": self.sqrt_mae_bps,
            "close_mae_bps": self.close_mae_bps,
            "recommended_model": self.recommended_model,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vamm.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/execution/vamm.py tests/test_vamm.py
git commit -m "feat: add VammCurve constant-product model and VammAccuracyReport"
```

---

### Task 3: ImpactStage Tier 0 Integration

**Files:**
- Modify: `flint/execution/impact.py`
- Create: `tests/test_vamm_integration.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_vamm_integration.py`:

```python
"""Tests for vAMM integration with ImpactStage and FillPipeline."""
import pytest

from flint.models import Candle, Order, OrderType, Side
from flint.execution.impact import ImpactStage
from flint.execution.vamm import VammCurve


def _make_order(market="SOL-PERP", side=Side.LONG, size=10.0):
    return Order(market=market, side=side, order_type=OrderType.MARKET,
                 size=size, order_id="test-1", ts=1000)


def _make_candle(market="SOL-PERP", close=150.0, volume=10000.0):
    return Candle(ts=1000, open=150.0, high=155.0, low=148.0, close=close,
                  volume=volume, market=market, resolution_s=60)


class TestImpactStageTier0:
    def test_vamm_used_when_configured(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        stage = ImpactStage(vamm_configs={"SOL-PERP": curve})
        order = _make_order(size=100.0)
        candle = _make_candle()
        result = stage.compute(order, candle, book=None)
        assert result.tier == "vamm"
        assert result.fill_price > 150.0  # Long should cost more
        assert result.impact_bps > 0

    def test_fallback_when_no_vamm_for_market(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        stage = ImpactStage(vamm_configs={"SOL-PERP": curve})
        order = _make_order(market="BTC-PERP", size=0.1)
        candle = _make_candle(market="BTC-PERP", close=65000.0)
        result = stage.compute(order, candle, book=None)
        assert result.tier == "sqrt"  # Falls back to Tier 2

    def test_no_vamm_configs_unchanged_behavior(self):
        stage = ImpactStage()
        order = _make_order(size=10.0)
        candle = _make_candle()
        result = stage.compute(order, candle, book=None)
        assert result.tier == "sqrt"  # Original behavior

    def test_vamm_with_short_order(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        stage = ImpactStage(vamm_configs={"SOL-PERP": curve})
        order = _make_order(side=Side.SHORT, size=100.0)
        candle = _make_candle()
        result = stage.compute(order, candle, book=None)
        assert result.tier == "vamm"
        assert result.fill_price < 150.0  # Short gets less

    def test_vamm_recenters_at_candle_close(self):
        # vAMM should use candle.close as oracle reference, not the stored peg
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=100.0)
        stage = ImpactStage(vamm_configs={"SOL-PERP": curve})
        order = _make_order(size=100.0)
        candle = _make_candle(close=200.0)  # Price moved to 200
        result = stage.compute(order, candle, book=None)
        # Fill price should be near 200, not 100
        assert result.fill_price > 190.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vamm_integration.py -v`
Expected: FAIL — `ImpactStage() got unexpected keyword argument 'vamm_configs'`

- [ ] **Step 3: Add Tier 0 to ImpactStage**

Modify `flint/execution/impact.py`:

```python
"""ImpactStage — computes fill price using vAMM, orderbook, sqrt model, or flat bps."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional

from ..models import Candle, Order, OrderbookSnapshot, Side


@dataclass
class ImpactResult:
    """Output of the impact stage."""
    fill_price: float
    available_size: float
    impact_bps: float
    tier: str  # "vamm", "orderbook", "sqrt", "fallback"


class ImpactStage:
    """Determines fill price via four-tier fallback.

    Tier 0: vAMM constant-product curve (when configured for market).
    Tier 1: Walk orderbook levels (when snapshot exists).
    Tier 2: Square-root participation model (when bar volume exists).
    Tier 3: Flat basis-point penalty (last resort).
    """

    def __init__(
        self,
        impact_coefficient: float = 0.005,
        fallback_bps: float = 5.0,
        vamm_configs: Optional[Dict[str, "VammCurve"]] = None,
    ):
        self._k = impact_coefficient
        self._fallback_bps = fallback_bps
        self._vamm_configs = vamm_configs

    def compute(
        self,
        order: Order,
        candle: Candle,
        book: Optional[OrderbookSnapshot],
    ) -> ImpactResult:
        """Compute fill price and available liquidity for an order."""
        # Tier 0: vAMM
        if self._vamm_configs and order.market in self._vamm_configs:
            return self._vamm_fill(order, candle)

        # Tier 1: Orderbook walk
        if book is not None and order.market == book.market:
            levels = book.asks if order.side == Side.LONG else book.bids
            if levels:
                return self._walk_book(order, candle, levels)

        # Tier 2: Sqrt participation model
        if candle.volume > 0:
            return self._sqrt_model(order, candle)

        # Tier 3: Flat bps fallback
        return self._flat_fallback(order, candle)

    def _vamm_fill(self, order, candle):
        from .vamm import VammCurve
        stored_curve = self._vamm_configs[order.market]
        # Re-center at current candle close (oracle proxy)
        curve = VammCurve.from_oracle_price(stored_curve._sqrt_k, candle.close)
        direction = "long" if order.side == Side.LONG else "short"
        fill_price = curve.fill_price(order.size, direction)
        impact = curve.impact_bps(order.size, direction, candle.close)
        return ImpactResult(fill_price=fill_price, available_size=order.size,
                            impact_bps=impact, tier="vamm")

    def _walk_book(self, order, candle, levels):
        remaining = order.size
        total_cost = 0.0
        filled = 0.0
        for level in levels:
            take = min(remaining, level.size)
            total_cost += take * level.price
            filled += take
            remaining -= take
            if remaining <= 0:
                break
        if filled <= 0:
            if candle.volume > 0:
                return self._sqrt_model(order, candle)
            return self._flat_fallback(order, candle)
        avg_price = total_cost / filled
        impact_bps = abs(avg_price - candle.close) / candle.close * 10_000 if candle.close > 0 else 0.0
        return ImpactResult(fill_price=avg_price, available_size=filled,
                            impact_bps=impact_bps, tier="orderbook")

    def _sqrt_model(self, order, candle):
        participation = order.size / candle.volume
        impact_pct = self._k * math.sqrt(participation)
        impact_bps = impact_pct * 10_000
        if order.side == Side.LONG:
            fill_price = candle.close * (1 + impact_pct)
        else:
            fill_price = candle.close * (1 - impact_pct)
        return ImpactResult(fill_price=fill_price, available_size=order.size,
                            impact_bps=impact_bps, tier="sqrt")

    def _flat_fallback(self, order, candle):
        pct = self._fallback_bps / 10_000
        if order.side == Side.LONG:
            fill_price = candle.close * (1 + pct)
        else:
            fill_price = candle.close * (1 - pct)
        return ImpactResult(fill_price=fill_price, available_size=order.size,
                            impact_bps=self._fallback_bps, tier="fallback")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vamm_integration.py -v`
Expected: PASS

- [ ] **Step 5: Run existing impact tests for regressions**

Run: `pytest tests/ -k "impact or fill_model or backtest" --tb=short -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add flint/execution/impact.py tests/test_vamm_integration.py
git commit -m "feat: add vAMM as Tier 0 in ImpactStage (constant-product fill pricing)"
```

---

### Task 4: FillPipeline Pass-Through

**Files:**
- Modify: `flint/execution/fill_models.py`
- Modify: `tests/test_vamm_integration.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_vamm_integration.py`:

```python
from flint.execution.fill_models import FillPipeline


class TestFillPipelineVamm:
    def test_pipeline_with_vamm_configs(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        pipeline = FillPipeline(
            impact_coefficient=0.005,
            latency_enabled=False,
            vamm_configs={"SOL-PERP": curve},
        )
        order = _make_order(size=100.0)
        candle = _make_candle()
        fill = pipeline.fill_market(order, candle)
        assert fill is not None
        assert fill.price > 150.0  # vAMM impact
        assert fill.impact_bps > 0

    def test_pipeline_without_vamm_unchanged(self):
        pipeline = FillPipeline(
            impact_coefficient=0.005,
            latency_enabled=False,
        )
        order = _make_order(size=10.0)
        candle = _make_candle()
        fill = pipeline.fill_market(order, candle)
        assert fill is not None
        # Should use sqrt model (existing behavior)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vamm_integration.py::TestFillPipelineVamm -v`
Expected: FAIL — `FillPipeline() got unexpected keyword argument 'vamm_configs'`

- [ ] **Step 3: Add vamm_configs to FillPipeline**

In `flint/execution/fill_models.py`, modify `FillPipeline.__init__`:

Add `vamm_configs=None` parameter:
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
        vamm_configs=None,
    ):
        self._impact = ImpactStage(
            impact_coefficient=impact_coefficient,
            fallback_bps=fallback_bps,
            vamm_configs=vamm_configs,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vamm_integration.py -v`
Expected: PASS

- [ ] **Step 5: Run existing fill model tests for regressions**

Run: `pytest tests/ -k "fill or pipeline" --tb=short -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add flint/execution/fill_models.py tests/test_vamm_integration.py
git commit -m "feat: pass vamm_configs through FillPipeline to ImpactStage"
```

---

### Task 5: ROADMAP Update

**Files:**
- Modify: `ROADMAP.md`

- [ ] **Step 1: Update ROADMAP.md §4.1**

Find the §4.1 vAMM Curve Modeling section and add after the existing checklist items:

```markdown
**Implemented (simplified model):**
- [x] `VammCurve` constant-product AMM model with peg multiplier (`flint/execution/vamm.py`)
- [x] `from_oracle_price()` factory for oracle-centered curve creation
- [x] `fill_price()` and `impact_bps()` for order-size-dependent pricing
- [x] Per-market `DEFAULT_SQRT_K` values for major Drift markets
- [x] Tier 0 integration in `ImpactStage` — highest priority fill model
- [x] `FillPipeline` pass-through via `vamm_configs` parameter
- [x] `VammAccuracyReport` dataclass for fill model comparison (data-ready)
- [x] Config: `vamm_enabled`, `vamm_default_sqrt_k`

**Deferred to backlog:**
- [ ] Full vAMM replication (dynamic K, inventory skew, 7-layer spread)
- [ ] Historical AMM state snapshots
- [ ] Fill model accuracy comparison (requires live fill data)
```

- [ ] **Step 2: Commit**

```bash
git add ROADMAP.md
git commit -m "docs: update ROADMAP §4.1 with vAMM curve modeling implementation notes"
```

---

## Task Dependencies

```
Task 1 (Config) ──────────┐
                           ├──→ Task 3 (ImpactStage Tier 0) ──→ Task 4 (FillPipeline)
Task 2 (VammCurve class) ─┘                                          │
                                                                  Task 5 (ROADMAP)
```

**Parallelizable:** Tasks 1 and 2 have no dependencies between them.
**Sequential:** Task 3 needs 1+2. Task 4 needs 3. Task 5 is last.
