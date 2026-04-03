# Quickstart Guide

Get from zero to backtest results in under 5 minutes.

## Prerequisites

- Python 3.10 or higher
- Node.js 18+ (only needed if you plan to run the UI in dev mode)
- Git

---

## 1. Install

### Option A: One-line install (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/sohan-shingade/flint/main/install.sh | bash
```

This installs Python, Node, clones the repo, builds the UI, and opens your browser. A setup wizard walks you through the rest -- no terminal needed after the initial command.

### Option B: Docker

```bash
git clone https://github.com/sohan-shingade/flint.git && cd flint
docker compose up
```

Open [localhost:8000](http://localhost:8000) -- the setup wizard handles the rest. Data persists in a Docker volume.

### Option C: From source (developers)

```bash
git clone https://github.com/sohan-shingade/flint.git && cd flint
pip install -e .
flint init
flint serve
```

Or use the Makefile:

```bash
make install              # pip install + npm install
make dev                  # API (hot reload) + UI dev server
make test                 # run all tests
make serve                # production build + serve
make build                # build Docker image
```

Verify the install:

```bash
flint --help
```

---

## 2. Initialize and Onboard

```bash
flint init
```

This downloads a few days of SOL-PERP candles from Drift and runs a sample momentum backtest to confirm everything works.

When you open the UI for the first time, the **setup wizard** walks you through:

1. **Venue selection** -- choose which venues to download data from (Drift, Hyperliquid, or both). Drift is always available with no keys. Hyperliquid candle data is also free.
2. **Market selection** -- pick which markets to download (presets: Starter Pack with top 5 markets, or Everything with all 48 Drift markets plus Hyperliquid equivalents).
3. **Data download** -- fetches OHLCV candles and funding rates from all selected venues. Download warnings are surfaced in the UI if any venue encounters errors.

You can always download more data later from the Data Explorer tab.

---

## 3. Start the Server

```bash
flint serve
```

The API and UI are both served from `http://localhost:8000`. The API binds to `127.0.0.1` by default (local only).

For development, run the API and UI separately:

```bash
flint serve --dev          # API only, on localhost:8000
cd ui && npm run dev       # UI with hot reload at localhost:5173
```

---

## 4. Run Your First Backtest

### Option A: Via the UI

1. Open `http://localhost:8000` in your browser.
2. Navigate to the **BacktestLab** page.
3. Select a strategy from the dropdown (20+ built-in templates available).
4. Choose a market (e.g., **SOL-PERP**).
5. If the strategy is multi-venue (e.g., FundingArb, BasisTrade), a **venue selector** appears automatically -- the UI detects the strategy type from the code.
6. Set a date range, initial capital (e.g., `10000`), and optionally enable **margin tracking** or **capital allocation**.
7. Click **Run Backtest**.

The BacktestLab auto-detects four strategy types:

| Type | Example strategies | How it's detected |
|------|-------------------|-------------------|
| Single-venue | Momentum, RSI, Bollinger | No `venue=` param in orders, no `get_candles()` calls |
| Multi-market | BTC Correlation, Beta-Hedged | Uses `ctx.get_candles("OTHER-MARKET")` |
| Multi-venue | FundingArb, BasisTrade, MultiVenueFunding | Uses `venue=` param on order methods |
| Monitor | MevArbMonitor | No trade methods, observation only |

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

For multi-venue backtests, include `capital_allocation` and `margin_tracking`:

```bash
curl -s -X POST http://localhost:8000/api/v1/backtest/run \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": "funding_arb",
    "market": "SOL-PERP",
    "start_ts": 1740787200,
    "end_ts": 1743465600,
    "initial_capital": 10000,
    "capital_allocation": {"drift": 5000, "hyperliquid": 5000},
    "margin_tracking": true
  }' | python3 -m json.tool
```

The response includes a `backtest_id`. Poll for results:

```bash
curl -s http://localhost:8000/api/v1/backtest/<backtest_id>/results | python3 -m json.tool
```

Results include `status` (`running`, `complete`, or `error`) and a `progress` field (0-100) while the run is in flight.

---

## 5. View Results

Once `status` is `complete`, the results payload contains:

- **metrics** -- total return, Sharpe ratio, Sortino ratio, max drawdown, win rate, profit factor, and more
- **equity_curve** -- list of `{ts, equity}` points for charting
- **trades** -- every fill with entry/exit price, PnL, duration, and venue
- **tearsheet** -- full analytics summary including PnL distribution, exposure timeline, monthly returns heatmap
- **monte_carlo** -- 500-iteration bootstrap with confidence intervals (on runs with 5+ trades)

In the UI, the BacktestLab page displays equity curve vs. buy-and-hold, drawdown chart, price chart with trade entry/exit markers, and the complete metrics panel.

---

## 6. Download More Data

`flint init` only fetches a small sample. To download more data:

### Via the UI

Go to the **Data Explorer** tab and use the download manager. Presets make it easy:

- **Starter Pack** -- top 5 markets from Drift
- **Everything** -- all 48 Drift markets plus funding from 10 venues

Select venues (Drift, Hyperliquid, or both) before downloading. The venue selector in the Data Explorer controls which venue's candles are fetched and stored.

### Via the CLI

```bash
flint data download --market SOL-PERP --days 180
```

### Via the API

```bash
curl -s -X POST http://localhost:8000/api/v1/data/download \
  -H "Content-Type: application/json" \
  -d '{
    "market": "SOL-PERP",
    "resolution": "1m",
    "start_ts": 1709251200,
    "end_ts": 1743465600,
    "venues": ["drift", "hyperliquid"]
  }'
```

Check which markets are available to download:

```bash
curl -s http://localhost:8000/api/v1/data/available-markets | python3 -m json.tool
```

---

## 7. CLI Quick Reference

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
flint live --strategy momentum --market SOL-PERP    # live trade (requires venue keys)
flint calibrate --venue drift --market SOL-PERP     # calibrate slippage model from live fills
```

---

## 8. Next Steps

- [Strategy Authoring Guide](strategy-authoring.md) -- write strategies (signal-based or context-based), understand the 4 strategy types, configure Optuna optimization.
- [Data Provider Guide](data-providers.md) -- understand all 15 data providers, multi-venue candle downloads, set up API keys for Birdeye/Helius.
- [Live Deployment Guide](live-deployment.md) -- deploy to Drift or Hyperliquid (devnet first, then mainnet), risk configuration, kill switch, multi-venue live execution.
- [Architecture Overview](architecture.md) -- execution hierarchy, fill pipeline, margin engine, WebSocket feeds, data flow.
- [Slippage Models Guide](slippage-models.md) -- 4-tier impact model, vAMM curves, calibration engine, transaction cost models.
- **Optimization** -- run `POST /api/v1/optimize/run` with `{"strategy": "momentum", "market": "SOL-PERP", "trials": 50}` to search for best parameters via Optuna.
- **Paper Trading** -- start a live paper session with `POST /api/v1/paper/start` or via the UI's "Deploy to Paper" button on any backtest result.
- **MCP Server** -- integrate Flint with Claude Code: `claude mcp add flint -- python -m flint.mcp_server`.
