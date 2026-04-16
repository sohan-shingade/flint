# Quickstart Guide

Get from zero to a validated, paper-trading strategy in under 30 minutes.

## Prerequisites

- Python 3.10 or higher
- Git

---

## Step 1: Install

```bash
git clone https://github.com/sohan-shingade/flint.git && cd flint
pip install -e .
flint init
flint serve
```

`flint init` downloads a few days of SOL-PERP candle data from Drift and runs a sample momentum backtest to confirm the install works. `flint serve` starts the API and UI at [localhost:8000](http://localhost:8000).

Verify the install:

```bash
flint --help
```

For development (separate API and UI with hot reload):

```bash
flint serve --dev          # API only at localhost:8000
cd ui && npm run dev       # UI with hot reload at localhost:5173
```

---

## Step 2: Download Data

The sample data from `flint init` covers only a few days. Before running real backtests, download historical data for the markets you want to test.

### Via the UI

1. Open [localhost:8000](http://localhost:8000) and go to the **Data Explorer** tab.
2. Select a market (e.g., **SOL-PERP**).
3. Choose a date range. 90-180 days is a good starting point.
4. Click **Download**.

The download fetches OHLCV candles from Drift and funding rates from all enabled venues (Drift, Binance, Hyperliquid, and others). All data providers are free and require no API keys.

### Via the API

```bash
curl -s -X POST http://localhost:8000/api/v1/data/download \
  -H "Content-Type: application/json" \
  -d '{
    "market": "SOL-PERP",
    "resolution": "1m",
    "start_ts": 1727740800,
    "end_ts": 1743465600
  }'
```

Check what markets are available to download:

```bash
curl -s http://localhost:8000/api/v1/data/available-markets | python3 -m json.tool
```

Check what data you already have locally:

```bash
curl -s http://localhost:8000/api/v1/data/freshness | python3 -m json.tool
```

---

## Step 3: Run Your First Backtest

### Via the UI

1. Go to **BacktestLab**.
2. Select **Momentum** from the strategy dropdown. (There are 20 built-in strategies -- Momentum is a good starting point.)
3. Set the market to **SOL-PERP**.
4. Set a date range that falls within your downloaded data.
5. Set initial capital to **10000**.
6. Click **Run Backtest**.

### Via the API

```bash
curl -s -X POST http://localhost:8000/api/v1/backtest/run \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": "momentum",
    "market": "SOL-PERP",
    "start_ts": 1727740800,
    "end_ts": 1743465600,
    "initial_capital": 10000,
    "fee_rate": 0.0006
  }' | python3 -m json.tool
```

The response returns a `backtest_id`. Poll for results:

```bash
curl -s http://localhost:8000/api/v1/backtest/<backtest_id>/results | python3 -m json.tool
```

The `status` field will be `running` (with a `progress` percentage), `complete`, or `error`.

---

## Step 4: Read the Results

Once the backtest completes, the results include metrics, an equity curve, a trade list, and a full tearsheet. Here is what the key metrics mean and how to interpret them.

### Sharpe Ratio

The Sharpe ratio measures risk-adjusted return. It answers: "How much return did I get per unit of risk?"

- **Above 2.0** -- Excellent. The strategy generates strong returns relative to its volatility.
- **1.0 to 2.0** -- Good. Worth investigating further with walk-forward validation.
- **0.5 to 1.0** -- Marginal. May not survive transaction costs and slippage in live trading.
- **Below 0** -- The strategy lost money. It performed worse than holding cash.

### Max Drawdown

The largest peak-to-trough decline in equity during the backtest. A max drawdown of 15% means the account dropped 15% from its highest point before recovering.

- **Under 10%** -- Conservative. Suitable for most risk tolerances.
- **10-20%** -- Moderate. Typical for trend-following strategies.
- **20-30%** -- Aggressive. Requires conviction and a long time horizon.
- **Over 30%** -- Dangerous. Most traders will abandon the strategy before it recovers.

### Win Rate

The percentage of trades that were profitable.

- **Above 60%** -- High win rate. Common in mean-reversion strategies.
- **40-60%** -- Normal range for most strategies.
- **Below 40%** -- The strategy relies on large winners to compensate for frequent small losses. Check the profit factor.

### Profit Factor

Total gross profit divided by total gross loss. A profit factor of 1.5 means the strategy made $1.50 for every $1.00 it lost.

- **Above 1.5** -- Strong.
- **1.0 to 1.5** -- Marginal after accounting for execution costs.
- **Below 1.0** -- The strategy is net negative.

### Other Metrics

- **Total Return** -- Percentage gain or loss on initial capital.
- **Sortino Ratio** -- Like Sharpe, but only penalizes downside volatility. More relevant for strategies with asymmetric returns.
- **Monte Carlo** -- On backtests with 5+ trades, Flint runs a 500-iteration bootstrap to estimate confidence intervals on the results.

### What "Good" Looks Like

For SOL-PERP over the past 6 months, realistic expectations for a single-asset strategy:

- Sharpe above 1.0 with max drawdown under 20% is a solid result.
- Sharpe above 2.0 on historical data often indicates overfitting. Validate with walk-forward before trusting it.
- In a bear market (2025-2026), even a well-designed strategy may have negative total returns. The goal is to lose less than buy-and-hold and to control drawdown.

---

## Step 5: Optimize

Once you have a strategy that shows promise, optimize its parameters to find the best configuration.

### Via the UI

1. In BacktestLab, after running a backtest, click the **OPTIMIZE** button.
2. Set the number of **trials** (50 is a good default; 200 for thorough search).
3. Choose the **metric** to optimize (Sharpe ratio is the default).
4. Click **Run**.

Flint uses Optuna (a Bayesian hyperparameter optimizer) to search the parameter space defined by the strategy's `parameters()` method. Each trial runs a full backtest with different parameter values.

### Via the API

```bash
curl -s -X POST http://localhost:8000/api/v1/optimize/run \
  -H "Content-Type: application/json" \
  -d '{
    "code": "<your strategy code here>",
    "market": "SOL-PERP",
    "start_ts": 1727740800,
    "end_ts": 1743465600,
    "initial_capital": 10000,
    "metric": "sharpe_ratio",
    "trials": 50
  }' | python3 -m json.tool
```

Poll for results the same way as backtests:

```bash
curl -s http://localhost:8000/api/v1/optimize/<run_id>/results | python3 -m json.tool
```

The results include the best parameters found, convergence curve, parameter importance rankings, and the top 20 trials with their metrics.

### Interpreting Optimization Results

- **Convergence curve** -- If the best value plateaus early (within the first 20-30 trials), the search has likely found the optimum. If it is still improving at 50 trials, consider running more.
- **Parameter importance** -- Shows which parameters have the most impact on the metric. If a parameter has near-zero importance, it can be fixed at its default.
- **Best params** -- The parameter combination that produced the highest metric value. Use these as a starting point, not a final answer.

---

## Step 6: Walk-Forward Validation

> **Warning**: Backtests are not predictions. A strategy that looks excellent on historical data may fail completely in live trading. Overfitting is the most common reason strategies fail after deployment. Always validate with walk-forward analysis before paper trading or going live.

Walk-forward analysis is the single most important step in strategy validation. It works by splitting your data into multiple windows, optimizing on each training window, and testing on the subsequent out-of-sample window. This simulates what would have happened if you had optimized and deployed the strategy in real time.

### Via the UI

1. In BacktestLab, after running an optimization, click the **WALK-FORWARD** button.
2. Set the number of **windows** (5 is the default).
3. Set the **train/test split** (70/30 is the default -- 70% of each window for training, 30% for testing).
4. Click **Run**.

### Via the API

```bash
curl -s -X POST http://localhost:8000/api/v1/optimize/walk-forward \
  -H "Content-Type: application/json" \
  -d '{
    "code": "<your strategy code here>",
    "market": "SOL-PERP",
    "start_ts": 1727740800,
    "end_ts": 1743465600,
    "initial_capital": 10000,
    "metric": "sharpe_ratio",
    "n_windows": 5,
    "train_pct": 0.7,
    "trials_per_window": 30
  }' | python3 -m json.tool
```

### Interpreting Walk-Forward Results

The key output is the **overfitting ratio**: the ratio of out-of-sample performance to in-sample performance.

- **Overfitting ratio near 1.0** -- The strategy performs about the same out-of-sample as in-sample. This is the best case.
- **Overfitting ratio 0.5-0.8** -- Some performance degradation out-of-sample, which is normal. The strategy is likely viable.
- **Overfitting ratio below 0.3** -- Severe overfitting. The in-sample results do not generalize. Do not deploy.
- **Negative out-of-sample Sharpe** -- The strategy loses money on unseen data. Go back to Step 5 and try different parameters or a different strategy.

Also check **parameter stability** across windows. If the optimal parameters change drastically from one window to the next, the strategy is fragile and unlikely to work going forward.

---

## Step 7: Deploy to Paper Trading

Paper trading runs your strategy against live market prices with simulated execution. No real money is at risk.

### Via the UI

1. On any completed backtest result, click **Deploy to Paper**.
2. The strategy starts running immediately against live Drift prices.

### Via the API

```bash
curl -s -X POST http://localhost:8000/api/v1/paper/start \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": "momentum",
    "market": "SOL-PERP",
    "initial_capital": 10000,
    "venue": "drift"
  }' | python3 -m json.tool
```

The response includes a `session_id` for monitoring and control.

### What Paper Trading Tests

Paper trading validates aspects that backtesting cannot:

- **Execution timing** -- Can the strategy generate signals fast enough to act on them?
- **Data availability** -- Does the strategy depend on data that arrives late or is missing?
- **Regime sensitivity** -- How does the strategy behave in current market conditions, which may differ from the backtest period?

---

## Step 8: Monitor Paper Trading

### Via the UI

Go to the **Paper Trading** page. Active sessions show:

- **Equity curve** -- Live equity over time, updated on each bar.
- **Open positions** -- Current holdings with unrealized PnL.
- **Trade log** -- Every fill with timestamp, price, and PnL.
- **Session status** -- Running or stopped.

### When to Stop

- **Equity drops below a threshold** -- If the strategy draws down more than its historical max drawdown, stop it. The market conditions may have changed.
- **No trades for an extended period** -- If the strategy goes several days without trading when it should be active, check whether data is flowing correctly.
- **Consistent underperformance vs. backtest** -- If paper results are significantly worse than backtest results after 2-4 weeks, the strategy may be overfit to historical data.

A reasonable paper trading trial is 2-4 weeks. Less than that is too short to draw conclusions. More than 8 weeks without deploying live means the strategy is gathering dust.

---

## Step 9: MCP Integration (Optional)

Flint includes an MCP (Model Context Protocol) server that lets AI tools like Claude Code interact with Flint directly -- running backtests, querying data, and optimizing strategies through natural language.

### Add to Claude Code

```bash
claude mcp add flint -- python -m flint.mcp_server
```

That single command registers Flint as an MCP server. After adding it, you can ask Claude Code to do things like:

- "Run a momentum backtest on SOL-PERP for the last 90 days"
- "Download BTC-PERP data from Drift"
- "Optimize my RSI strategy with 100 trials"
- "What is the current funding rate for SOL-PERP across venues?"

### Available MCP Tools

`run_backtest`, `optimize_strategy`, `get_candles`, `download_market_data`, `list_available_markets`, `list_local_markets`, `list_strategies`, `get_funding_rates`, `get_open_interest`, `get_correlation`, `get_data_freshness`

### Run Standalone

```bash
pip install flint[mcp]
python -m flint.mcp_server
```

For full details, see the [MCP server source](../../flint/mcp_server.py).

---

## Next Steps

- [Strategy Authoring Guide](strategy-authoring.md) -- Write custom strategies, understand the v1/v2 API, configure optimization parameters.
- [First Strategy Tutorial](../tutorials/first-strategy.md) -- Step-by-step walkthrough of building, optimizing, and deploying an RSI-MACD strategy.
- [Data Provider Guide](data-providers.md) -- All 15 data providers, multi-venue downloads, API key setup for Birdeye/Helius.
- [Architecture Overview](architecture.md) -- Execution pipeline, fill models, margin engine, data flow.
- [Slippage Models Guide](slippage-models.md) -- 4-tier impact model, calibration, transaction cost analysis.
- [Live Deployment Guide](live-deployment.md) -- Deploy to Drift or Hyperliquid (devnet first, then mainnet).

## CLI Quick Reference

```bash
flint init                                          # download sample data + run demo backtest
flint serve                                         # build UI + start API at localhost:8000
flint serve --dev                                   # API only (run UI separately with npm run dev)
flint backtest <strategy.py>                        # run backtest from CLI
flint optimize <strategy.py>                        # hyperparameter search
flint data download --market SOL-PERP --days 180    # download market data
flint data status                                   # show what data you have
flint new my_strategy                               # scaffold a new strategy file
flint live --paper                                  # paper trade with live prices
```
