<p align="center">
  <br/>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://img.shields.io/badge/FLINT-Solana_Trading_Engine-e8a849?style=for-the-badge&labelColor=09090b">
    <img alt="Flint" src="https://img.shields.io/badge/FLINT-Solana_Trading_Engine-e8a849?style=for-the-badge&labelColor=09090b">
  </picture>
  <br/><br/>
  <strong>Algorithmic trading, backtesting, and MEV research for Solana.</strong>
  <br/>
  <em>Think QuantConnect, but native to Solana &mdash; Drift perps, Jupiter swaps, free data, local-first.</em>
  <br/><br/>
  <a href="#quickstart"><img src="https://img.shields.io/badge/setup-3_commands-57c84d?style=flat-square&labelColor=141418" alt="3 commands"></a>
  <a href="#features"><img src="https://img.shields.io/badge/strategies-10_templates-e8a849?style=flat-square&labelColor=141418" alt="10 strategies"></a>
  <a href="#features"><img src="https://img.shields.io/badge/markets-48_on_Drift-8b5cf6?style=flat-square&labelColor=141418" alt="48 markets"></a>
  <a href="#features"><img src="https://img.shields.io/badge/tests-309_passing-57c84d?style=flat-square&labelColor=141418" alt="309 tests"></a>
  <img src="https://img.shields.io/badge/python-3.9+-3776ab?style=flat-square&labelColor=141418" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/license-MIT-gray?style=flat-square&labelColor=141418" alt="MIT License">
</p>

---

```
              )    )
           ( /( ( /(    *   )
           )\()))\())  )  /(
          ((_)\((_)\  ( )(_))
          | |__ | |  (_(_())
          | __| | |  |_   _|
          |_|   |_|    |_|
    ╔══════════════════════════════════╗
    ║  local-first solana algo trading ║
    ╚══════════════════════════════════╝
```

## Why Flint?

Most crypto trading frameworks are built for centralized exchanges. Flint is **purpose-built for Solana DeFi** — Drift perpetuals, Jupiter swaps, on-chain data, and MEV-aware execution.

**No API keys needed.** Market data is free from Drift's public data API. Install, run, and start backtesting in under a minute.

| | Flint | Freqtrade | QuantConnect | Jesse |
|---|:---:|:---:|:---:|:---:|
| Solana-native | **Yes** | No | No | No |
| Free data (no signup) | **Yes** | No | Limited | No |
| Local-first (no cloud) | **Yes** | Yes | Cloud | Yes |
| On-chain execution | **Drift** | CCXT (CEX) | Multi-broker | CEX |
| MEV framework | **Yes** | No | No | No |
| Web UI included | **Yes** | FreqUI | Cloud IDE | No |
| Strategy Lab (Monaco) | **Yes** | No | Cloud IDE | No |

<a name="quickstart"></a>
## Quickstart

```bash
pip install -e .          # install flint + dependencies
flint init                # downloads market data, runs a sample backtest
flint serve               # starts the web UI at localhost:5173
```

That's it. Open [localhost:5173](http://localhost:5173) and you'll see the dashboard with your data loaded.

### Your first backtest

```bash
# From the CLI
flint backtest strategies/user/my_strategy.py --market SOL-PERP --start 2025-01-01 --end 2025-06-01

# Or use the Strategy Lab UI — write code in Monaco, click Run
flint serve
```

### Docker

```bash
docker compose up         # API + UI, data persisted in ./data/
```

## What You Can Do

### Write Strategies in Python

Flint has two strategy APIs — pick whichever fits your style:

**Simple (v1)** — return a signal, Flint handles execution:

```python
from flint.strategy.base import Strategy
from flint.models import Candle, Signal

class MyStrategy(Strategy):
    name = "golden-cross"

    def on_candle(self, candle: Candle, history: list[Candle], ctx=None) -> Signal:
        if len(history) < 50:
            return Signal.HOLD

        from flint.indicators import sma
        fast = sma(history, 20)
        slow = sma(history, 50)

        if fast > slow:
            return Signal.BUY
        elif fast < slow:
            return Signal.SELL
        return Signal.HOLD
```

