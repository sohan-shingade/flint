# Tutorial 1 — Install and Run Your First Backtest

End state: Flint installed, SOL-PERP data downloaded, a momentum backtest complete, you know what every metric on the result page means.

Time: ~15 minutes.

## Prerequisites

- Python 3.10+
- Git
- Node.js 18+ (optional; only needed for the web UI)

## Step 1 — Install

```bash
git clone https://github.com/sohan-shingade/flint.git && cd flint
pip install -e .
flint init
flint serve
```

`flint init` creates `flint.yaml`, downloads ~90 days of SOL-PERP candles, and runs a sanity-check MA-crossover backtest. `flint serve` starts the API + UI at [localhost:8000](http://localhost:8000).

Alternative installs: pip (`pip install flint-trading`), Docker (`docker compose up`), one-line installer — see the root [README](../../README.md).

## Step 2 — Download more data

The `flint init` sample is enough for one demo backtest. For real work, pull 90–180 days.

**UI:** Data Explorer → select `SOL-PERP` → pick a date range → **Download**. Fetches candles from Hyperliquid (with Pyth oracle prices) and funding rates from all enabled venues. No API keys needed.

**CLI:**

```bash
flint data download --market SOL-PERP --days 180
```

**API:**

```bash
curl -X POST localhost:8000/api/v1/data/download \
  -H 'Content-Type: application/json' \
  -d '{"market":"SOL-PERP","start_ts":1727740800,"end_ts":1743465600}'
```

Check what you have:

```bash
flint data status
```

## Step 3 — Run a backtest

**UI:** BacktestLab → **Momentum** strategy → `SOL-PERP` → pick a date range inside your downloaded data → `$10,000` capital → **Run Backtest**.

**API:**

```bash
RUN_ID=$(curl -s -X POST localhost:8000/api/v1/backtest/run \
  -H 'Content-Type: application/json' \
  -d '{"strategy":"momentum","market":"SOL-PERP","start_ts":1727740800,"end_ts":1743465600,"initial_capital":10000,"fee_rate":0.0006}' \
  | jq -r .id)

# Poll
while true; do
  R=$(curl -s localhost:8000/api/v1/backtest/$RUN_ID/results)
  [ "$(echo $R | jq -r .status)" = "complete" ] && break
  sleep 1
done
echo $R | jq .results.metrics
```

**CLI:** `flint backtest` takes a strategy file, not a template name. For template-based runs use the UI or API. See [reference/cli.md](../reference/cli.md).

## Step 4 — Read the results

Four metrics matter on a first read:

| Metric | What it means | What to look for |
|---|---|---|
| **Sharpe ratio** | Return per unit of volatility | >1.0 interesting; >2.0 suspicious (often overfit) |
| **Max drawdown** | Worst peak-to-trough equity drop | <20% tolerable for most risk profiles |
| **Win rate** | % trades profitable | Useful only with profit factor |
| **Profit factor** | Gross wins / gross losses | >1.5 strong, <1.0 net negative |

Also scan:

- **Trade count.** Under 30? Metrics aren't statistically meaningful.
- **Total fees.** If fees are >20% of gross PnL, the strategy's edge is fragile.
- **Monte Carlo (bottom of tearsheet).** 500-iteration bootstrap shows P5/P95 on PnL — if P5 is deeply negative, the strategy got lucky.

Full definitions: [reference/metrics.md](../reference/metrics.md).

## Step 5 — Try another strategy

In BacktestLab, swap the strategy dropdown. 20 templates: `ma_crossover`, `rsi`, `bollinger`, `momentum`, `rsi_macd_combo`, `funding_harvest`, `mean_reversion`, etc.

Good starting points: `momentum`, `rsi_macd_combo`, `mean_reversion`. Avoid `funding_arb` / `basis_trade` until you've downloaded multi-venue funding data.

Full template catalog with default parameters: [reference/strategy-templates.md](../reference/strategy-templates.md).

## What's next

You have a working install, data, and can run backtests. Pick your direction:

| Goal | Go to |
|---|---|
| Write a custom strategy | [Tutorial 2 — Author a strategy](02-author-a-strategy.md) |
| Tune parameters of an existing strategy | [Tutorial 3 — Optimize + walk-forward](03-optimize-walk-forward.md) |
| Deploy to paper trading | [Tutorial 4 — Paper to live](04-paper-to-live.md) |
| Understand what the engine is doing | [Architecture](../concepts/architecture.md) |

## Troubleshooting

- **`flint: command not found`** — `pip install -e .` didn't register the entry point. Try `python -m flint --help`.
- **No data for date range** — `flint data download --market SOL-PERP --days 90` then retry.
- **UI not loading** — `cd ui && npm install && npm run build` and restart `flint serve`.
- **DuckDB lock error** — only one process can write to the DB. Stop `flint serve` before running the CLI, or use the CLI in-process mode (works when server is stopped).
