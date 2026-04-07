# Fill Model Comparison

## Why Fill Models Matter

Most backtesting frameworks fill market orders at the candle close price. This is convenient but dangerously optimistic -- in live trading, your order moves the market. The gap between close-price fills and realistic fills is where most backtest overfitting hides. A strategy that shows 30% annual return with close-price fills might show 15% with realistic impact modeling, or go negative entirely on large position sizes.

This matters even more on DeFi perps than on centralized exchanges. Drift's vAMM has fundamentally different liquidity characteristics than a Binance CLOB -- a $50k market order on Drift moves the price through a constant-product curve, while the same order on Binance walks a discrete orderbook. Flint models each venue natively rather than applying a single generic slippage estimate.

The question is not "which fill model is correct" -- it's "how sensitive is my strategy to fill assumptions?" If your strategy only works with close-price fills, it will not survive live trading.

## How Each Model Works

Flint's `FillPipeline` applies four tiers in priority order, falling back to the next tier when data for the preferred tier is unavailable:

### Tier 0: vAMM Constant-Product Curve

Models Drift's virtual AMM using the constant-product invariant:

```
x * y = k
fill_price = (new_quote_reserves / base_amount) * peg_multiplier
```

Where `k = sqrt_k^2` is the liquidity depth parameter (per-market), and the peg multiplier anchors the curve to the oracle price. Larger orders consume more of the curve and receive worse prices. This is how Drift actually prices trades on-chain.

Per-market `sqrt_k` values are calibrated to approximate real Drift AMM depth (e.g., SOL: 5M, BTC: 50M, ETH: 10M).

**When it fires**: Only for markets with a configured `vamm_configs` entry. Currently Drift markets only.

### Tier 1: Orderbook Walk

Walks stored L2 orderbook snapshots level by level, computing a volume-weighted average fill price:

```
For each level (price, size) in the book:
  fill portion of order at this level's price
  if remaining_size == 0: done
  else: move to next level
```

**When it fires**: When an `OrderbookSnapshot` exists for the market at the current timestamp. Requires L2 data from providers (Drift orderbook, Hyperliquid L2 book).

### Tier 2: Sqrt Participation Model

A statistical impact model that penalizes orders proportional to their size relative to bar volume:

```
impact_pct = k * sqrt(order_size / bar_volume)
fill_price = close * (1 + impact_pct)   # for buys
fill_price = close * (1 - impact_pct)   # for sells
```

Where `k` is a per-venue impact coefficient (default: 0.005 for Drift, 0.003 for Hyperliquid, 0.002 for Binance). The square-root relationship captures the empirical finding that market impact scales sub-linearly with order size.

**When it fires**: When volume data is available but no orderbook or vAMM data exists.

### Tier 3: Flat Basis Points Fallback

Adds a constant slippage to every fill:

```
fill_price = close * (1 + slippage_bps / 10000)   # for buys
```

Default: 5 bps. This is the least realistic model but ensures fills are never exactly at close price.

**When it fires**: When no volume, orderbook, or vAMM data is available.

### Legacy Models

These are standalone fill models (not part of the pipeline) available for comparison:

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
| FillPipeline (vAMM) | Highest for Drift -- models actual venue | Drift-specific strategies |
| FillPipeline (orderbook) | Highest when data available -- actual depth | Any market with L2 snapshots |

**Concrete example**: A momentum strategy trading $10k notional on SOL-PERP hourly bars might see:
- ClosePriceFill: +12% return
- SlippageFill (10bps): +10.5% return
- FillPipeline (sqrt, k=0.005): +8-11% return depending on volume
- FillPipeline (vAMM): +7-10% return (Drift-specific curve)

The difference grows with position size. A $100k order on the same strategy could see 3-5x more impact than a $10k order through the sqrt model.

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
from flint.execution.vamm import VammCurve

# Default pipeline (sqrt impact)
engine = BacktestEngine(strategy=strategy, fill_model=FillPipeline())

# Custom impact coefficient
engine = BacktestEngine(strategy=strategy, fill_model=FillPipeline(impact_coefficient=0.01))

# With vAMM for Drift markets
vamm = VammCurve.from_oracle_price(oracle_price=150.0, sqrt_k=5_000_000)
engine = BacktestEngine(
    strategy=strategy,
    fill_model=FillPipeline(vamm_configs={"SOL-PERP": vamm})
)
```

## Known Limitations

- **Static sqrt_k values.** Drift's AMM adjusts K dynamically. Flint uses fixed per-market values, so impact estimates can be too conservative in high-liquidity periods and too optimistic in low-liquidity periods.
- **Point-in-time orderbook snapshots.** The orderbook walk uses stored snapshots, not a live book. Between snapshots, depth can change significantly.
- **Volume is not liquidity.** The sqrt model uses bar volume as a denominator. A bar with $1M volume from one whale trade has very different liquidity than $1M from 1000 retail trades.
- **No MEV modeling.** Sandwich attacks and frontrunning can degrade real fill prices by 1-10 bps on Solana. None of the models account for adversarial order flow.
- **Latency is stochastic, not deterministic.** The pipeline adds a random latency drawn from a distribution. Real latency is correlated with network conditions and volatility.
- **Calibration improves accuracy.** `flint calibrate` fits coefficients from real fill data. Without calibration, the default coefficients are reasonable estimates but not venue-specific measurements.

## When to Use Which Model

| Scenario | Recommended model | Why |
|----------|-------------------|-----|
| Exploring parameter space (100+ trials) | ClosePriceFill | Speed -- 5-10x faster than pipeline |
| Narrowing to top 5 parameter sets | FillPipeline (default) | Realistic enough to filter out fragile configs |
| Final validation before paper trading | FillPipeline with vAMM (Drift) or orderbook | Most accurate available simulation |
| Large position sizes (>$50k notional) | FillPipeline with sqrt or vAMM | Impact is material; close-price is misleading |
| Cross-venue comparison | FillPipeline with per-venue configs | Each venue has different impact characteristics |
