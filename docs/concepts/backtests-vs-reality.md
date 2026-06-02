# Backtests vs Reality

What Flint's backtester captures well, what it approximates, and what it cannot model. If you deploy without understanding this, you will lose money.

## What Flint models well

- **Price history** — real OHLCV from Hyperliquid (with Pyth oracle prices) / 100+ CEX via CCXT. No synthesized data.
- **Per-venue fees** — taker/maker rates from actual venue schedules. Maker rebates feed back into PnL.
- **Per-venue funding** — hourly rates from 6 perp venues, forward-filled from the real 8h schedule.
- **Slippage** — 4-tier fill pipeline (orderbook walk / sqrt impact / flat; a dormant vAMM tier is retained for reference) chosen per order per bar.
- **Latency** — per-venue `base_latency ± jitter`, which meaningfully affects short-timeframe strategies.
- **Margin + liquidation** — per-venue maintenance margin, liquidation penalty applied to cash.
- **Transaction costs** — Solana priority fees + Jito tips in lamports.
- **Stop orders** — triggered against each bar's high/low, not close.
- **Multi-venue cash** — per-venue balances with simulated transfer delays.
- **Monte Carlo** — 500-iteration bootstrap of trade order for confidence intervals.

## What Flint approximates

- **Fill prices.** The 4-tier model is far better than close-price fills, but it's still an approximation. Real books move intra-bar; we sample them. Calibration closes the gap; live deployment closes it further.
- **Partial fills.** Probabilistic based on participation threshold — real partials depend on book state, queue position, and counterparty behavior.
- **Orderbook depth.** Only as good as the L2 snapshots in `orderbook_snapshots`. If you didn't collect snapshots at fine granularity, Tier 1 degrades to Tier 2.
- **Funding within the 8h window.** Forward-filled to hourly. Real funding is paid at settlement; mid-window PnL is approximate.

## What Flint does NOT model

- **MEV attacks.** Sandwich trades, frontrunning, and Jito-bundle priority competition are not simulated. On Solana swaps and limit fills, this is a real cost.
- **Queue priority on CLOBs.** Hyperliquid CLOB walks are price-time priority. We don't model queue position — maker fills are optimistic.
- **Venue downtime.** Hyperliquid halts, CEX maintenance windows. Backtests fill as if the venue is always up.
- **RPC failures / network flakes.** Solana RPCs drop transactions. Live strategies need retry logic; backtests assume perfect delivery.
- **Account-level rejections.** Insufficient collateral, leverage caps hit mid-position, self-trade prevention, exchange risk checks. Engine catches the obvious ones; venue-specific quirks are missed.
- **Listing/delisting and symbol changes.** Historical data may look "clean" for a market that was thin/halted in reality.
- **Real-time liquidity dynamics.** Books tighten/widen based on what other participants do; you can't backtest against a book that reacts to you.

## Why this matters for live deployment

Backtest Sharpe overstates live Sharpe, always. Magnitude depends on:

- **Strategy frequency.** Intraday momentum — large gap (MEV + latency + slippage). Weekly rebalance — small gap.
- **Order size relative to liquidity.** If you consume 5% of a bar's volume, you move the market against yourself and the backtest didn't see it.
- **Market regime.** Backtests on bull-run data look great. The same strategy in a crash is a different creature.
- **Calibration quality.** Default `impact_coefficient` is a wide-margin fit. Calibrate from live fills (`flint calibrate`) for narrower confidence.

## How Flint closes the gap

| Tool | Closes | How |
|---|---|---|
| Walk-forward | Overfit → generalizes | Train/test windows; report overfitting ratio |
| Multi-regime testing | Single-regime fragility | Run across 8 regimes; check consistency |
| Monte Carlo | Trade-ordering luck | 500 bootstrap resamples; P05/P95 on PnL + drawdown |
| Parity test | Paper vs backtest drift | Run both on same data; ≤2% PnL divergence required |
| Paper trading | Timing + data-flow issues | Live WebSocket + simulated fills for 2–4 weeks |
| `flint calibrate` | Impact model staleness | Fit impact coefficient from recent live fills |
| Testnet testing | Venue-specific surprises | Hyperliquid testnet before mainnet |

In that order. Do **all** of them before committing capital. See [tutorials/04-paper-to-live.md](../tutorials/04-paper-to-live.md).

## Rules of thumb

- If walk-forward overfitting ratio is < 0.5, the strategy does not generalize. Nothing downstream saves it.
- If paper PnL runs 30%+ below backtest PnL after 2 weeks, your fill model is wrong. Re-calibrate before going live.
- If max drawdown on paper exceeds backtest max drawdown, stop. The strategy is meeting conditions it wasn't designed for.
- If live PnL runs 50%+ below paper PnL, you have a venue-specific issue (latency, queue, MEV). Look at fills individually.

## See also

- [validation/known-limitations.md](../validation/known-limitations.md) — exhaustive list
- [validation/fill-model-comparison.md](../validation/fill-model-comparison.md) — empirical fill-model comparison
- [how-to/run-parity-test.md](../how-to/run-parity-test.md)
- [how-to/calibrate-slippage.md](../how-to/calibrate-slippage.md)
