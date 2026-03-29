<p align="center">
  <br/>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://img.shields.io/badge/FLINT-Solana_Trading_Engine-e8a849?style=for-the-badge&labelColor=09090b">
    <img alt="Flint" src="https://img.shields.io/badge/FLINT-Solana_Trading_Engine-e8a849?style=for-the-badge&labelColor=09090b">
  </picture>
  <br/><br/>
  <strong>Backtest, paper trade, and research trading strategies on Solana. Free data, local-first, no cloud.</strong>
  <br/><br/>
  <a href="#getting-started"><img src="https://img.shields.io/badge/setup-1_command-57c84d?style=flat-square&labelColor=141418" alt="1 command"></a>
  <a href="#writing-strategies"><img src="https://img.shields.io/badge/strategies-16_built--in-e8a849?style=flat-square&labelColor=141418" alt="16 strategies"></a>
  <a href="#where-data-comes-from"><img src="https://img.shields.io/badge/providers-14_data_sources-8b5cf6?style=flat-square&labelColor=141418" alt="14 providers"></a>
  <img src="https://img.shields.io/badge/python-3.10+-3776ab?style=flat-square&labelColor=141418" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-AGPL--3.0-gray?style=flat-square&labelColor=141418" alt="AGPL-3.0 License">
</p>

---

## What is Flint?

Flint is a **local trading lab for Solana**. You write a strategy in Python, point it at historical market data, and Flint tells you how it would have performed — with realistic fills, fees, funding rates, and risk metrics.

Everything runs on your machine. Market data comes free from Drift Protocol's public API (no signup, no keys). You get a web UI with a code editor, interactive charts, and one-click backtesting. When you're ready, you can paper trade with live prices or optimize parameters with Optuna.

Flint is built specifically for **Solana DeFi** — Drift perpetuals, Jupiter swaps, on-chain funding rates — but also supports 100+ centralized exchanges via CCXT if you need CEX data.

<p align="center">
  <img src="imgs/homepage.png" alt="Flint homepage" width="100%">
</p>

### Why Flint?

| | Flint | Freqtrade | Hummingbot | TradingView |
|---|:---:|:---:|:---:|:---:|
| **Solana-native** (Drift, Jupiter, Raydium) | Yes | No | Partial | No |
| **Free data** (no API keys needed) | Yes | No | No | Paid |
| **Browser-based strategy editor** | Yes | No | No | Yes |
| **Paper trading with live data** | Yes | Yes | Yes | No |
| **Funding rate analysis** (10 venues) | Yes | Limited | No | No |
| **Optuna optimization** | Yes | Hyperopt | No | No |
| **MCP server** (AI integration) | Yes | No | No | No |
| **Local-first** (nothing leaves your machine) | Yes | Yes | Yes | No |
| **Setup time** | 1 command | 10+ min | Docker | Browser |

---

## Getting Started

