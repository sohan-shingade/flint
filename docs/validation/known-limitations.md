# Known Limitations

Flint is a backtesting and research platform, not a crystal ball. Every backtest is a simulation, and every simulation makes approximations. This document describes exactly where those approximations are, how much they matter, and what we do to mitigate them.

## Fill Simulation

**Close-price fills overstate performance.** By default, Flint fills market orders at the candle close price. In live trading, market orders move the price -- you pay slippage proportional to your size relative to available liquidity. Close-price fills assume zero impact. Use `FillPipeline` with sqrt-impact or orderbook fill models for final validation.

**vAMM model uses static liquidity depth (dormant).** The vAMM fill path was built for Drift, which is dropped post-hack; it is retained for reference and off by default. It uses a fixed per-market `sqrt_k` for impact estimation rather than a dynamically-adjusted K, so impact estimates can be too optimistic during low-liquidity periods and too conservative during high-liquidity periods. Hyperliquid uses the CLOB fill model instead.

**Orderbook snapshots are point-in-time.** The `OrderbookFillModel` walks a stored L2 snapshot to compute volume-weighted fill prices. Between snapshots, the book can change significantly. This matters most for strategies that trade frequently or in thin markets.

**No queue priority modeling.** Limit orders in backtests fill when price crosses the limit level. In reality, you are behind everyone else in the queue at that price. Aggressive limit orders (close to mid) fill reliably; passive orders (deep in the book) may never fill in practice.

## Funding Rates

**Bar-boundary funding.** Flint applies funding payments at candle boundaries. Real funding settles at venue-specific times (Hyperliquid: every 8 hours; OKX: every 8 hours). For hourly candles the approximation is close. For daily candles, timing error grows because a full day of funding is applied at a single point rather than distributed.

**Close price as mark proxy.** Some venues do not provide historical mark/index prices per funding record. Flint uses candle close as a proxy, which can differ from the actual mark price by 1-10 bps during volatile periods. This affects PnL attribution between "trading PnL" and "funding PnL" but not total PnL.

**Funding data depth varies by venue.** Some venues publish only shallow funding history. Backtests before a venue's available window use interpolated or zero funding, which can materially affect strategies that depend on funding income (e.g., funding harvest, basis trade). Cross-check coverage via `/api/v1/data/funding` and pick venues with complete history for your window.

## Margin and Liquidation

**Per-bar liquidation detection.** Flint checks liquidation conditions at the end of each bar. In reality, liquidation engines run continuously. A position can be liquidated mid-bar during a wick that does not appear in OHLC close prices. This means backtests undercount liquidations, especially on lower timeframes with large wicks.

**Simplified margin model.** The `MarginEngine` uses a flat initial/maintenance margin ratio per venue. Real venues use tiered margin schedules where larger positions require proportionally more margin. Strategies running at high leverage on large positions may see fewer margin calls in backtests than in production.

## Live Execution

**Latency is modeled, not measured.** Backtest latency uses a stochastic model (base delay + random jitter). Real Solana transaction confirmation times vary with network congestion, priority fees, and validator behavior. The model captures the distribution shape but not correlated spikes (e.g., during NFT mints or liquidation cascades).

**No partial fill queuing.** Limit orders that partially fill in backtests are treated as all-or-nothing -- there is no persistent resting order that accumulates fills over multiple bars. Strategies that rely on iceberg or time-weighted execution will see better fill rates in backtests than in production.

**No MEV modeling.** Sandwich attacks, frontrunning, and other MEV activity can degrade real fill prices, particularly on Solana DEXes. Flint does not model adversarial order flow. Strategies trading large size on-chain should assume 1-5 bps of additional hidden cost.

## Data

**Candle data is aggregated.** Sub-candle price action (wicks, order flow within a bar) is lost. A strategy that triggers on a 2% intra-bar move will behave differently with 1-hour candles vs 1-minute candles. When in doubt, backtest on the shortest resolution your strategy actually needs.

**Volume as liquidity proxy.** The sqrt impact model uses bar volume as a denominator. Volume does not equal available liquidity -- a bar with high volume from a single large trade has very different liquidity characteristics than the same volume spread across many small trades. This is a fundamental limitation of OHLCV data.

**Jupiter Perps has no historical backfill.** There is no API for historical borrow rates or volume. Forward collection works, but backtests beyond collected data have no Jupiter-specific funding or volume information. Use Pyth oracle prices as the OHLCV source for Jupiter Perps markets.

## What We Do About It

These limitations are real, and we address them through layered validation rather than pretending they do not exist:

- **Slippage calibration** (`flint calibrate`) fits impact model coefficients from actual live fills, replacing estimates with measurements.
- **Parity testing** (`examples/parity_test_example.py`) runs backtest and paper broker on identical data and flags divergence above 2%. If the two engines disagree, investigate before going live.
- **Monte Carlo bootstrap** (500 iterations on every run with 5+ trades) provides confidence intervals rather than point estimates. A strategy with a wide CI is less trustworthy than one with a narrow CI, regardless of median return.
- **Walk-forward validation** via Optuna prevents overfitting to a single backtest window by optimizing on rolling in-sample periods and testing on out-of-sample data.

The goal is not perfect simulation -- it is knowing exactly where the simulation breaks down so you can size your positions and set your expectations accordingly.
