# Tutorial 3 — Optimize and Walk-Forward

End state: you know how to find good parameters with Optuna, how to detect overfitting with walk-forward, and when to stop tuning.

Prereq: Tutorial 2 finished — you have a custom strategy file with a `parameters()` method.

Time: ~20 minutes.

## Why optimize?

Default parameters are compromises. A strategy that works at `(14, 28)` might be much better at `(9, 32)`. Optuna searches the space more efficiently than a grid.

## The risk of optimizing

The same search will find parameters that look great on history **but won't survive out-of-sample**. This is overfitting. Walk-forward is how you detect it.

Rule: any parameter set you find via optimization **must** be validated with walk-forward before you trust it. If you skip this step, you will lose money.

## Step 1 — Make your strategy optimizable

Add a `parameters()` classmethod:

```python
@classmethod
def parameters(cls):
    return {
        "rsi_period":       {"type": "int",   "low": 5,    "high": 30,  "default": 14},
        "macd_fast":        {"type": "int",   "low": 6,    "high": 20,  "default": 12},
        "macd_slow":        {"type": "int",   "low": 20,   "high": 50,  "default": 26},
        "rsi_oversold":     {"type": "float", "low": 20.0, "high": 40.0, "default": 30.0},
        "rsi_overbought":   {"type": "float", "low": 60.0, "high": 80.0, "default": 70.0},
    }
```

Supported field types: `int`, `float` (with optional `log: true` for log-uniform), `categorical` (with `choices`). `low`/`high` are required; `default` is optional.

Keep the space **narrow but not trivial**. A 1000-point space with 100 trials is under-sampled; a 5-point space doesn't need Optuna.

## Step 2 — Run optimization

**CLI:**

```bash
flint optimize strategies/user/rsi_macd.py \
  --market SOL-PERP \
  --period 180d \
  --metric sharpe_ratio \
  --trials 100
```

**API:**

```bash
curl -X POST localhost:8000/api/v1/optimize/run \
  -H 'Content-Type: application/json' \
  -d '{
    "code": "<strategy source>",
    "market": "SOL-PERP",
    "start_ts": 1720000000, "end_ts": 1743465600,
    "initial_capital": 10000,
    "metric": "sharpe_ratio",
    "trials": 100
  }'
```

**UI:** BacktestLab → run a backtest → **OPTIMIZE** button → choose trials + metric → Run.

Metrics: `sharpe_ratio` (default), `total_pnl`, `sortino`, `profit_factor`, `calmar_ratio`.

## Step 3 — Read the optimization output

```json
{
  "best_value": 2.41,
  "best_params": { "rsi_period": 9, "macd_fast": 8, "macd_slow": 32, ... },
  "trials": [
    { "metric_value": 2.41, "total_pnl": 2380, "params": {...} },
    { "metric_value": 2.39, "total_pnl": 2210, "params": {...} }
  ],
  "param_importance": { "macd_slow": 0.42, "rsi_period": 0.31, ... },
  "convergence": [[0, 0.4], [1, 0.4], [5, 1.2], [20, 2.1], [50, 2.35], [100, 2.41]]
}
```

Three things to check:

1. **Convergence curve.** Did it plateau? If best value is still climbing at trial 100, run more. If it flattened by trial 30, 100 was overkill.
2. **Parameter importance.** Near-zero importance → fix that parameter to its default. Shrinks the space for future searches.
3. **Top trials.** Look at 5–10 top trials. If their param values are all over the place, the search didn't converge on a region — the "best" is noisy. If they cluster, you found a real peak.

## Step 4 — Walk-forward validation

The critical step. Walk-forward splits your data into N windows, optimizes on each training slice, and measures performance on the subsequent out-of-sample slice. This mimics what would have happened if you had re-optimized periodically in real time.

**API:**

```bash
curl -X POST localhost:8000/api/v1/optimize/walk-forward \
  -H 'Content-Type: application/json' \
  -d '{
    "code": "<strategy source>",
    "market": "SOL-PERP",
    "start_ts": 1709000000, "end_ts": 1743465600,
    "initial_capital": 10000,
    "metric": "sharpe_ratio",
    "n_windows": 5,
    "train_pct": 0.7,
    "trials_per_window": 30
  }'
```

**UI:** BacktestLab → after an optimization → **WALK-FORWARD** button → N windows, train/test split.

## Step 5 — Read walk-forward results

The key number is the **overfitting ratio**:

```
overfitting_ratio = out_sample_metric / in_sample_metric
```

| Value | Verdict |
|---|---|
| ~1.0 | Generalizes. Deploy. |
| 0.5–0.8 | Some decay. Likely viable. |
| 0.3–0.5 | Marginal. Don't deploy without more validation. |
| <0.3 | Severe overfit. Do not deploy. |
| Negative OoS | Out-of-sample loses money. Abandon these params. |

Also check **parameter stability**: correlation of best params across windows. If the "best" RSI period jumps from 8 to 24 across consecutive windows, the strategy is fragile — no stable optimum exists.

**Window-level view:**

```
Window 1: train Jan–Feb, test Mar → in 2.4, out 1.8 (decay 25%)
Window 2: train Feb–Mar, test Apr → in 2.1, out 1.5 (decay 29%)
Window 3: train Mar–Apr, test May → in 2.3, out -0.4 (BROKEN)
Window 4: train Apr–May, test Jun → in 1.9, out 0.9 (decay 53%)
Window 5: train May–Jun, test Jul → in 2.2, out 1.4 (decay 36%)
```

Window 3 is a red flag. Even with good average metrics, **one regime breaks the strategy**. Find out why (news event? funding spike? low-volume period?) before deploying.

## Step 6 — Multi-regime sanity check

One more filter. Run your optimized strategy across all 8 regimes:

```bash
curl -X POST localhost:8000/api/v1/backtest/run-regimes \
  -H 'Content-Type: application/json' \
  -d '{"regime_ids":["etf_bull_run","summer_correction","extended_decline","crash_phase_1","crash_phase_2"],
       "code":"...", "market":"SOL-PERP", "initial_capital":10000}'
```

If the strategy is net negative in 2+ regimes, it's directionally biased. Fine if you can detect the regime live; risky if you can't.

Regime explanation: [concepts/regimes.md](../concepts/regimes.md).

## When to stop tuning

Stop when:

- Walk-forward overfitting ratio ≥ 0.5 and stable across windows.
- Multi-regime Sharpe is positive in ≥ 6/8 regimes.
- The top 10 trials cluster around one param region.

Don't stop when:

- You're still adding new hyperparameters. Every new parameter doubles the overfitting surface. Drop low-importance params before adding new ones.
- You're only testing on one market. Add BTC-PERP, ETH-PERP for robustness.
- Total fees are a large share of PnL. Re-check the fill model before tuning further.

## What's next

- [Tutorial 4 — Paper to live](04-paper-to-live.md) — deploy your validated strategy
- [concepts/backtests-vs-reality.md](../concepts/backtests-vs-reality.md) — what walk-forward can and cannot catch
- [reference/metrics.md](../reference/metrics.md) — every metric definition