### Option 1: One-line install (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/sohan-shingade/flint/main/install.sh | bash
```

This installs Python, Node, clones the repo, builds the UI, and opens your browser. A **setup wizard** walks you through picking markets and downloading data — no terminal required after the initial command.

### Option 2: Docker

```bash
git clone https://github.com/sohan-shingade/flint.git && cd flint
docker compose up
```

Open [localhost:8000](http://localhost:8000) — the setup wizard handles the rest. Data persists in a Docker volume.

### Option 3: From source (developers)

```bash
git clone https://github.com/sohan-shingade/flint.git && cd flint
pip install -e .
flint init
flint serve               # starts everything at localhost:8000
```

Or use the **Makefile** for dev workflows:

```bash
make install              # pip install + npm install
make dev                  # API (hot reload) + UI dev server
make test                 # run all 676 tests
make serve                # production build + serve
make build                # build Docker image
```

For UI development:

```bash
flint serve --dev         # API only on :8000
cd ui && npm run dev      # UI on :5173 with hot reload
```

---

## Core Features

### Strategy Lab

Write strategies directly in the browser with a full Monaco editor (same editor as VS Code). Pick a market, set your date range and capital, and click **Run**. Results appear inline — no context switching.

Flint ships with **15 built-in strategy templates** you can use as starting points: moving average crossovers, RSI, Bollinger Bands, MACD, VWAP reversion, grid trading, funding rate harvesting, and more. Or write your own from scratch.

The lab checks your data coverage before running — if you don't have enough data for the selected date range, it tells you to download it from the Data tab first.

<p align="center">
  <img src="imgs/IMG_1390.png" alt="Strategy Lab" width="100%">
</p>

### Backtest Results

Every backtest produces a full tearsheet: equity curve vs. buy-and-hold, drawdown chart, price chart with trade entry/exit markers, and a complete metrics panel (Sharpe, Sortino, max drawdown, win rate, profit factor, and more).

<p align="center">
  <img src="imgs/IMG_5613.png" alt="Backtest results — equity curve, drawdown, metrics" width="100%">
  <br/>
  <em>Equity curve, drawdown, price action with trade markers, and all metrics at a glance</em>
</p>

Below the overview you get PnL distribution, an exposure timeline, monthly returns heatmap, and a full trade log showing every entry and exit with prices, sizes, and PnL.

<p align="center">
  <img src="imgs/IMG_0222.png" alt="Trade log and monthly returns" width="100%">
  <br/>
  <em>PnL distribution, exposure timeline, monthly returns, and trade-by-trade breakdown</em>
</p>

Backtests aren't just bar-close fills. Flint simulates:

- **Slippage** — configurable basis-point slippage on market orders
- **Fee models** — Drift maker/taker tiers, or flat bps for any venue
- **Stop-loss / take-profit** — checked against each bar's high and low
- **Funding rates** — applied hourly to open positions, just like on Drift
- **Multi-market** — run strategies across multiple markets simultaneously
- **Monte Carlo** — 500-iteration bootstrap with confidence intervals on every run with 5+ trades

### Optimization

Define a `parameters()` method on your strategy and Flint uses **Optuna** to find the best combination. Bayesian search, grid search, or random — your choice. Results show a ranked table of all trials with metrics so you can see parameter stability, not just the best single result.

<p align="center">
  <img src="imgs/IMG_0074.png" alt="Optimization results" width="100%">
  <br/>
  <em>Optuna optimization — 10 trials ranked by Sharpe ratio with one-click "backtest with best params"</em>
</p>

### Paper Trading

Deploy any backtested strategy to run live against real Drift market data with simulated execution. Click **Deploy to Paper** on any backtest result and it goes live immediately.

Each strategy runs as its own independent portfolio with:

- **Replay-forward execution** — replays up to 30 days of history, then seamlessly transitions to live candle processing
- **Risk guardrails** — configurable max drawdown, daily loss limit, position size cap, and perp liquidation simulation
- **Realistic fills** — 5bps slippage, Drift fee schedule, optional order latency
- **Funding rate payments** — applied hourly from real multi-venue data
- **Live PnL updates** — DLOB mid-price polling every 5 seconds
- **Equity curve with buy-and-hold baseline** — see your strategy vs just holding the asset
- **Trade markers** — entry/exit dots on the equity chart
- **Session persistence** — survives server restarts, resumes automatically
- **Multi-venue support** — per-venue capital allocation with transfer delays

### Data Explorer

Interactive TradingView-quality charts powered by lightweight-charts v5. Overlay indicators (SMA, EMA, VWAP, Bollinger Bands, RSI) and toggle between price and funding views.

<p align="center">
  <img src="imgs/IMG_8100.png" alt="Interactive candlestick chart" width="100%">
  <br/>
  <em>Candlestick chart with volume, moving average overlay, and crosshair</em>
</p>

The Data tab also has a **download manager** where you pick which markets and time ranges to fetch. Presets make it easy — "Starter Pack" gets you the top 5 markets, "Everything" gets all 48 Drift markets plus funding from 10 venues.

<p align="center">
  <img src="imgs/IMG_5254.png" alt="Data download manager" width="100%">
  <br/>
  <em>Download presets, venue selection, and market inventory with one-click bulk download</em>
</p>

### Cross-Venue Funding Analysis

Flint pulls funding rates from **10 venues** (Drift, Binance, Hyperliquid, OKX, Bybit, Gate.io, Bitget, dYdX, plus any CCXT exchange) and normalizes them to hourly. The Data Explorer lets you overlay all venues on one chart to spot dislocations — when one venue's funding diverges from the rest, there's a potential arb.

<p align="center">
  <img src="imgs/IMG_7466.png" alt="Cross-venue funding rate comparison" width="100%">
  <br/>
  <em>Funding rates across 7 venues with per-venue statistics — spot dislocations at a glance</em>
</p>

---

## Writing Strategies

Every strategy is a Python class with one method: `on_candle`. It gets called once per candle with the current price and all history. You decide what to do.

### Simple approach — return a signal

```python
from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from flint.indicators import sma

