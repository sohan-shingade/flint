# Slippage Models Guide

Flint uses a four-tier impact model to simulate execution costs in backtests. The engine automatically selects the highest-fidelity model available for each order based on what data is present in FlintStore.

---

## 1. Four-Tier Impact Model

The `ImpactStage` in the fill pipeline selects a model in priority order:

| Tier | Model | Condition |
|------|-------|-----------|
| 0 | vAMM curve (`VammCurve`) | `vamm_enabled: true` and market has a `vamm_configs` entry |
| 1 | Orderbook walk | L2 snapshot present in `orderbook_snapshots` for the bar timestamp |
| 2 | Sqrt participation | Volume data available in candles, no L2 snapshot |
| 3 | Flat bps fallback | No market depth data; uses `fee_rate` as a constant impact |

The tier selection is per-order, per-bar. A strategy running on SOL-PERP with vAMM enabled uses Tier 0; if vAMM is disabled but an L2 snapshot was fetched, it uses Tier 1 — no config change required.

---

## 2. vAMM Curve (Tier 0)

`flint/execution/vamm.py` — `VammCurve` implements a constant-product AMM anchored to an oracle price.

### Model

The curve maintains base and quote reserves such that:

```
base_reserve * quote_reserve = K
```

where `K = sqrt_k^2`. The peg multiplier anchors the curve center to the current oracle price:

```
base_reserve  = sqrt_k / sqrt(oracle_price)
quote_reserve = sqrt_k * sqrt(oracle_price)
```

### Fill price formula

For a **long** (buying base):

```
fill_price = quote_reserve / (base_reserve - fill_size)  *  peg_multiplier
```

For a **short** (selling base):

```
fill_price = quote_reserve / (base_reserve + fill_size)  *  peg_multiplier
```

The peg multiplier is the ratio of the current oracle price to the curve's reference price, keeping the curve centered on fair value.

### Impact in basis points

```
impact_bps = abs(fill_price - oracle_price) / oracle_price * 10_000
```

### K and depth

Larger K means deeper liquidity and lower price impact for a given size. Each major Drift market has a `DEFAULT_SQRT_K` calibrated to approximate historical AMM depth:

| Market | DEFAULT_SQRT_K |
|--------|---------------|
| SOL-PERP | 2_000_000 |
| BTC-PERP | 500_000 |
| ETH-PERP | 800_000 |

### Configuration

```yaml
vamm_enabled: true
vamm_default_sqrt_k: 1000000   # fallback for markets without explicit config

# Per-market overrides (passed as BacktestContext vamm_configs dict)
```

In backtest requests, pass `vamm_configs` per market to override the default K.

---

## 3. Sqrt Participation Model (Tier 2)

When no vAMM or orderbook data is available but volume is known, impact is estimated via a square-root participation model:

```
impact_pct = impact_coefficient * sqrt(fill_size / bar_volume)
```

**Variables:**

- `fill_size` — order size in base units
- `bar_volume` — total traded volume on the bar (from candle data)
- `impact_coefficient` — venue-specific constant from `VenueConfig`

**What `impact_coefficient` means**: a coefficient of `0.1` means that filling 1% of bar volume incurs 1% of that percentage as slippage: `0.1 * sqrt(0.01) = 0.01` = 1 bps. Higher coefficients mean less liquid markets.

**Defaults per venue** (from `VenueConfig`):

| Venue | impact_coefficient |
|-------|--------------------|
| Drift | 0.10 |
| Hyperliquid | 0.08 |
| Binance | 0.05 |
| OKX | 0.06 |
| Bybit | 0.06 |
| dYdX | 0.09 |

---

## 4. Calibration

The calibration engine (`flint/backtest/calibration.py`) fits impact model coefficients from observed fills.

### Power-law model

```
impact_bps = a * sigma * (Q / ADV)^b
```

**Variables:**

