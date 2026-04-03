# Slippage Models Guide

Flint uses a four-tier impact model to simulate execution costs in backtests, plus per-venue transaction cost models, a calibration engine, and concentrated liquidity (CLMM) modeling for MEV research. The engine automatically selects the highest-fidelity model available for each order based on what data is present in FlintStore.

---

## 1. Four-Tier Impact Model

The `ImpactStage` in the fill pipeline selects a model in priority order:

| Tier | Model | Condition |
|------|-------|-----------|
| 0 | vAMM curve (`VammCurve`) | `vamm_enabled: true` and market has a `vamm_configs` entry |
| 1 | Orderbook walk (`OrderbookFillModel`) | L2 snapshot present in `orderbook_snapshots` for the bar timestamp |
| 2 | Sqrt participation | Volume data available in candles, no L2 snapshot |
| 3 | Flat bps fallback | No market depth data; uses `fee_rate` as a constant impact |

The tier selection is per-order, per-bar. A strategy running on SOL-PERP with vAMM enabled uses Tier 0; if vAMM is disabled but an L2 snapshot was fetched, it uses Tier 1 -- no config change required.

---

## 2. vAMM Curve (Tier 0)

`flint/execution/vamm.py` -- `VammCurve` implements a constant-product AMM anchored to an oracle price. This is the most accurate fill model for Drift Protocol backtests, as Drift itself uses a vAMM for price discovery.

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
fill_price = quote_reserve / (base_reserve - fill_size) * peg_multiplier
```

For a **short** (selling base):

```
fill_price = quote_reserve / (base_reserve + fill_size) * peg_multiplier
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
| SOL-PERP | 2,000,000 |
| BTC-PERP | 500,000 |
| ETH-PERP | 800,000 |

### Configuration

```yaml
vamm_enabled: true
vamm_default_sqrt_k: 1000000   # fallback for markets without explicit config
```

In backtest requests, pass `vamm_configs` per market to override the default K.

---

## 3. Orderbook Walk (Tier 1)

`flint/execution/fill_models.py` -- `OrderbookFillModel` walks the L2 orderbook snapshot to compute a volume-weighted fill price.

When an L2 snapshot is present in `orderbook_snapshots` for the current bar, the model:

1. Sorts the relevant side (asks for buys, bids for sells) by price.
2. Walks levels, accumulating size until the order is fully filled.
3. Computes the volume-weighted average fill price across all consumed levels.
4. If the order size exceeds available depth, the remaining size fills at the worst level plus the flat bps fallback.

This model is most useful for venues where you have fetched L2 orderbook data (Drift, Hyperliquid).

---

## 4. Sqrt Participation Model (Tier 2)

When no vAMM or orderbook data is available but volume is known, impact is estimated via a square-root participation model:

```
impact_pct = impact_coefficient * sqrt(fill_size / bar_volume)
```

**Variables:**

- `fill_size` -- order size in base units
- `bar_volume` -- total traded volume on the bar (from candle data)
- `impact_coefficient` -- venue-specific constant from `VenueConfig`

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

## 5. Flat BPS Fallback (Tier 3)

When no market depth or volume data is available, impact is a constant percentage of the fill price:

```
impact_pct = fee_rate  (in bps / 10_000)
```

This is the fastest and least accurate model. Use it for quick feasibility checks during strategy development, then switch to a higher tier before sizing up capital.

---

## 6. Transaction Cost Models

`flint/execution/tx_costs.py` -- `TxCostModel` ABC with per-venue implementations.

Each fill can include a network-level cost on top of exchange fees. This separates exchange maker/taker fees from blockchain transaction costs.

### Per-venue implementations

| Venue | Model Class | Components |
|-------|-------------|-----------|
| Drift | `SolanaTxCostModel` | Priority fee (p50/p90 of recent fees) + Jito bundle tip |
| Hyperliquid | `HyperliquidTxCostModel` | Negligible L1 settlement cost |
| CEX (Binance, OKX, etc.) | `CexTxCostModel` | Zero network cost |

### Solana cost breakdown

```
total_cost_usd = (priority_fee_lamports + jito_tip_lamports) / LAMPORTS_PER_SOL * sol_price_usd
```

Priority fees are modeled from historical percentile distributions. Use `p50` for normal conditions, `p90` for urgent orders.

### Pre-trade cost estimation

Use `ctx.estimate_cost()` in your strategy to check costs before placing an order:

```python
cost = ctx.estimate_cost("SOL-PERP", size_usd=1000)
# cost.exchange_fee_usd  -- maker/taker fees
# cost.network_fee_usd   -- Solana priority fee + Jito tip
# cost.total              -- exchange_fee_usd + network_fee_usd

if cost.total > max_acceptable_cost:
    return  # skip this bar
```

