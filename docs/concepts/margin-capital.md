# Margin & Capital

How Flint tracks leverage, liquidation, and cross-venue cash. Off by default; enable with `margin_tracking: true` on a backtest request.

## Position key

Positions are keyed by `(venue, market)`. A long on Drift and a short on Hyperliquid for the same market are **two positions**, not a hedge — they consume margin independently on each venue.

This matches reality: you can't cross-margin a Drift position with collateral held on Hyperliquid unless you move it there first.

## `MarginEngine`

`flint/execution/margin.py`. Per bar:

1. Mark-to-market: recompute `unrealized_pnl = size · (mark_price - entry_price)` (signed).
2. Compute `margin_ratio = (collateral + unrealized_pnl) / notional`.
3. If `margin_ratio < maintenance_margin`: liquidate. Liquidation fee = `notional · liquidation_penalty`.

Initial margin, maintenance margin, and liquidation penalty come from `VenueConfig`:

| Venue | Init | Maint | Max lev | Liq penalty |
|---|---:|---:|---:|---:|
| drift | 10% | 5% | 10× | 1% |
| hyperliquid | 5% | 2.5% | 20× | 1% |
| binance | 2% | 1% | 50× | 1% |
| jupiter | 1% | 0.2% | 100× | 0% |

Full table: [reference/venue-configs.md](../reference/venue-configs.md).

## `VenueAllocator`

`flint/execution/capital.py`. When `capital_allocation` is set on a backtest, cash is partitioned:

```json
{ "capital_allocation": { "drift": 5000, "hyperliquid": 3000, "binance": 2000 } }
```

Each venue has its own cash balance. Strategies spend the per-venue balance via orders; `ctx.venue_balance("drift")` reports what's available.

### Transfers

```python
ctx.transfer("drift", "hyperliquid", 1000.0)
```

Transfers are **not instant**. Each venue pair has a configured delay (Solana → EVM bridge time, etc.). `ctx.pending_transfers()` shows in-flight capital. This is why naive "rebalance every bar" logic underperforms in multi-venue backtests — capital gets stuck in flight.

### Fragmentation metrics

`allocator.fragmentation_metrics()` reports:

- `utilization_by_venue` — how much of each balance is in use
- `idle_cash` — total cash not deployed
- `transfer_count`, `total_transferred` — activity stats

Strategies that keep idle cash high are leaving PnL on the table; strategies that transfer constantly pay fees.

## Liquidation

When a position hits maintenance margin:

1. Mark the position `liquidated = True`.
2. Close at the liquidation price (roughly `entry + (maintenance - init) · entry`, signed).
3. Subtract `notional · liquidation_penalty` from cash.
4. Emit a warning into `BacktestResult.strategy_warnings`.

In live, `LiveDriftContext.poll_fills()` detects on-chain liquidations via the Drift program and reconciles local state.

## Per-venue PnL

`BacktestResult.per_venue_pnl` / `per_venue_trades` / `per_venue_funding_income` — useful for debugging cross-venue strategies. If one leg is profitable and the other is a drag, you have fee/slippage asymmetry, not alpha.

## When to enable

- **Leave margin tracking off** for simple single-market strategies where leverage stays ≤1× and drawdowns are bounded by strategy logic.
- **Enable margin tracking** whenever the strategy can use more than 1× leverage, especially on Drift (low liquidity → real liquidation risk) or Jupiter (100× max leverage).
- **Enable capital allocation** for any cross-venue strategy — otherwise Flint pools cash and underestimates how often capital is stuck in-flight.

## Not in this doc

- Risk guards that stop orders *before* they consume margin → [risk-model.md](risk-model.md)
- Fee schedule per venue → [venue-configs.md](../reference/venue-configs.md)
- Fill model tier selection → [fill-pipeline.md](fill-pipeline.md)