**Advanced (v2)** — full control with limit orders, stop-losses, position sizing:

```python
def on_candle(self, candle, history, ctx=None):
    if ctx is None:
        return Signal.HOLD

    from flint.indicators import rsi, atr

    r = rsi(history, 14)
    risk = atr(history, 14)

    if r < 30 and not ctx.positions:
        # Oversold — enter with stop-loss and take-profit
        ctx.market_order(candle.market, "long", size=1.0)
        ctx.stop_order(candle.market, "short", size=1.0,
                       trigger_price=candle.close - 2 * risk)
        ctx.take_profit_order(candle.market, "short", size=1.0,
                              trigger_price=candle.close + 3 * risk)
    elif r > 70 and ctx.positions:
        ctx.close_position(candle.market)
        ctx.cancel_all()

    return Signal.HOLD
```

### Backtest with Realistic Execution

Not just bar-close fills. Flint simulates real trading conditions:

- **Fill models** — close-price, next-bar-open, configurable slippage (bps)
- **Fee models** — Drift maker/taker tiers, Hyperliquid, Binance, OKX, Bybit, or flat bps
- **Stop-loss / take-profit** — checked against high/low of each bar
- **Limit orders** — filled when price crosses your level
- **Funding rates** — applied to open positions (hourly, like Drift)
- **Multi-market** — backtest across multiple markets simultaneously
- **Monte Carlo** — 500-iteration bootstrap on every backtest with 5+ trades

### Optimize with Optuna

```python
class MyStrategy(Strategy):
    @classmethod
    def parameters(cls):
        return {
            "fast_period": {"type": "int", "low": 5, "high": 50},
            "slow_period": {"type": "int", "low": 20, "high": 200},
            "rsi_threshold": {"type": "float", "low": 20, "high": 40},
        }
```

```bash
flint optimize strategies/user/my_strategy.py --market SOL-PERP --trials 100 --metric sharpe
```

Or use the **Optimize** button in Strategy Lab — results appear inline with parameter heatmaps.

Walk-forward analysis validates that optimized parameters hold up out-of-sample.

### Paper Trade

Same strategy code, real prices, simulated execution:

```bash
flint live --paper --strategy strategies/user/my_strategy.py --market SOL-PERP
```

### Explore Data

Interactive TradingView-quality charts with:
- OHLCV candlesticks with volume
- SMA, EMA, VWAP, Bollinger Bands, RSI overlays
- Configurable time horizons (1W to ALL)
- Auto-download from Drift if data isn't cached locally
- 48 markets: 42 perps + 6 spot on Drift

<a name="features"></a>
## Features

<table>
<tr>
<td width="50%">

### Strategy Engine
- v1 signal API + v2 ExecutionContext
- 10 built-in templates (MA, RSI, Bollinger, VWAP, grid, funding harvest, breakout, mean reversion, dual timeframe, multi-indicator)
- 20 built-in indicators (SMA, EMA, RSI, MACD, Bollinger, ATR, VWAP, ADX, stochastic, z-score...)
- User strategy hot-loading from `strategies/user/`

### Backtesting
- Pluggable fill models (close, next-open, slippage)
- Pluggable fee models (6 venue presets)
- Stop-loss, take-profit, limit orders
- Funding rate simulation
- Multi-market engine
- Monte Carlo confidence intervals

### Optimization
- Optuna bayesian/grid/random search
- 5 objective metrics (Sharpe, Sortino, return, drawdown, win rate)
- Walk-forward out-of-sample validation
- Parameter stability analysis

</td>
<td width="50%">

### Data
- 48 Drift markets (42 perp + 6 spot)
- Auto-download from Drift Data API
- DuckDB local cache (no cloud, no API keys)
- Funding rates, oracle prices, orderbook snapshots
- Cross-venue funding (Drift, Hyperliquid, OKX)
- Parquet export/import

