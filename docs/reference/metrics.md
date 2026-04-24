# Metrics Reference

Every metric Flint reports on a backtest or paper session, with formula and interpretation. Source: `flint.analytics.tearsheet` + `flint.analytics.metrics`.

Unless otherwise noted, returns are computed on the equity curve, not on individual trades.

## Return metrics

| Metric | Formula | Units |
|---|---|---|
| `total_pnl` | `equity[-1] − initial_capital` | USD |
| `total_return_pct` | `total_pnl / initial_capital` | Decimal |
| `cagr` | `(equity[-1] / initial_capital) ^ (365·86400 / duration_s) − 1` | Decimal |

## Risk-adjusted

### Sharpe ratio

```
sharpe = mean(returns) / stddev(returns) · sqrt(periods_per_year)
```

Computed from bar returns. `periods_per_year` = `31536000 / resolution_s` (31.5M seconds in a year).

| Value | Interpretation |
|---|---|
| `> 2.0` | Excellent; likely overfit on short windows |
| `1.0 – 2.0` | Good; worth walk-forward |
| `0.5 – 1.0` | Marginal after real costs |
| `< 0` | Net losing |

### Sortino ratio

```
sortino = mean(returns) / stddev(negative returns) · sqrt(periods_per_year)
```

Penalizes downside only. Better fit for strategies with asymmetric return distributions (trend-following, options-like payoffs).

### Calmar ratio

```
calmar = cagr / max_drawdown
```

How much return per unit of worst-case drawdown. `>1.0` is a high bar.

## Drawdown

| Metric | Formula |
|---|---|
| `max_drawdown` | `max((peak_i − equity_i) / peak_i)` across the curve, as decimal |
| `max_drawdown_duration_s` | Longest peak-to-recovery stretch |

| Value | Notes |
|---|---|
| `< 10%` | Conservative |
| `10 – 20%` | Typical trend-following |
| `20 – 30%` | Aggressive; requires conviction |
| `> 30%` | Most traders abandon before recovery |

## Trade metrics

| Metric | Formula |
|---|---|
| `total_trades` | Count of closed positions |
| `win_rate` | `winning_trades / total_trades` |
| `profit_factor` | `sum(winning_pnl) / abs(sum(losing_pnl))` |
| `avg_win` | `sum(winning_pnl) / winning_trades` |
| `avg_loss` | `sum(losing_pnl) / losing_trades` |
| `payoff_ratio` | `avg_win / abs(avg_loss)` |
| `expectancy` | `win_rate·avg_win + (1−win_rate)·avg_loss` (per-trade $) |

**Profit factor sanity:**

| Value | Notes |
|---|---|
| `> 1.5` | Strong |
| `1.0 – 1.5` | Marginal after execution |
| `< 1.0` | Net negative |

## Activity / cost

| Metric | Formula | Units |
|---|---|---|
| `total_fees` | Sum of `fill.fee` across all fills | USD |
| `funding_paid` | Sum of hourly funding payments | USD (negative = received) |
| `total_tx_costs` | Priority fees + Jito tips + gas (Solana / EVM) | USD |
| `turnover_annualized` | `(total_trade_notional / avg_equity) · (periods_per_year / total_bars)` | Multiplier |

High turnover with thin edge is the fastest way to bleed out in live trading — watch this alongside Sharpe.

## Per-venue (multi-venue backtests)

- `per_venue_pnl: Dict[str, float]`
- `per_venue_trades: Dict[str, int]`
- `per_venue_funding_income: Dict[str, float]`

## Monte Carlo

For backtests with ≥5 trades, Flint runs a 500-iteration bootstrap of trade PnL order to estimate confidence intervals:

```
monte_carlo = {
  "mean_pnl": ...,
  "median_pnl": ...,
  "p05": ..., "p25": ..., "p75": ..., "p95": ...,
  "prob_positive": 0.78,
  "expected_max_dd": 0.16
}
```

Read: "under resampled trade ordering, there's an 78% chance the strategy is net positive and the median P95 worst-case drawdown is 16%." Not a substitute for walk-forward.

## Walk-forward (from optimization)

- `in_sample_sharpe` / `out_sample_sharpe` per window
- `overfitting_ratio = out_sample / in_sample` (aggregated)
- `parameter_stability` — correlation of best params across windows; `1.0` = identical, `0` = random

| Overfitting ratio | Read |
|---|---|
| `≈ 1.0` | Generalizes |
| `0.5 – 0.8` | Some decay; likely viable |
| `< 0.3` | Severe overfit |
| `< 0` | OoS loses money |

## Strategy warnings

`BacktestResult.strategy_warnings` flags things that can inflate metrics:

- `no_funding_data` — funding payments were zeroed out
- `high_turnover` — turnover above a heuristic cap
- `coverage_gap` — missing candles in the backtest window
- `low_trade_count` — fewer than 5 closed trades (most metrics not meaningful)

Ignore at your peril.

## What to look at first

1. **Trade count.** If `< 30`, stop; metrics aren't statistically meaningful.
2. **Walk-forward overfitting ratio.** Trumps any in-sample Sharpe.
3. **Max drawdown.** If you can't stomach it on paper, you won't tolerate it live.
4. **Profit factor + win rate together.** A 35% win rate at PF 1.8 beats 60% at PF 1.1.
5. **Turnover × fees.** If `total_fees` is a meaningful share of PnL, check calibration.
