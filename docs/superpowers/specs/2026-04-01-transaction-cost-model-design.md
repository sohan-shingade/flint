# Transaction Cost Model — Design Spec

> Sub-project 4.3 of Phase 4 (ROADMAP.md §4.3)
> Date: 2026-04-01

## Overview

Add per-venue transaction cost modeling to capture the full cost of execution beyond exchange fees. Each venue has its own cost profile: Solana venues (Drift) incur priority fees and Jito tips, Hyperliquid has L1 settlement costs, and CEXes have no network costs. Costs are deducted per fill in backtests and exposed to strategies via `ctx.estimate_cost()`.

### Scope

**In scope:**
- `TxCostModel` ABC with per-venue implementations (Solana, Hyperliquid, CEX)
- `CostEstimate` dataclass with typed breakdown and `.total` property
- `SolanaTxCostModel` with configurable defaults + optional historical percentile data
- `HyperliquidTxCostModel` with negligible L1 cost
- `CexTxCostModel` with zero network costs
- `tx_cost` field on `Fill` dataclass (separate from exchange `fee`)
- `total_tx_costs` field on `BacktestResult`
- `FillPipeline` integration (optional `tx_cost_model` parameter)
- `ctx.estimate_cost()` on ExecutionContext, LiveExecutionContext, MultiVenueLiveContext
- Config additions for default costs
- Deduction of tx_cost from PnL in BacktestContext

**Out of scope:**
- Live priority fee querying from Solana RPC (infrastructure ready, data collection deferred)
- Jito bundle submission (Phase 1 scope, already done)
- Historical fee data storage and retrieval (future enhancement)

---

## 1. CostEstimate Dataclass

**New in:** `flint/execution/tx_costs.py`

```python
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

    def to_dict(self) -> dict: ...
```

---

## 2. TxCostModel Interface + Venue Implementations

**New file:** `flint/execution/tx_costs.py`

### ABC

```python
class TxCostModel(abc.ABC):
    """Per-venue transaction cost estimator."""

    @abc.abstractmethod
    def estimate(
        self,
        market: str,
        size: float,
        price: float,
        urgency: str = "normal",
    ) -> CostEstimate: ...

    @property
    @abc.abstractmethod
    def venue(self) -> str: ...
```

### SolanaTxCostModel (Drift)

```python
class SolanaTxCostModel(TxCostModel):
    """Transaction costs for Solana-based venues (Drift).

    Includes priority fees and Jito tips. Converts lamports to USD
    using SOL price (from config or oracle).
    """

    def __init__(
        self,
        priority_fee_lamports: int = 5000,
        jito_tip_lamports: int = 10000,
        sol_price_usd: float = 150.0,
        exchange_fee_bps: float = 10.0,
        historical_fees: Optional[Dict[str, List[int]]] = None,
    ): ...

    def estimate(self, market, size, price, urgency="normal") -> CostEstimate:
        exchange_fee = size * price * (self._exchange_fee_bps / 10000)

        if urgency == "urgent" and self._historical_fees:
            priority = self._percentile(self._historical_fees, 90)
        elif self._historical_fees:
            priority = self._percentile(self._historical_fees, 50)
        else:
            priority = self._priority_fee_lamports

        priority_usd = priority / 1e9 * self._sol_price_usd
        jito_usd = self._jito_tip_lamports / 1e9 * self._sol_price_usd

        return CostEstimate(
            exchange_fee=exchange_fee,
            network_fee=priority_usd,
            bundle_tip=jito_usd,
            impact_est=0.0,  # Impact estimated separately by ImpactStage
        )
```

### HyperliquidTxCostModel

```python
class HyperliquidTxCostModel(TxCostModel):
    """Transaction costs for Hyperliquid. Negligible L1 settlement for perps."""

    def __init__(
        self,
        l1_cost_usd: float = 0.001,
        exchange_fee_bps: float = 3.5,
    ): ...

    def estimate(self, market, size, price, urgency="normal") -> CostEstimate:
        return CostEstimate(
            exchange_fee=size * price * (self._exchange_fee_bps / 10000),
            network_fee=self._l1_cost_usd,
            bundle_tip=0.0,
            impact_est=0.0,
        )
```

### CexTxCostModel

```python
class CexTxCostModel(TxCostModel):
    """Transaction costs for centralized exchanges. No network costs."""

    def __init__(self, exchange_fee_bps: float = 5.0): ...

    def estimate(self, market, size, price, urgency="normal") -> CostEstimate:
        return CostEstimate(
            exchange_fee=size * price * (self._exchange_fee_bps / 10000),
            network_fee=0.0,
            bundle_tip=0.0,
            impact_est=0.0,
        )
```

### Factory

```python
def get_tx_cost_model(venue: str, **kwargs) -> TxCostModel:
    """Get the appropriate TxCostModel for a venue."""
    if venue == "drift":
        return SolanaTxCostModel(**kwargs)
    elif venue == "hyperliquid":
        return HyperliquidTxCostModel(**kwargs)
    elif venue in ("binance", "okx", "bybit"):
        return CexTxCostModel(**kwargs)
    else:
        return CexTxCostModel(**kwargs)  # Default: no network costs
```

