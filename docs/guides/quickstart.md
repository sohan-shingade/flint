# Quickstart Guide

Get from zero to backtest results in under 5 minutes.

## Prerequisites

- Python 3.10 or higher
- Node.js 18+ (only needed if you plan to run the UI in dev mode)
- Git

## 1. Install

Clone the repo and install Flint in editable mode:

```bash
git clone https://github.com/your-org/flint.git
cd flint
pip install -e .
```

Verify the install:

```bash
flint --help
```

## 2. Initialize

Download sample market data and run a quick sanity-check backtest:

```bash
flint init
```

This downloads a few days of SOL-PERP candles from Drift and stores them in the local DuckDB database. It also runs a sample momentum backtest to confirm everything works.

## 3. Start the Server

```bash
flint serve
```

The API and UI are both served from `http://localhost:8000`. The API binds to `127.0.0.1` by default (local only).

For development, you can run the API and UI separately:

```bash
flint serve --dev          # API only, on localhost:8000
cd ui && npm run dev       # UI with hot reload at localhost:5173
```

## 4. Run Your First Backtest

### Option A: Via the UI

1. Open `http://localhost:8000` in your browser.
2. Navigate to the **BacktestLab** page.
3. Select the **Momentum** strategy from the dropdown.
4. Choose **SOL-PERP** as the market.
5. Set a date range (e.g. last 30 days) and initial capital of `10000`.
6. Click **Run Backtest**.

### Option B: Via the API

```bash
curl -s -X POST http://localhost:8000/api/v1/backtest/run \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": "momentum",
    "market": "SOL-PERP",
    "start_ts": 1740787200,
    "end_ts": 1743465600,
    "initial_capital": 10000,
    "fee_rate": 0.0006
  }' | python3 -m json.tool
```

The response includes a `backtest_id`. Poll for results:

```bash
curl -s http://localhost:8000/api/v1/backtest/<backtest_id>/results | python3 -m json.tool
```

Results include `status` (`running`, `complete`, or `error`) and a `progress` field (0–100) while the run is in flight.

## 5. View Results

Once `status` is `complete`, the results payload contains:

- **metrics** — total return, Sharpe ratio, max drawdown, win rate, profit factor
- **equity_curve** — list of `{ts, equity}` points for charting
- **trades** — every fill with entry/exit price, PnL, and duration
- **tearsheet** — full analytics summary

In the UI, the BacktestLab page displays the equity curve (via the InteractiveChart component), a trade list, and the metrics panel automatically once the run finishes.

## 6. Download More Data

`flint init` only fetches a small sample. To download a full market:

```bash
curl -s -X POST http://localhost:8000/api/v1/data/download \
  -H "Content-Type: application/json" \
  -d '{
    "market": "SOL-PERP",
    "resolution": "1m",
    "start_ts": 1709251200,
    "end_ts": 1743465600
  }'
```

Check which markets are available to download:

```bash
curl -s http://localhost:8000/api/v1/data/available-markets | python3 -m json.tool
```

## 7. Next Steps

- [Strategy Authoring Guide](strategy-authoring.md) — write your own strategies (v1 signal-based or v2 context-based), use the ExecutionContext API, and configure Optuna optimization.
- [Data Provider Guide](data-providers.md) — understand all 14 data providers, set up API keys for Birdeye/Helius, and add custom providers.
- **Optimization** — run `POST /api/v1/optimize/run` with `{"strategy": "momentum", "market": "SOL-PERP", "trials": 50}` to search for best parameters via Optuna.
- **Paper Trading** — start a live paper session with `POST /api/v1/paper/start`.
- **MCP Server** — integrate Flint with Claude Code: `claude mcp add flint -- python -m flint.mcp_server`.
