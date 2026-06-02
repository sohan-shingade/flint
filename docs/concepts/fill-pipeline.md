# Fill Pipeline

How orders become fills in Flint. The same pipeline runs in backtest and paper trading; live trading replaces the impact stage with actual venue fills.

## Stages

```
Order ─► LatencyStage ─► ImpactStage ─► PartialFillStage ─► Fill
```

Each stage is pluggable (`flint/execution/fill_models.py`). The default `FillPipeline` wires them in this order.

### LatencyStage

Delays execution by `base_latency_s ± latency_jitter_s` before the fill price is computed. Uses the candle whose `ts` is closest to `order.ts + latency` — so a slow venue fills on a later bar than a fast one. Seeded for determinism in tests via `latency_seed`.

Typical latencies:

- Binance ≈ 0.2 s — next-bar fill on high-res data
- Hyperliquid ≈ 1 s
- Jupiter ≈ 12 s — you will straddle candle boundaries

### ImpactStage — 4 tiers

Chooses the best-available fill model **per order**, **per bar**:

| Tier | Model | Fires when |
|---|---|---|
| 0 | `VammCurve` (constant-product) | `vamm_enabled=true` and the market has a configured vAMM — dormant (built for Drift, which is dropped post-hack; off by default) |
| 1 | `OrderbookFillModel` (book walk) | L2 snapshot present in `orderbook_snapshots` for that bar |
| 2 | Sqrt participation impact | Volume known, no book snapshot |
| 3 | Flat bps fallback | No depth data; uses `slippage_bps` |

**Tier 2 math:**

```
impact_bps = k · sqrt( order_notional / bar_notional )
```

`k` per venue in `VENUE_DEFAULTS` (see [venue-configs.md](../reference/venue-configs.md)). Calibrate with `flint calibrate <venue>` once you have live fills.

**Tier 1:** walk bid/ask levels until `size` is filled, compute volume-weighted average price.

**Tier 0 (dormant):** the vAMM uses constant-product with peg multiplier and oracle anchoring; reserve depth comes from per-market `sqrt_k` values. This path was built for Drift and is retained for reference only — Hyperliquid uses the CLOB walk (Tier 1).

### PartialFillStage

Probabilistically reduces fill size when the order exceeds a participation threshold of the bar's volume. This catches "I tried to buy $1M on a $50k bar" scenarios that flat models miss.

## Per-venue pipelines

Venue-specific fill logic lives in `fill_hyperliquid.py`, `fill_cex.py`, `fill_jupiter.py` (plus a dormant `fill_drift.py`). The `fill_registry.py` dispatches based on `order.venue`:

- **Hyperliquid.** CLOB orderbook walk → HLP backstop for residual size. This is the live fill model.
- **CEX (CCXT).** Orderbook walk → sqrt impact.
- **Jupiter.** Swap simulation using pool state; routes through the aggregator.
- **Drift (dormant).** vAMM model → DLOB walk → sqrt impact. Retained for reference; Drift is dropped post-hack.

The Rust engine mirrors this in `engine/venue_fills.rs`.

## Costs applied after fill

- **Fees.** `taker_fee_bps` / `maker_fee_bps` from `VenueConfig`. Maker rebates feed back into PnL.
- **Funding.** Hourly from real multi-venue funding rates, per holding position.
- **Transaction costs.** Solana priority fee + Jito tip in lamports → USD via config; negligible for HL / CEX.

Full cost surface lives in `flint/execution/tx_costs.py` + `flint/execution/venue_config.py`.

## Changing fill models

Per-request via the API / CLI:

```json
{ "fill_model": "pipeline", "slippage_bps": 10.0, "latency_enabled": true, "impact_coefficient": 0.004 }
```

`fill_model` ∈ `pipeline` (default) · `slippage` · `close` · `next_bar_open`. The last three are diagnostic — compare realistic vs optimistic fills to see how much PnL is fill-model-dependent. See [how-to/compare-fill-models.md](../how-to/compare-fill-models.md) and the [fill model comparison writeup](../validation/fill-model-comparison.md).

## Realism ceiling

The pipeline is a massive upgrade over close-price fills, but it is still an approximation:

- Doesn't model queue priority on CLOBs.
- Doesn't model MEV sandwich attacks on Solana.
- Assumes orderbook snapshots are representative between samples.
- Can't see real-time liquidity that wasn't captured.

This is the main source of backtest-vs-live divergence. Calibrate from live fills + run a [parity test](../how-to/run-parity-test.md) before trusting any number.