- `a` — amplitude coefficient (fitted)
- `sigma` — rolling realized volatility (normalization for regime robustness)
- `Q` — fill size in USD notional
- `ADV` — average daily volume in USD
- `b` — exponent (fitted; `b = 0.5` is the sqrt model)

The sigma and ADV normalization makes coefficients comparable across different volatility regimes and liquidity periods.

### Model selection

The calibration engine fits both models (sqrt fixed at `b=0.5`, and free power-law) and selects between them via 5-fold cross-validation on held-out fills. Power-law is chosen when it reduces out-of-sample MAE by more than 10%.

### Drift detection

After each calibration, the engine computes the relative change in `a` from the previous calibration. If the change exceeds 15%, a drift alert fires suggesting recalibration.

### Running calibration

```bash
flint calibrate --venue drift --market SOL-PERP
```

This reads live fills from `FlintStore`, fits the model, prints a report, and writes calibrated coefficients back to `VenueConfig`. Add `--dry-run` to print the report without saving.

Also available via API:

```bash
curl -X POST http://localhost:8000/api/v1/calibrate \
  -H 'Content-Type: application/json' \
  -d '{"venue": "drift", "market": "SOL-PERP"}'
```

The API endpoint is read-only and never writes to config.

---

## 5. Transaction Costs

`flint/execution/tx_costs.py` — `TxCostModel` with per-venue implementations.

Each fill can include a network-level cost on top of exchange fees:

| Venue | Model | Components |
|-------|-------|-----------|
| Drift | `SolanaTxCostModel` | Priority fee (p50/p90 of recent fees) + Jito bundle tip |
| Hyperliquid | `HyperliquidTxCostModel` | Negligible L1 settlement cost |
| CEX (Binance, OKX…) | `CexTxCostModel` | Zero network cost |

**Solana cost breakdown:**

```
total_cost_usd = (priority_fee_lamports + jito_tip_lamports) / LAMPORTS_PER_SOL * sol_price_usd
```

Priority fees are modeled from historical percentile distributions. Use `p50` for normal conditions, `p90` for urgent orders.

**Pre-trade cost estimation in strategy code:**

```python
cost = ctx.estimate_cost("SOL-PERP", size_usd=1000)
# cost.exchange_fee_usd + cost.network_fee_usd == cost.total
if cost.total > max_cost:
    return  # skip this bar
```

**Configuration:**

```yaml
tx_cost_priority_fee_lamports: 100000   # default priority fee
tx_cost_jito_tip_lamports: 50000        # default Jito tip
tx_cost_sol_price_usd: 140.0            # used when live SOL price unavailable
```

---

## 6. Configuration Reference

| Key | Default | Description |
|-----|---------|-------------|
| `vamm_enabled` | `false` | Enable vAMM fill model (Tier 0) |
| `vamm_default_sqrt_k` | `1000000` | K for markets without explicit config |
| `impact_coefficient` | per-venue | Sqrt model amplitude (in `VenueConfig`) |
| `tx_cost_priority_fee_lamports` | `100000` | Solana priority fee |
| `tx_cost_jito_tip_lamports` | `50000` | Jito bundle tip |
| `tx_cost_sol_price_usd` | `140.0` | SOL price fallback |

---

## 7. When to Use Which Model

| Situation | Recommended tier |
|-----------|-----------------|
| Drift perp backtest, high accuracy needed | Tier 0 (vAMM) — enable `vamm_enabled: true` |
| Any venue with L2 snapshot data | Tier 1 (orderbook) — download orderbook data first |
| CEX backtest with volume data | Tier 2 (sqrt) — default for Binance/OKX/Bybit |
| Rough feasibility check | Tier 3 (flat bps) — fast, low accuracy |
| Post-live calibration available | Tier 2 with calibrated `impact_coefficient` |

For new strategies, start with Tier 3 (flat bps) to confirm signal logic, then move to Tier 0 or 1 before sizing up capital. A strategy that is profitable under flat-bps but not under vAMM is not viable on Drift.
