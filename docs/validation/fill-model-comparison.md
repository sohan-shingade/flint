# Fill Model Comparison

> **Note on Drift.** Drift is dropped as a supported venue post-hack. The `DriftFillModel` (JIT + DLOB + vAMM) below documents real engine behavior and is retained for reference, but the vAMM path is **dormant** — it is off by default and not exercised in live trading. **Hyperliquid's CLOB is the live fill model.** The Drift comparison data is left intact because it remains a useful illustration of how DEX vAMM mechanics differ from CLOB and CEX fills.

## Why Fill Models Matter

Most backtesting frameworks fill market orders at the candle close price. This is convenient but dangerously optimistic -- in live trading, your order moves the market. The gap between close-price fills and realistic fills is where most backtest overfitting hides. A strategy that shows 30% annual return with close-price fills might show 15% with realistic impact modeling, or go negative entirely on large position sizes.

This matters even more on DeFi perps than on centralized exchanges. A vAMM (as on the now-dormant Drift path) has fundamentally different liquidity characteristics than a CLOB -- a $50k market order against a constant-product curve moves price differently than the same order walking a discrete orderbook (Hyperliquid's CLOB, or a Binance book). Flint models each venue natively rather than applying a single generic slippage estimate.

The question is not "which fill model is correct" -- it's "how sensitive is my strategy to fill assumptions?" If your strategy only works with close-price fills, it will not survive live trading.

## Per-Venue Execution Breakdown

Each venue has fundamentally different execution mechanics. Flint provides a dedicated `FillModel` subclass per venue that replicates those mechanics as closely as possible using available data.

### Drift

**How trades fill live:**

On-chain, Drift routes a market order through three execution layers in priority order:

1. **JIT (Just-In-Time) Dutch Auction.** When a taker order arrives, Drift runs a short auction (typically ~20 Solana slots, ~10 seconds). Market makers compete to fill the order by offering price improvement over the oracle. Empirically, JIT fills ~60% of order flow at 1-3 bps better than oracle price. This is Drift's primary source of tight fills for retail-sized orders.

2. **DLOB (Decentralized Limit Order Book).** Remaining unfilled size is matched against resting limit orders from Drift's on-chain order book. The DLOB functions like a traditional orderbook -- price-time priority, level-by-level matching. Liquidity is thinner than CEX books (typically $2M within 1% of mid for SOL-PERP, vs. $20M on Binance).

3. **vAMM (Virtual Automated Market Maker).** Any size not absorbed by JIT or DLOB is filled against Drift's constant-product AMM. The vAMM is a liquidity backstop -- it always fills, but at a price determined by the curve: `fill_price = (new_quote_reserves / base_amount) * peg_multiplier`. Larger orders push further along the curve and receive worse prices. Per-market `sqrt_k` values control the curve's depth (SOL: 5M, BTC: 50M, ETH: 10M).

**How Flint models it** (`DriftFillModel` in `execution/fill_drift.py`):

The model replicates all three tiers. For each market order:
- **Tier 1:** A random draw (p=0.6) determines if JIT activates. If so, 30-80% of the order fills at oracle minus 2 bps (buys) or oracle plus 2 bps (sells).
- **Tier 2:** Remaining size walks the DLOB. When a real `OrderbookSnapshot` is available (from Drift's L2 data), it walks that. Otherwise it generates a synthetic book calibrated to Drift's typical depth profile ($2M within 1%, spread 3 bps, concentration 0.4).
- **Tier 3:** Any remainder hits the vAMM curve using `VammCurve.from_oracle_price()` with the market's `sqrt_k` value.

The final fill price is the volume-weighted average across all tiers that fired.

| Parameter | Default | What it controls |
|-----------|---------|------------------|
| `jit_fill_probability` | 0.6 | How often JIT activates |
| `auction_price_improvement_bps` | 2.0 | Price improvement from JIT MMs |
| `seed` | None | RNG seed for reproducible JIT draws |
| Fee | 10 bps taker, -2 bps maker | Handled by `DriftFeeModel` separately |
| Latency | 8s base, 5s jitter | Via `VenueConfig`, not in fill model |

---

### Jupiter Perps

**How trades fill live:**

Jupiter Perps is entirely pool-based -- there is no orderbook. Execution follows a two-step keeper model:

1. **Trader submits a request.** The trader creates a position request transaction on-chain. This does not execute immediately.

2. **Keeper fulfills after a delay.** A permissionless keeper network watches for pending requests and executes them. The keeper fills the trade at the **Pyth oracle price** at the moment of fulfillment, not at the moment of submission. The delay between submission and fulfillment is typically 4-20 seconds (median ~12s, roughly 2 Solana slots).

This delay means the fill price depends on where the oracle price moves during the waiting period -- a form of execution risk unique to Jupiter. During volatile periods, the price can move significantly between submission and fulfillment.

Price impact is **quadratic** in notional size: `impact_usd = (notional / scalar) * notional`, where the scalar is ~$1B for major markets. Small orders (<$10k) have negligible impact; large orders ($100k+) can see meaningful slippage.

There is no historical borrow rate or volume API. Forward collection via `JupiterBorrowCollector` works but only accumulates going forward.

**How Flint models it** (`JupiterFillModel` in `execution/fill_jupiter.py`):

The model captures both the keeper delay and pool-based impact:
- **Keeper delay:** A random delay is drawn from `Uniform(base - jitter, base + jitter)` (default: 4-20s, median 12s). The fill price is linearly interpolated between candle open and close based on where the delay lands within the bar. If the delay exceeds the bar duration, the model looks ahead into the next candle from a buffer provided by the engine.
- **Quadratic impact:** Applied on top of the interpolated oracle price. `impact_per_unit = notional / scalar * fill_price`. Added for buys, subtracted for sells.
- **No orderbook:** `set_orderbook()` is a no-op. Jupiter does not have one.

| Parameter | Default | What it controls |
|-----------|---------|------------------|
| `base_latency_s` | 12.0 | Median keeper delay |
| `latency_jitter_s` | 8.0 | Delay variability |
| `seed` | None | RNG seed for reproducible delays |
| Fee | 6 bps flat | Handled by `FlatFeeModel` separately |
| Impact scalar | $1B (SOL, ETH, BTC) | Quadratic impact denominator |

---

### Hyperliquid

**How trades fill live:**

Hyperliquid operates a **pure CLOB** (Central Limit Order Book) on its own L1 chain, with sub-second block times (~1s). Market orders walk the book level by level just like a CEX. Two key differences from CEX execution:

1. **HLP Vault Backstop.** Hyperliquid maintains a protocol-owned liquidity vault (the HLP) that backstops market orders when the CLOB is exhausted. If your order is larger than the visible book depth, the HLP fills the remainder -- but at a worse price proportional to how much of the bar's volume you're consuming. This means you can always get filled, but large orders pay a premium.

2. **Lower depth than top CEXes.** Typical book depth is ~$5M within 1% of mid for major pairs (vs. $20M on Binance). The tighter depth plus 1.5 bps typical spread means impact is higher per dollar.

Fees are aggressive: 3.5 bps taker (lowest in the industry for perps), 1 bps maker.

**How Flint models it** (`HyperliquidFillModel` in `execution/fill_hyperliquid.py`):

- **CLOB walk:** Walks the orderbook levels (real snapshot if available, synthetic with Hyperliquid's depth profile otherwise). Accumulates a VWAP fill.
- **HLP backstop:** Any remaining size after the book is exhausted fills at a price adjusted by `impact_coefficient * (remaining / candle_volume)`. This penalizes large orders relative to available volume -- a $50k order on a $500k volume bar gets 0.5% additional impact.

| Parameter | Default | What it controls |
|-----------|---------|------------------|
| `impact_coefficient` | 0.005 | HLP backstop price impact scale |
| Fee | 3.5 bps taker, 1 bps maker | Via `VenueConfig` |
| Latency | 1s base, 0.5s jitter | Via `VenueConfig` |

---

### Binance

**How trades fill live:**

Binance operates the deepest perp CLOB in crypto. A market order walks the L2 orderbook level by level. Key characteristics:

- **Deep books.** Typically $20M+ within 1% of mid for BTC-PERP, $5-10M for SOL-PERP. This means even $100k orders often fill within the first few levels with minimal slippage.
- **Tight spreads.** 0.5 bps typical spread on major pairs.
- **High concentration.** ~70% of depth sits in the top few levels (aggressive market making).
- **Low latency.** Co-located matching engine, ~200ms round-trip for API orders.

Fees: 4.5 bps taker (with BNB discount), 2 bps maker.

**How Flint models it** (`BinanceFillModel` in `execution/fill_cex.py`):

- **Real book path:** When an `OrderbookSnapshot` is available, walks it level by level for a VWAP fill. No modifications -- the real book data drives the fill price.
- **Synthetic fallback (volume-scaled):** When no real book exists, generates a synthetic orderbook from Binance's depth profile ($20M baseline, 0.5 bps spread, 0.7 concentration). **The depth is scaled by the ratio of actual bar volume to expected volume** -- low-volume bars produce thinner books and worse fills; high-volume bars produce deeper books and better fills. Scale is clamped to [5%, 300%] of baseline to avoid extremes.
- **Participation cap:** On the synthetic path, fills are capped at 10% of bar volume. An order that would consume more than 10% of a bar's total volume returns a partial fill. This prevents unrealistic fills against synthetic liquidity.

| Parameter | Default | What it controls |
|-----------|---------|------------------|
| `taker_fee_bps` | 5.0 | Applied to fill notional |
| Depth profile | $20M / 1%, 0.5 bps spread | Synthetic book shape |
| Volume scaling | 20x ratio, [0.05, 3.0] clamp | How bar volume modulates depth |
| Participation cap | 10% of bar volume | Max fill on synthetic path |
| Latency | 0.2s base, 0.1s jitter | Via `VenueConfig` |

---

### OKX

**How trades fill live:**

OKX operates a CLOB similar to Binance but with moderately less depth. Key differences:

- **Moderate depth.** ~$10M within 1% of mid for BTC-PERP -- about half of Binance.
- **Wider spreads.** ~1 bps typical on major pairs.
- **Moderate concentration.** 60% of depth at the top of book (less aggressive quoting than Binance).
- **Slightly higher latency.** ~300ms round-trip for API orders.

Fees: 5 bps taker, 2 bps maker.

**How Flint models it** (`OkxFillModel` in `execution/fill_cex.py`):

Same architecture as Binance -- real book walk when available, volume-scaled synthetic book with participation cap when not. The only differences are the depth profile parameters ($10M baseline, 1 bps spread, 0.6 concentration) and taker fee.

---

### Bybit

**How trades fill live:**

Bybit uses a CLOB with one important difference: **IOC (Immediate-Or-Cancel) price band enforcement.** Market orders are internally converted to IOC orders capped at 1% from the last traded price. If the book within that band cannot fill the full order, you get a partial fill. This protects against flash crashes and fat-finger errors but means large orders in thin markets may not fill completely.

- **Moderate depth.** ~$8M within 1% of mid for BTC-PERP.
- **1% price band.** Market buys are capped at `last_price * 1.01`; market sells at `last_price * 0.99`. Levels beyond the cap are ignored.
- **Partial fills are common** for large orders near the band edge.

Fees: 5.5 bps taker, 2 bps maker.

**How Flint models it** (`BybitFillModel` in `execution/fill_cex.py`):

Extends the base CEX model with IOC band logic:
- Computes `price_cap = candle.close * (1 ± 0.01)` based on order side.
- Walks the book but stops at the cap. If the order isn't fully filled within the band, the fill is flagged `is_partial=True` and only the consumed portion is returned.
- On the synthetic path, volume-scaled depth and the 10% participation cap are applied in addition to the IOC band -- whichever is more restrictive wins.

---

## Synthetic Fallback: Volume-Scaled Depth

When no real L2 orderbook snapshot is available for a CEX venue (Binance, OKX, Bybit), Flint falls back to a **synthetic orderbook** generated from per-venue depth profiles. As of v0.3, this fallback is volume-aware:

**Depth scaling formula:**
```
bar_volume_usd = candle.volume * candle.close
expected_volume = profile.bid_depth_1pct * 20    (vol-to-depth ratio)
scale = clamp(bar_volume_usd / expected_volume, 0.05, 3.0)
effective_depth = baseline_depth * scale
```

This means:
- A 3 AM Sunday bar with 1/10th normal volume gets a book that's 10% as deep -- fills walk more levels and get worse VWAPs.
- A high-volume bar during NY open gets up to 3x baseline depth -- fills concentrate at the top of book.
- A low-cap altcoin with 1/50th the volume of BTC-PERP naturally gets a thinner book without any per-market configuration.

**Participation rate cap:**
```
max_fill_size = candle.volume * 0.10   (10% of bar volume)
```

If your order would consume more than 10% of the bar's total volume, it returns a partial fill. This prevents the synthetic book from producing fills that would be physically impossible in a real market.

Both adjustments apply only to the synthetic fallback. When a real orderbook snapshot is available, the walk uses actual depth data with no caps.

## Venue Comparison Summary

| Venue | Execution model | Book depth (1%) | Spread | Taker fee | Latency | Fill model class |
|-------|----------------|-----------------|--------|-----------|---------|-----------------|
| Hyperliquid (live) | CLOB + HLP vault | ~$5M | 1.5 bps | 3.5 bps | 1s | `HyperliquidFillModel` |
| Jupiter | Pool + keeper delay | N/A (no book) | N/A | 6 bps | 12s | `JupiterFillModel` |
| Binance | Pure CLOB | ~$20M | 0.5 bps | 5 bps | 0.2s | `BinanceFillModel` |
| OKX | Pure CLOB | ~$10M | 1 bps | 5 bps | 0.3s | `OkxFillModel` |
| Bybit | CLOB + IOC band | ~$8M | 1 bps | 5.5 bps | 0.3s | `BybitFillModel` |
| Drift (dormant) | JIT + DLOB + vAMM | ~$2M | 3 bps | 10 bps | 8s | `DriftFillModel` |

## How Each Model Captures Live Behavior

| Live behavior | Which model captures it | How |
|--------------|------------------------|-----|
| JIT auction price improvement | `DriftFillModel` | Stochastic JIT draw, 60% activation, 2 bps improvement |
| Decentralized orderbook matching | `DriftFillModel` (Tier 2) | L2 book walk or synthetic depth |
| Constant-product AMM pricing | `DriftFillModel` (Tier 3) | `VammCurve` with per-market `sqrt_k` |
| Keeper delay uncertainty | `JupiterFillModel` | Stochastic delay, price interpolation across bars |
| Pool-based quadratic impact | `JupiterFillModel` | `(notional / scalar) * notional` |
| HLP vault backstop | `HyperliquidFillModel` | Impact proportional to `remaining / bar_volume` |
| L2 orderbook depth | All CEX models | Real snapshot walk when available |
| Time-varying liquidity | CEX synthetic fallback | Volume-scaled depth profiles |
| IOC price band limits | `BybitFillModel` | 1% cap from close, partial fills beyond band |
| Large order rejection | CEX synthetic fallback | 10% participation rate cap |

## Generic FillPipeline

In addition to the per-venue models, Flint's `FillPipeline` (in `execution/fill_models.py`) is a composable, venue-agnostic pipeline used as the default when no venue-specific model is assigned. It chains three stages:

1. **Latency** -- stochastic delay drawn from a per-venue distribution. Orders that arrive after the bar's close are deferred to the next bar.
2. **Impact** -- orderbook walk if available, otherwise sqrt participation model: `impact = k * sqrt(order_size / bar_volume)`.
3. **Partial fill** -- IOC/FOK/GTC semantics. IOC fills what's available; FOK rejects if insufficient; GTC creates a resting order for the remainder.

This is the model used for venues without a dedicated `FillModel` class (e.g., dYdX). It also serves as the engine's default when running single-venue backtests without specifying a model.

## Legacy Models

Standalone fill models (not part of the pipeline) available for comparison and fast iteration:

- **ClosePriceFill**: Fill at candle close. Zero slippage. Most optimistic.
- **NextBarOpenFill**: Fill at the next candle's open. Captures overnight/between-bar drift.
- **SlippageFill(bps)**: Close price plus a constant basis-point adjustment.

## Expected Impact on Results

| Model | Realism | Best for |
|-------|---------|----------|
| ClosePriceFill | Low -- assumes zero impact | Quick parameter sweeps where you need speed over accuracy |
| NextBarOpenFill | Low-Medium -- captures execution delay | Strategies sensitive to entry timing |
| SlippageFill (10bps) | Medium -- uniform penalty | Conservative baseline estimate |
| FillPipeline (sqrt) | High -- size-dependent impact | Final validation before paper trading |
| Venue-specific model | Highest -- models actual venue mechanics | Production backtests targeting a specific venue |

**Concrete example**: A momentum strategy trading $10k notional on SOL-PERP hourly bars might see:
- ClosePriceFill: +12% return
- SlippageFill (10bps): +10.5% return
- FillPipeline (sqrt, k=0.005): +8-11% return depending on volume
- DriftFillModel (3-tier): +7-10% return (JIT helps, vAMM hurts on large orders)
- JupiterFillModel: +6-9% return (keeper delay adds execution risk)

The difference grows with position size. A $100k order on the same strategy could see 3-5x more impact than a $10k order through any realistic model.

## How to Run Your Own Comparison

### Via the example script

```bash
python examples/fill_model_comparison.py
```

This runs the momentum breakout strategy through all four models on the same SOL-PERP data and prints a side-by-side comparison table.

### Via the API

```bash
# Close-price fills (fast, optimistic)
curl -X POST http://localhost:8000/api/v1/backtest/run \
  -H "Content-Type: application/json" \
  -d '{"strategy": "momentum_breakout", "market": "SOL-PERP", "fill_model": "close"}'

# Full pipeline with custom impact coefficient
curl -X POST http://localhost:8000/api/v1/backtest/run \
  -H "Content-Type: application/json" \
  -d '{"strategy": "momentum_breakout", "market": "SOL-PERP", "fill_model": "pipeline", "impact_coefficient": 0.01}'
```

### In Python code

```python
from flint.backtest.engine import BacktestEngine
from flint.execution.fill_models import FillPipeline, ClosePriceFill
from flint.execution.fill_drift import DriftFillModel
from flint.execution.fill_hyperliquid import HyperliquidFillModel
from flint.execution.fill_jupiter import JupiterFillModel
from flint.execution.fill_cex import BinanceFillModel, BybitFillModel
from flint.execution.fill_registry import create_venue_fill_models

# Single venue with its native model
engine = BacktestEngine(strategy=strategy, fill_model=DriftFillModel())

# Multi-venue: each venue gets its own fill model
venue_models = create_venue_fill_models(["drift", "hyperliquid", "binance"])
engine = BacktestEngine(strategy=strategy, venue_fill_models=venue_models)

# Custom Drift parameters (conservative JIT estimate)
engine = BacktestEngine(
    strategy=strategy,
    fill_model=DriftFillModel(jit_fill_probability=0.4, auction_price_improvement_bps=1.0)
)
```

## Known Limitations

- **Static sqrt_k values.** Drift's AMM adjusts K dynamically. Flint uses fixed per-market values, so impact estimates can be too conservative in high-liquidity periods and too optimistic in low-liquidity periods.
- **Point-in-time orderbook snapshots.** The orderbook walk uses stored snapshots, not a live book. Between snapshots, depth can change significantly.
- **Volume is not liquidity.** The sqrt model and synthetic depth scaling use bar volume as a proxy. A bar with $1M volume from one whale trade has very different liquidity than $1M from 1000 retail trades.
- **Synthetic depth is approximate.** Volume-scaled depth profiles are directionally correct (low volume = worse fills) but don't capture intra-bar liquidity dynamics or market-specific depth shapes.
- **No MEV modeling.** Sandwich attacks and frontrunning can degrade real fill prices by 1-10 bps on Solana. None of the models account for adversarial order flow.
- **Jupiter has no historical data.** No borrow rate or volume API exists. Backtest accuracy depends on forward-collected data from `JupiterBorrowCollector`.
- **Latency is stochastic, not deterministic.** The pipeline adds a random latency drawn from a distribution. Real latency is correlated with network conditions and volatility.
- **Calibration improves accuracy.** `flint calibrate` fits coefficients from real fill data. Without calibration, the default coefficients are reasonable estimates but not venue-specific measurements.

## When to Use Which Model

| Scenario | Recommended model | Why |
|----------|-------------------|-----|
| Exploring parameter space (100+ trials) | ClosePriceFill | Speed -- 5-10x faster than pipeline |
| Narrowing to top 5 parameter sets | FillPipeline (default) | Realistic enough to filter out fragile configs |
| Final validation before paper trading | Venue-specific model | Most accurate available simulation |
| Large position sizes (>$50k notional) | Venue-specific with real L2 data | Impact is material; close-price is misleading |
| Cross-venue comparison | Per-venue models via `create_venue_fill_models()` | Each venue has different impact characteristics |
| Hyperliquid strategies (live) | `HyperliquidFillModel` | CLOB walk + HLP backstop; the live fill model |
| Jupiter strategies | `JupiterFillModel` | Only model that captures keeper delay |
| Drift strategies (dormant) | `DriftFillModel` | Reference only — captures JIT + vAMM mechanics; Drift is dropped post-hack |
