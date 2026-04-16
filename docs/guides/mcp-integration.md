# MCP Integration Guide

Flint exposes an MCP (Model Context Protocol) server that lets AI models interact with the platform directly -- running backtests, querying market data, managing paper trading sessions, and optimizing strategies through natural language.

---

## Setup

### Install the MCP dependency

```bash
pip install flint[mcp]
```

### Add to Claude Code

```bash
claude mcp add flint -- python -m flint.mcp_server
```

### Run standalone (stdio transport)

```bash
python -m flint.mcp_server
```

The MCP server accesses DuckDB directly for data tools (candles, funding rates, open interest, correlation, freshness). For paper trading and journal tools, it connects to the Flint API server, which must be running separately:

```bash
flint serve   # start API server at localhost:8000
```

---

## Tools

The MCP server exposes 17 tools organized by category.

### Backtesting

| Tool | Description | Key Args |
|---|---|---|
| `run_backtest` | Run a backtest on Solana market data. Returns PnL, Sharpe, win rate, trades. | `market`, `strategy`, `start_date`, `end_date`, `initial_capital`, `fee_rate`, `code` |
| `list_strategies` | List all 20 built-in strategies with parameters and categories. | -- |

### Paper Trading

| Tool | Description | Key Args |
|---|---|---|
| `start_paper_trading` | Start a paper trading session with simulated fills on live data. | `market`, `strategy`, `initial_capital`, `code` |
| `stop_paper_trading` | Stop a session gracefully. Closes positions and saves state. | `session_id` |
| `get_paper_sessions` | List all sessions with status, equity, PnL, trade count. | -- |
| `get_paper_status` | Detailed status of one session including positions and equity history. | `session_id` |

### Data

| Tool | Description | Key Args |
|---|---|---|
| `get_candles` | Get OHLCV candle data for a market. | `market`, `resolution_s`, `start_date`, `end_date`, `limit` |
| `download_market_data` | Download candles from Drift + funding from 7 venues. | `market`, `days`, `resolution_s`, `funding_venues` |
| `list_available_markets` | List all downloadable markets (Drift perps, spot, CoinGecko). | -- |
| `list_local_markets` | Show what data is cached in the local DuckDB. | -- |
| `get_funding_rates` | Get funding rate history grouped by venue. | `market`, `venue`, `limit` |
| `get_open_interest` | Get open interest for a Drift perp market. | `market` |
| `get_correlation` | Compute cross-market correlation matrix from returns. | `markets`, `resolution_s` |
| `get_data_freshness` | Check how fresh data is across providers and markets. | -- |

### Optimization

| Tool | Description | Key Args |
|---|---|---|
| `optimize_strategy` | Run Optuna hyperparameter search for best strategy params. | `market`, `strategy`, `start_date`, `end_date`, `metric`, `trials` |

### Journal

| Tool | Description | Key Args |
|---|---|---|
| `list_journal_runs` | List past backtest runs with strategy, PnL, Sharpe, win rate. | `limit` |
| `compare_runs` | Compare multiple backtest runs side-by-side. | `run_ids` (comma-separated) |

---

## Resources

The MCP server exposes two resources that AI models can read for context:

| URI | Description |
|---|---|
| `flint://guide` | Platform overview, typical workflow, strategy list, funding venues, paper trading usage |
| `flint://markets` | Current list of available markets with types and counts |

---

## Example Workflow

A typical conversation with an AI model using Flint's MCP tools follows this progression:

**1. Download data**

> "Download 90 days of SOL-PERP data"

The model calls `download_market_data(market="SOL-PERP", days=90)`. This fetches candles from Drift and funding rates from all 7 venues (Drift, Hyperliquid, OKX, Bybit, dYdX, Gate.io, Bitget).

**2. Run a backtest**

> "Run a backtest with the RSI strategy"

The model calls `run_backtest(market="SOL-PERP", strategy="rsi")` and returns PnL, Sharpe ratio, max drawdown, win rate, and trade count.

**3. Optimize parameters**

> "Optimize it with 50 trials"

The model calls `optimize_strategy(market="SOL-PERP", strategy="rsi", trials=50)` and returns the best parameters found (period, oversold/overbought thresholds) along with the top trial scores.

**4. Deploy to paper trading**

> "Paper trade with the best params"

The model calls `start_paper_trading(market="SOL-PERP", strategy="rsi")` (or passes custom code with the optimized parameters) and returns a session ID.

**5. Monitor performance**

> "Check how it is doing"

The model calls `get_paper_sessions()` to see current equity, PnL, and trade count across all active sessions.

**6. Review history**

> "Show me my last 10 backtests"

The model calls `list_journal_runs(limit=10)` to display past runs, then optionally `compare_runs(run_ids="abc,def")` for side-by-side comparison.

---

## Notes

- The MCP server runs on stdio transport by default. It is designed for local use with Claude Code or other MCP-compatible clients.
- Data tools (`get_candles`, `list_local_markets`, `get_funding_rates`, `get_open_interest`, `get_correlation`, `get_data_freshness`, `download_market_data`, `list_available_markets`) access DuckDB directly and do not require the API server.
- Paper trading tools (`start_paper_trading`, `stop_paper_trading`, `get_paper_sessions`, `get_paper_status`) and journal tools (`list_journal_runs`, `compare_runs`) make HTTP requests to `http://127.0.0.1:8000` and require `flint serve` to be running.
- The `run_backtest` and `optimize_strategy` tools access DuckDB directly and run the backtest engine in-process. They do not require the API server.
- All market data is free. No API keys are needed for core functionality (candles, funding, backtesting, optimization, paper trading).
