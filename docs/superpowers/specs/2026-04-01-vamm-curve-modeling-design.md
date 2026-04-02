# vAMM Curve Modeling — Design Spec

> Sub-project 4.1 of Phase 4 (ROADMAP.md §4.1)
> Date: 2026-04-01

## Overview

Model Drift Protocol's virtual AMM constant-product curve for more accurate backtest fill prices. The current sqrt participation model (`impact = k * sqrt(size/volume)`) doesn't capture the curve shape of Drift's on-chain AMM. A simplified vAMM model with static K factor and oracle-derived peg gives significantly better fill price estimates for medium-to-large orders.

### Scope

**In scope:**
- `VammCurve` class — constant-product math with peg multiplier
- Integration as Tier 0 in `FillPipeline.ImpactStage`
- Per-market K factor defaults (hardcoded, configurable via config)
- `VammAccuracyReport` for comparing fill models against actual data
- Config additions for vAMM parameters

**Out of scope:**
- Full vAMM replication (dynamic K adjustment, inventory skew, 7-layer spread) — noted as future enhancement
- Historical AMM state snapshots (not available)
- DLOB (orderbook) vs AMM routing simulation

**Future enhancements (backlog):**
- Dynamic K factor adjustment based on funding revenue
- Inventory skew modeling (`base_asset_amount_with_amm`)
- 7-layer spread computation
- Fetching current on-chain AMM state via driftpy

---

## 1. VammCurve — Core Math

**New file:** `flint/execution/vamm.py`

Implements Drift's constant-product curve: `base_reserve * quote_reserve = k`

### Interface

```python
class VammCurve:
    def __init__(self, sqrt_k: float, peg_multiplier: float):
        """
        sqrt_k: Square root of K factor. Determines curve depth.
        peg_multiplier: Anchors curve to oracle price.

        Reserves derived: base_reserve = sqrt_k, quote_reserve = sqrt_k
        K = sqrt_k^2
        """

    @classmethod
    def from_oracle_price(cls, sqrt_k: float, oracle_price: float) -> "VammCurve":
        """Create with balanced reserves centered at oracle_price.

        peg_multiplier = oracle_price (reserves start balanced at sqrt_k each,
        so reserve_price = quote/base * peg = 1 * oracle_price = oracle_price)
        """

    def fill_price(self, base_amount: float, direction: str) -> float:
        """Compute average fill price for a trade of given size.

        direction: "long" (buying base, reducing base_reserve) or
                   "short" (selling base, increasing base_reserve)

        Math:
          For LONG: new_base = base_reserve - base_amount
                    new_quote = k / new_base
                    quote_delta = new_quote - quote_reserve
                    fill_price = (quote_delta / base_amount) * peg_multiplier
          For SHORT: new_base = base_reserve + base_amount
                     new_quote = k / new_base
                     quote_delta = quote_reserve - new_quote
                     fill_price = (quote_delta / base_amount) * peg_multiplier
        """

    def impact_bps(self, base_amount: float, direction: str, oracle_price: float) -> float:
        """Price impact in basis points relative to oracle price."""

    @property
    def reserve_price(self) -> float:
        """Current midpoint: (quote_reserve / base_reserve) * peg_multiplier"""
```

### Key Properties

- **Larger orders get worse prices** — the curve is concave, so each additional unit costs more
- **Symmetric** — buying and selling have equal-and-opposite impact on a balanced curve
- **Depth scales with K** — higher K = deeper liquidity = less price impact for same order size
- **Oracle-anchored** — peg multiplier keeps the curve centered at oracle price

### Per-Market K Defaults

Hardcoded defaults based on typical Drift on-chain values:

```python
DEFAULT_SQRT_K = {
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
```

These are approximate and configurable via `vamm_default_sqrt_k` config. K values change ~5-20% over 3 months in practice (bounded by 0.1% per hour formulaic adjustment), making static defaults acceptable for backtest approximation.

---

## 2. ImpactStage Integration

**Modify:** `flint/execution/fill_models.py` — `ImpactStage`