This is especially useful for small trades on Solana where network fees can be a significant percentage of trade value.

### Configuration

```yaml
tx_cost_priority_fee_lamports: 100000   # default priority fee
tx_cost_jito_tip_lamports: 50000        # default Jito tip
tx_cost_sol_price_usd: 140.0            # used when live SOL price unavailable
```

---

## 7. Calibration Engine

`flint/backtest/calibration.py` -- `CalibrationEngine` fits impact model coefficients from observed live fills. This is the bridge between simulated and real execution.

### Power-law model

```
impact_bps = a * sigma * (Q / ADV)^b
```

**Variables:**

- `a` -- amplitude coefficient (fitted)
- `sigma` -- rolling realized volatility (normalization for regime robustness)
- `Q` -- fill size in USD notional
- `ADV` -- average daily volume in USD
- `b` -- exponent (fitted; `b = 0.5` is the sqrt model)

The sigma and ADV normalization makes coefficients comparable across different volatility regimes and liquidity periods.

### Sqrt model (special case)

The sqrt model is the power-law with `b` fixed at `0.5`:

```
impact_bps = a * sigma * sqrt(Q / ADV)
```

### Model selection

The calibration engine fits both models (sqrt fixed at `b=0.5`, and free power-law) and selects between them via 5-fold cross-validation on held-out fills. Power-law is chosen when it reduces out-of-sample MAE by more than 10%.

### Drift detection

After each calibration, the engine computes the relative change in `a` from the previous calibration. If the change exceeds **15%**, a drift alert fires suggesting recalibration. This detects liquidity regime changes -- for example, if a market becomes significantly more or less liquid over time.

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

## 8. CLMM Pool Model

`flint/mev/clmm.py` -- `CLMMPool` models concentrated liquidity pools (Orca Whirlpool, Raydium CLMM) for MEV and arbitrage research.

### What it does

- Computes swap outputs given tick data and input amounts
- Detects arbitrage opportunities between CLMM price and oracle/perp price
- Models price impact within concentrated liquidity ranges

### Tick data

`flint/providers/orca_ticks.py` -- `OrcaTickFetcher` fetches CLMM tick data from Orca's on-chain accounts. Tick data is stored in the `tick_snapshots` table:

```sql
PRIMARY KEY (pool_address, ts)
```

Each snapshot contains: current tick, tick spacing, fee rate, sqrt price, and the full tick data array.

### Usage

The `MevArbMonitor` strategy uses `CLMMPool` to scan for arbitrage routes between DEX pools and perp markets. The MEV Dashboard in the UI visualizes detected opportunities.

```bash
# Scan for arb opportunities via API
curl -X POST http://localhost:8000/api/v1/mev/scan/arb
```

---

## 9. Configuration Reference

| Key | Default | Description |
|-----|---------|-------------|
| `vamm_enabled` | `false` | Enable vAMM fill model (Tier 0) |
| `vamm_default_sqrt_k` | `1000000` | K for markets without explicit config |
| `impact_coefficient` | per-venue | Sqrt model amplitude (in `VenueConfig`) |
| `tx_cost_priority_fee_lamports` | `100000` | Solana priority fee |
| `tx_cost_jito_tip_lamports` | `50000` | Jito bundle tip |
| `tx_cost_sol_price_usd` | `140.0` | SOL price fallback |

---

## 10. When to Use Which Model

| Situation | Recommended tier |
|-----------|-----------------|
| Drift perp backtest, high accuracy needed | Tier 0 (vAMM) -- enable `vamm_enabled: true` |
| Any venue with L2 snapshot data | Tier 1 (orderbook) -- download orderbook data first |
| CEX backtest with volume data | Tier 2 (sqrt) -- default for Binance/OKX/Bybit |
| Rough feasibility check | Tier 3 (flat bps) -- fast, low accuracy |
| Post-live calibration available | Tier 2 with calibrated `impact_coefficient` |
| Cross-venue arb strategies | Enable both vAMM (Drift) and sqrt (Hyperliquid) |

### Recommended workflow

1. **Development**: Start with Tier 3 (flat bps) to confirm signal logic works.
2. **Refinement**: Switch to Tier 0 (vAMM for Drift) or Tier 2 (sqrt for other venues) to test with realistic market impact.
3. **Validation**: Download L2 orderbook data and run with Tier 1 for the most accurate backtests.
4. **Post-live**: After accumulating live fills, run `flint calibrate` to fit coefficients from real data.
5. **Monitoring**: Watch for drift alerts (15% coefficient change) and recalibrate periodically.

A strategy that is profitable under flat-bps but not under vAMM is not viable on Drift. Always validate with higher-fidelity models before committing real capital.