class GoldenCross(Strategy):
    @property
    def name(self): return "golden-cross"

    def on_candle(self, candle, history, ctx=None):
        if len(history) < 50:
            return Signal.HOLD
        if sma(history, 20) > sma(history, 50):
            return Signal.BUY
        elif sma(history, 20) < sma(history, 50):
            return Signal.SELL
        return Signal.HOLD

    def reset(self): pass
```

Return `Signal.BUY` and Flint opens a long. Return `Signal.SELL` and it closes. That's it.

### Advanced approach — full execution control

```python
def on_candle(self, candle, history, ctx=None):
    if ctx is None:
        return Signal.HOLD

    from flint.indicators import rsi, atr

    r = rsi(history, 14)
    risk = atr(history, 14)

    if r < 30 and not ctx.positions:
        ctx.market_order(candle.market, "long", size=1.0)
        ctx.stop_order(candle.market, "short", size=1.0,
                       trigger_price=candle.close - 2 * risk)
    elif r > 70 and ctx.positions:
        ctx.close_position(candle.market)
        ctx.cancel_all()

    return Signal.HOLD
```

With `ctx` you get market orders, limit orders, stop-losses, take-profits, multi-market data access (`ctx.get_candles("BTC-PERP")`), funding rates, open interest, and orderbook depth.

### Making strategies optimizable

Add a `parameters()` classmethod and Flint's optimizer will search the space:

```python
@classmethod
def parameters(cls):
    return {
        "fast_period": {"type": "int", "low": 5, "high": 50, "default": 20},
        "slow_period": {"type": "int", "low": 20, "high": 200, "default": 50},
    }