Add Tier 0 (vAMM) to the existing fallback chain:

```
Tier 0: VammCurve (if configured for this market)
  → Compute fill price from constant-product curve
  → Use oracle_price (from candle close) to set peg
Tier 1: Orderbook walk (if snapshot available)
Tier 2: Sqrt participation model
Tier 3: Flat bps fallback
```

### ImpactStage Changes

Add `vamm_configs` parameter:
```python
class ImpactStage:
    def __init__(
        self,
        impact_coefficient: float = 0.005,
        fallback_bps: float = 5.0,
        vamm_configs: Optional[Dict[str, VammCurve]] = None,
    ): ...
```

In `compute()`, check vAMM first:
```python
def compute(self, order, candle, book):
    # Tier 0: vAMM
    if self._vamm_configs and order.market in self._vamm_configs:
        curve = self._vamm_configs[order.market]
        # Re-center curve at current oracle price (candle close)
        centered = VammCurve.from_oracle_price(curve._sqrt_k, candle.close)
        direction = "long" if order.side == Side.LONG else "short"
        price = centered.fill_price(order.size, direction)
        impact = centered.impact_bps(order.size, direction, candle.close)
        return ImpactResult(fill_price=price, available_size=order.size,
                           impact_bps=impact, tier="vamm")
    # Tier 1: Orderbook (existing)
    # Tier 2: Sqrt (existing)
    # Tier 3: Flat (existing)
```

**Backward compatible:** When `vamm_configs` is None (default), existing behavior unchanged.

### FillPipeline Changes

Pass `vamm_configs` through to ImpactStage:
```python
class FillPipeline:
    def __init__(self, ..., vamm_configs=None):
        self._impact = ImpactStage(..., vamm_configs=vamm_configs)
```

---

## 3. VammAccuracyReport

**Added to:** `flint/execution/vamm.py`

For comparing fill model accuracy against actual execution data (useful when live data is available).

```python
@dataclass
class VammAccuracyReport:
    market: str
    num_fills: int
    vamm_mae_bps: float           # Mean absolute error vs actual
    orderbook_mae_bps: float
    sqrt_mae_bps: float
    close_mae_bps: float
    recommended_model: str        # Best performing model

    def summary(self) -> str: ...
    def to_dict(self) -> dict: ...
```

The report is a dataclass for now — the comparison logic requires actual fill data from Phase 1 live trading. The infrastructure is ready, the actual comparison deferred until data is available.

---

## 4. Config Additions

**Modify:** `flint/config.py`

```python
# --- vAMM ---
vamm_enabled: bool = False
vamm_default_sqrt_k: str = ""   # JSON: '{"SOL-PERP": 5000000, "BTC-PERP": 50000000}'
```

`vamm_enabled` controls whether FillPipeline creates VammCurve instances from defaults. When True and `vamm_default_sqrt_k` is empty, uses `DEFAULT_SQRT_K` hardcoded values. When non-empty JSON, parses and uses those values.

---

## 5. Dependencies

No new dependencies. Uses existing `math` and `numpy`.

---

## 6. ROADMAP Update

After implementation, update ROADMAP.md §4.1 with "Implemented" checkboxes. Note full vAMM replication as a future enhancement.

---

## 7. Testing Strategy

All tests use synthetic data — no network calls.

- **VammCurve**: Test fill_price returns worse price for larger orders. Test long direction costs more than oracle, short gets less. Test `from_oracle_price` centers reserve_price at oracle. Test `impact_bps` increases with order size. Test very small orders have near-zero impact. Test very large orders (approaching K) have massive impact.
- **ImpactStage Tier 0**: Test vAMM used when configured for market. Test fallback to Tier 1/2/3 when no vAMM. Test backward compatibility (no vamm_configs = unchanged). Test tier="vamm" in result.
- **FillPipeline**: Test with vamm_configs passed through. Test without (default behavior).
- **VammAccuracyReport**: Test dataclass creation, summary, to_dict.
- **Config**: Test vamm_enabled and vamm_default_sqrt_k defaults and overrides.