### Trading
- Paper trading with real Drift prices
- Risk guards (max drawdown, position limits, daily loss)
- Notifications (Telegram, Discord, webhooks)
- Fixed-point precision (Decimal at boundaries)
- WebSocket streaming

### Web UI
- Strategy Lab with Monaco editor
- Interactive charts (lightweight-charts v5)
- Data Explorer with indicator overlays
- Trade journal with run history
- MEV Scanner dashboard
- Built-in documentation

</td>
</tr>
</table>

## CLI Reference

```
flint init                          # Download data + sample backtest
flint serve                         # Start API + UI
flint backtest <strategy.py>        # Run backtest
flint optimize <strategy.py>        # Hyperparameter optimization
flint data download                 # Download/update market data
flint data status                   # Show data coverage
flint new <name>                    # Scaffold a new strategy
flint live --paper                  # Paper trading
```

## Architecture

```
flint/
├── strategy/          # Strategy ABC, 10 built-in templates, loader
├── execution/         # ExecutionContext, BacktestContext, fill/fee models
├── backtest/          # Event-driven backtest engine, Drift simulation
├── optimization/      # Optuna optimizer, walk-forward, stability
├── paper/             # Paper trading engine + broker
├── risk/              # Risk guards (drawdown, position, daily loss)
├── portfolio/         # Multi-strategy engine, allocators
├── providers/         # Drift Data API, Drift S3, funding rates
├── connectors/        # Drift (driftpy), Jupiter
├── analytics/         # Metrics, tearsheet, Monte Carlo
├── indicators.py      # 20 technical indicators
├── precision.py       # Fixed-point math for Solana
├── store.py           # Thread-safe DuckDB store
├── config.py          # Pydantic settings (YAML + env)
├── cli.py             # Typer CLI
├── api/               # FastAPI (25+ endpoints, WebSocket)
│   └── routes/        # backtest, data, paper, journal, optimization
├── notifications/     # Telegram, Discord, webhook
├── journal/           # Backtest run persistence
└── mev/               # Arb detection, liquidation scanning

ui/                    # React 19 + Vite + Tailwind
├── pages/             # Dashboard, Strategy Lab, Data Explorer, Docs, MEV
├── components/        # Charts, editors, metrics cards
└── hooks/             # useBacktest, useOptimize, useJournal

tests/                 # 309 tests across 36 test files
strategies/user/       # Your strategies go here
```

## Coming from Freqtrade?

| Freqtrade | Flint |
|---|---|
| `populate_indicators(df)` | `on_candle(candle, history)` |
| `minimal_roi`, `stoploss` | `ctx.stop_order()`, `ctx.take_profit_order()` |
| `Hyperopt` | `flint optimize` (Optuna) |
| `--dry-run` | `flint live --paper` |
| `freqtrade download-data` | `flint data download` (or auto on first backtest) |
| CCXT (CEX only) | Drift Protocol (Solana on-chain) |
| DataFrame-based | Candle objects + indicator functions |

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.9+, FastAPI, DuckDB, Optuna |
| Frontend | React 19, Vite, Tailwind CSS, Monaco Editor, lightweight-charts |
| Data | Drift Data API, Drift S3 (archival), Parquet |
| Execution | driftpy (Drift Protocol), Jupiter |
| CLI | Typer + Rich |
| Infra | Docker, WebSocket |

## Configuration

Flint uses `flint.yaml` in the project root + environment variables (`FLINT_` prefix):

```yaml
db:
  path: ./data/flint.duckdb

trading:
  default_capital: 10000
  default_fee_bps: 5

collector:
  markets: ["SOL-PERP", "BTC-PERP", "ETH-PERP"]
  resolution_s: 3600

api:
  host: 0.0.0.0
  port: 8000
```

See `.env.example` for environment variable options.

## Development

```bash
# Setup
pip install -e ".[dev]"
cd ui && npm install

# Run tests
pytest tests/ -v          # 309 tests

# Dev servers
flint serve               # API on :8000, UI on :5173
```

## License

MIT

---

<p align="center">
  <sub>Built for Solana. Powered by Drift Protocol.</sub>
</p>