---

## 3. Fill + BacktestResult Changes

**Modify:** `flint/models.py`

Add `tx_cost` to Fill:
```python
@dataclass(frozen=True)
class Fill:
    # ... existing fields ...
    tx_cost: float = 0.0  # Network + bundle cost in USD
```

Add `total_tx_costs` to BacktestResult:
```python
@dataclass
class BacktestResult:
    # ... existing fields ...
    total_tx_costs: float = 0.0  # Sum of tx_cost across all fills
```

---

## 4. BacktestContext Integration

**Modify:** `flint/execution/backtest_context.py`

In `_apply_fill()`, after the existing `self._debit_cash(fill.fee, fill.venue)`, add:
```python
if fill.tx_cost > 0:
    self._debit_cash(fill.tx_cost, fill.venue)
```

This deducts network costs from cash alongside exchange fees.

---

## 5. FillPipeline Integration

**Modify:** `flint/execution/fill_models.py`

Add optional `tx_cost_model` parameter to `FillPipeline`:
```python
class FillPipeline(FillModel):
    def __init__(
        self,
        # ... existing params ...
        tx_cost_model: Optional[TxCostModel] = None,
    ):
        self._tx_cost_model = tx_cost_model
```

In `fill_market()`, after computing the fill price and fee, compute tx_cost:
```python
tx_cost = 0.0
if self._tx_cost_model:
    cost_est = self._tx_cost_model.estimate(order.market, fill_size, fill_price)
    tx_cost = cost_est.network_fee + cost_est.bundle_tip
```

Include `tx_cost` in the returned Fill object.

**Backward compatible:** When `tx_cost_model` is None (default), `tx_cost=0.0` on all fills.

---

## 6. BacktestEngine Integration

**Modify:** `flint/backtest/engine.py`

In `_build_result()`, compute `total_tx_costs`:
```python
total_tx_costs = sum(f.tx_cost for f in ctx.all_fills)
```

Add to BacktestResult constructor:
```python
total_tx_costs=total_tx_costs,
```

---

## 7. Strategy Access — estimate_cost()

**Modify:** `flint/execution/context.py`

Add default implementation on ExecutionContext ABC:
```python
def estimate_cost(self, market: str, size: float, venue: str = "default") -> Optional["CostEstimate"]:
    """Estimate total execution cost before trading. Returns None if no cost model."""
    return None
```

**Modify:** `flint/execution/live_base.py`

Override in LiveExecutionContext to use a configured TxCostModel:
```python
def estimate_cost(self, market, size, venue="default"):
    if self._tx_cost_model is None:
        return None
    price = self._current_candle.close if self._current_candle else 0
    if price <= 0:
        return None
    return self._tx_cost_model.estimate(market, size, price)
```

Add `tx_cost_model: Optional[TxCostModel] = None` to constructor.

**Modify:** `flint/execution/multi_venue_live.py`

Delegate to resolved venue:
```python
def estimate_cost(self, market, size, venue="default"):
    target = self._resolve_venue(venue)
    ctx = self._contexts.get(target)
    if ctx:
        return ctx.estimate_cost(market, size, venue=target)
    return None
```

---

## 8. Config Additions

**Modify:** `flint/config.py`

```python
# --- Transaction costs ---
tx_cost_priority_fee_lamports: int = 5000
tx_cost_jito_tip_lamports: int = 10000
tx_cost_sol_price_usd: float = 150.0
```

---

## 9. Dependencies

No new dependencies. Uses existing `numpy` and standard library.

---

## 10. ROADMAP Update

After implementation, update ROADMAP.md §4.3 with "Implemented" checkboxes.

---

## 11. Testing Strategy

All tests mocked — no network calls.

- **CostEstimate**: Test `.total` sums all components. Test `to_dict()` serialization.
- **SolanaTxCostModel**: Test default costs (priority + Jito in USD). Test urgency="urgent" uses higher percentile. Test lamport→USD conversion with different SOL prices.
- **HyperliquidTxCostModel**: Test negligible L1 cost. Test exchange fee computation.
- **CexTxCostModel**: Test zero network costs. Test exchange fee only.
- **get_tx_cost_model factory**: Test returns correct model per venue.
- **Fill.tx_cost**: Test new field defaults to 0.0. Test backward compatibility.
- **BacktestResult.total_tx_costs**: Test new field defaults to 0.0.
- **FillPipeline**: Test with tx_cost_model set — fills include tx_cost. Test without (default) — tx_cost=0.0.
- **BacktestContext**: Test tx_cost deducted from cash alongside fee.
- **BacktestEngine**: Test total_tx_costs computed in result.
- **estimate_cost()**: Test returns CostEstimate on LiveExecutionContext. Test returns None when no model. Test MultiVenueLiveContext delegates to correct venue.
- **Config**: Test 3 new fields with defaults and env overrides.