```

### 20 built-in indicators

`sma`, `ema`, `wma`, `rsi`, `stochastic`, `macd`, `bollinger`, `bollinger_width`, `atr`, `volatility`, `vwap`, `volume_ratio`, `roc`, `adx`, `z_score`, `highest_high`, `lowest_low` — all take `(history, period)` and return floats.

---

## Where Data Comes From

All core data is **free** — no API keys, no signup. Flint pulls from Drift Protocol's public API and stores everything locally in DuckDB.

### Free (no keys needed)

| Source | What you get |
|---|---|
| **Drift Data API** | OHLCV candles for 48 markets (1m to daily), funding rates, L2/L3 orderbook |
| **Drift S3** | Archival trade records for backfilling older data |
| **Drift Open Interest** | Long/short OI for all perp markets |
| **Pyth Network** | Real-time oracle prices for 20 pairs (SOL, BTC, ETH...) |
| **GeckoTerminal** | DEX pool candles for any Solana pool |
| **CoinGecko** | Spot candles for BTC, ETH, SOL (fills gaps for non-Drift assets) |
| **Jupiter** | Swap quotes and routing for any SPL token pair |
| **Raydium + Orca** | AMM/CLMM pool data, TVL, volume from the two largest Solana DEXs |
| **Cross-venue funding** | 10 venues: Drift, Binance, Hyperliquid, OKX, Bybit, Gate.io, Bitget, dYdX + CCXT adapters |

### Optional (free API key, no credit card)

| Source | What you get | Sign up |
|---|---|---|
| **Birdeye** | OHLCV for **any** Solana token | [birdeye.so](https://birdeye.so/developers) |
| **Helius** | Liquidation detection, whale tracking | [helius.dev](https://helius.dev) |

### CCXT (100+ centralized exchanges)

```bash
pip install flint[ccxt]
```

Pull candles, funding, and orderbooks from Binance, Coinbase, Kraken, KuCoin, and 100+ more. Symbol mapping is automatic (`SOL-PERP` maps to each exchange's native format).

### Local storage

Everything is cached in a local DuckDB file (`./data/flint.duckdb`). No data leaves your machine. Subsequent backtests on the same market are instant — no re-downloading.

---

## CLI Quick Reference

```bash
flint init                          # download sample data + run demo backtest
flint serve                         # build UI + start API at localhost:8000
flint serve --dev                   # API only (run UI separately with npm run dev)
flint backtest <strategy.py>        # run backtest from CLI
flint optimize <strategy.py>        # hyperparameter search
flint data download --market SOL-PERP --days 180   # download market data
flint data status                   # show what data you have
flint new my_strategy               # scaffold a new strategy file
flint live --paper                  # paper trade with live prices
```

## MCP Server (AI Integration)

Flint includes an MCP server so AI models (Claude, etc.) can run backtests, query data, and optimize strategies directly.

```bash
pip install flint[mcp]
claude mcp add flint -- python -m flint.mcp_server
```

11 tools available: `run_backtest`, `optimize_strategy`, `get_candles`, `download_market_data`, `list_strategies`, `get_funding_rates`, `get_open_interest`, `get_correlation`, and more.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, FastAPI, DuckDB, Optuna, NumPy |
| Frontend | React 19, Vite, Tailwind CSS, Monaco Editor, lightweight-charts v5 |
| Data | 14 providers (Drift, Pyth, Birdeye, Helius, Raydium, Orca, GeckoTerminal, CoinGecko, Jupiter, CCXT) |
| Funding | 10 venues (Drift, Binance, Hyperliquid, OKX, Bybit, Gate.io, Bitget, dYdX + CCXT) |
| Execution | driftpy (Drift Protocol), Jupiter |
| Testing | 676 tests, all mocked (no network calls) |

---

## Limitations

Flint is a **backtesting and research tool**, not a production trading system. Keep these in mind:

- **Backtests are not predictions.** Past performance doesn't guarantee future results. Overfitting to historical data is easy — use walk-forward validation and Monte Carlo to stress-test your strategies.
- **Fill simulation is approximate.** Slippage models use configurable bps, and orderbook fills use snapshots — neither captures real-time liquidity dynamics, queue priority, or MEV-induced price impact.
- **Funding rate data varies by venue.** Some venues don't provide historical mark/index prices per record, so funding payments in backtests use the candle close price as a proxy for notional calculation.
- **Paper trading, not live execution.** Paper trading uses real prices with simulated fills (slippage, fees, funding). On-chain execution through Drift is scaffolded but not production-ready — use at your own risk.
- **Single-machine only.** Flint runs locally on one machine. There's no distributed mode, no cloud deployment, and no multi-user support. DuckDB is single-writer.
- **Solana-centric.** Data providers are built around the Solana ecosystem (Drift, Jupiter, Raydium, Orca). CEX data is available via CCXT but the execution layer targets Drift.

---

<p align="center">
  <sub>Built for Solana. Powered by Drift Protocol.</sub>
</p>
