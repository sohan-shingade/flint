# Web UI Guide

Flint's web interface runs at `http://localhost:8000` after starting the server with `flint serve`. It provides visual tools for every stage of the quant workflow: data exploration, backtesting, optimization, paper trading, and live monitoring.

## Starting the UI

```bash
flint serve                   # production: API + UI at localhost:8000
flint serve --dev             # dev mode: API only at localhost:8000
cd ui && npm run dev          # dev UI at localhost:5173 (hot reload, proxies to API)
```

In production mode, the built React app is served directly by FastAPI. In dev mode, use Vite's dev server for hot reload during UI development.

## Pages

The sidebar navigation lists all pages. Here's what each one does and when to use it.

### BacktestLab

The primary workspace. Write strategies, run backtests, optimize, validate, and deploy -- all in one page.

**Left panel -- Code Editor:**
- Monaco-based editor with Python syntax highlighting
- Load built-in templates from the dropdown (20 strategies)
- Save/load custom strategies with names
- Ctrl+Enter to run, Ctrl+S to save

**Right panel -- Configuration:**
- Market selector (SOL-PERP, BTC-PERP, ETH-PERP, etc.)
- Venue preset (Drift, Hyperliquid, Binance, OKX, Bybit) -- sets default fee rates
- Date range with presets (1M, 3M, 6M, 1Y, 3Y) or custom dates
- Capital and fee rate inputs
- Advanced settings: fill model, slippage, latency, margin tracking, capital allocation

**Run + Optimize buttons:**
- RUN: executes the backtest. Shows progress bar during execution.
- OPTIMIZE: runs Optuna hyperparameter search. Select metric (Sharpe, PnL, Calmar, Win Rate) and trial count (5-500).
- After optimization: WALK-FORWARD button appears for overfitting validation. Set fold count (2-20).

**Data staleness warning:**
A yellow banner appears above the run button if the selected market's data is older than 24 hours, prompting you to download fresh data from the Data tab.

**Results panel (below editor):**
- Metrics summary: PnL, return %, Sharpe, max drawdown, win rate, profit factor, Sortino, Calmar
- Monte Carlo confidence intervals (500 simulations): Sharpe CI, max drawdown CI, ruin probability
- Equity curve chart with buy-and-hold benchmark overlay
- Drawdown chart
- Trade table: entry/exit timestamps, prices, size, PnL per trade
- PnL histogram
- Instrument exposure timeline (for multi-market strategies)

**Export buttons:**
- JSON: full backtest result as JSON file
- TRADES CSV: trade-by-trade export (timestamp, side, entry/exit price, PnL, holding hours)
- EQUITY CSV: equity curve with drawdown (timestamp, equity, drawdown %)

**Regime testing:**
Switch to REGIMES range preset to backtest across multiple market conditions:
- 8 regimes from Dec 2023 to Apr 2026 (bull, bear, crash, sideways, high-vol)
- Select which regimes to test, runs separate backtests for each
- Results show per-regime metrics side by side

**Optimization results:**
- Best parameters with metric value
- Top 10 trials table (metric, PnL, Sharpe, max DD, win rate, params)
- BACKTEST BEST PARAMS button to run the winner
- WALK-FORWARD button with fold selector for overfitting detection
- Walk-forward results: per-fold in-sample vs out-of-sample Sharpe, degradation %, overfit ratio, parameter stability

**Deploy:**
- DEPLOY button opens deploy panel to start a paper trading session with current strategy and params

**Journal:**
- JOURNAL button shows all saved backtest runs
- Checkbox selection on rows to compare 2-4 runs
- COMPARE button shows side-by-side metrics diff table with winner highlighting per metric

### Dashboard

Overview page showing system health and strategy performance.

**Strategy Leaderboard:**
- Ranked table of all strategies from journal runs
- Sort by: avg Sharpe, avg PnL, best Sharpe, win rate, max drawdown, run count
- Groups runs by strategy name, computes per-strategy averages
- Color-coded: positive PnL in green, negative in red, Sharpe > 1 in green

**Data coverage:**
- Shows which markets have data and their date ranges
- Quick-download buttons for common markets

### DataExplorer

Browse and download market data.

**Market browser:**
- Select market and resolution from dropdowns
- View candle chart with volume bars
- Date range picker for filtering

**Download panel:**
- Download OHLCV candles from Drift
- Download funding rates from 7 venues simultaneously
- Progress indicator during download
- Data coverage visualization showing gaps

**Multi-venue view:**
- Select venue to view venue-specific data
- Correlation matrix between markets
- Volume comparison across venues

### Paper Trading

Manage paper trading sessions.

**Sidebar -- Session list:**
- All active and stopped sessions with status indicators
- Green dot = live, amber = replaying, red = stopped
- Click to select and view details

**Main panel -- Session detail:**
- Strategy name, market, deployment time
- Metrics grid: equity, realized PnL, unrealized PnL, total trades, fees, leverage
- Equity curve chart with buy-and-hold overlay
- Trade history table
- Action buttons: STOP (graceful), KILL (immediate)
- PARITY TEST button: compares paper results against a backtest on the same data
  - Shows PnL divergence, fill price MAE, equity correlation, signal timing match
  - Pass/fail indicator (< 2% divergence = pass)

**Deploy panel:**
- Deploy a new strategy from code or built-in template
- Set market, capital, and venue

### LiveMonitor

Monitor live trading sessions on Drift and Hyperliquid.

- Session selector for multi-venue trading
- Real-time equity curve
- Recent fills table
- Position summary

### FundingHeatmap

Cross-venue funding rate visualization.

- Heatmap showing funding rates across markets and venues
- Color scale from negative (green, shorts pay longs) to positive (red, longs pay shorts)
- Useful for identifying funding arbitrage opportunities

### FillAnalysis

Analyze fill quality and slippage.

- Per-venue fill analysis
- Slippage distribution charts
- Compare actual fills vs expected prices

### MevDashboard

MEV opportunity detection and monitoring.

- Arbitrage route scanning
- Cross-venue price discrepancy alerts
- Historical MEV opportunity log

### Setup

Initial configuration wizard.

- API key setup (Birdeye, Helius)
- Venue configuration (Drift keypair, Hyperliquid key, CCXT credentials)
- Data preset selection (Starter: SOL-PERP, Full: all markets)
- Risk configuration defaults

### Docs

Links to documentation. Redirects to the guides in the docs/ directory.

## Keyboard Shortcuts

| Shortcut | Action | Where |
|---|---|---|
| Ctrl+Enter | Run backtest | BacktestLab |
| Ctrl+S | Save strategy | BacktestLab |
| Ctrl+Shift+O | Open strategy | BacktestLab |

## URL Routes

| Path | Page |
|---|---|
| `/` | Dashboard |
| `/backtest` | BacktestLab |
| `/data` | DataExplorer |
| `/paper` | Paper Trading |
| `/live` | LiveMonitor |
| `/funding` | FundingHeatmap |
| `/fills` | FillAnalysis |
| `/mev` | MevDashboard |
| `/setup` | Setup |
| `/docs` | Docs |

## Tech Stack

The UI is built with React 19, Vite, and Tailwind CSS. Key libraries:
- **lightweight-charts v5**: candlestick and equity curve charts
- **Monaco Editor**: strategy code editor (same engine as VS Code)
- **React Router**: client-side routing

To modify the UI, edit files in `ui/src/`. Run `cd ui && npm run dev` for hot reload. Production build: `npm run build` outputs to `ui/dist/` which FastAPI serves automatically.
