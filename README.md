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
  <a href="#data-sources"><img src="https://img.shields.io/badge/providers-13_data_sources-8b5cf6?style=flat-square&labelColor=141418" alt="13 providers"></a>
  <a href="#testing"><img src="https://img.shields.io/badge/tests-497_passing-57c84d?style=flat-square&labelColor=141418" alt="497 tests"></a>
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

**No API keys needed to start.** Core market data is free from Drift's public API. Install, run, and backtest in under a minute.

| | Flint | Freqtrade | QuantConnect | Jesse |
|---|:---:|:---:|:---:|:---:|
| Solana-native | **Yes** | No | No | No |
| Free data (no signup) | **Yes** | No | Limited | No |
| Local-first (no cloud) | **Yes** | Yes | Cloud | Yes |
| On-chain execution | **Drift** | CCXT (CEX) | Multi-broker | CEX |
| CEX data via CCXT | **Yes** | Yes | No | No |
| MEV framework | **Yes** | No | No | No |
| Web UI + Monaco editor | **Yes** | FreqUI | Cloud IDE | No |
| Multi-market strategies | **Yes** | Yes | Yes | No |

<a name="quickstart"></a>
## Quickstart

```bash
pip install -e .          # install flint + dependencies
flint init                # downloads market data, runs a sample backtest
flint serve               # builds UI + starts API — everything at localhost:8000
```

Open [localhost:8000](http://localhost:8000) — API and UI both run from a single command.

```bash
# Run a backtest from CLI
flint backtest strategies/user/my_strategy.py --market SOL-PERP --start 2025-01-01 --end 2025-06-01

# Or use the Strategy Lab UI — write code in Monaco, click Run
flint serve

# Docker
docker compose up
```

## What You Can Do

### Write Strategies in Python

Create a `.py` file in `strategies/user/` — it automatically appears in the Strategy Lab UI. Or write directly in the browser with the Monaco editor.

Two strategy APIs — pick whichever fits:

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

**Advanced (v2)** — full control with limit orders, stop-losses, multi-market:

```python
def on_candle(self, candle, history, ctx=None):
    if ctx is None:
        return Signal.HOLD

    from flint.indicators import rsi, atr

    r = rsi(history, 14)
    risk = atr(history, 14)

    # Access other markets for cross-market strategies
    btc = ctx.get_candles("BTC-PERP", 20)

    if r < 30 and not ctx.positions:
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
- **Multi-market** — backtest across multiple markets simultaneously, auto-detected from strategy code
- **Monte Carlo** — 500-iteration bootstrap on every backtest with 5+ trades
- **Data quality checks** — gap detection, outlier detection, duplicate removal before every run

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

Or use the **Optimize** button in Strategy Lab — results appear inline.

Walk-forward analysis validates that optimized parameters hold up out-of-sample.

### Paper Trade

Same strategy code, real prices, simulated execution:

```bash
flint live --paper --strategy strategies/user/my_strategy.py --market SOL-PERP
```

### Explore Data

Interactive TradingView-quality charts (lightweight-charts v5) with:
- OHLCV candlesticks with volume
- SMA, EMA, VWAP, Bollinger Bands, RSI overlays
- Configurable time horizons (1W to ALL)
- Auto-download from Drift if data isn't cached locally

<a name="features"></a>
## Features

<table>
<tr>
<td width="50%">

### Strategy Engine
- v1 signal API + v2 ExecutionContext
- 10 built-in templates (MA, RSI, Bollinger, VWAP, grid, funding harvest, breakout, mean reversion, dual timeframe, multi-indicator)
- 20 built-in indicators (SMA, EMA, RSI, MACD, Bollinger, ATR, VWAP, ADX, stochastic, z-score...)
- Multi-market strategies via `ctx.get_candles()`
- User strategy hot-loading from `strategies/user/`

### Backtesting
- Pluggable fill models (close, next-open, slippage)
- Pluggable fee models (6 venue presets)
- Stop-loss, take-profit, limit orders
- Funding rate simulation
- Multi-market engine with auto-sync
- Monte Carlo confidence intervals
- Data quality checks (gaps, outliers, completeness)

### Optimization
- Optuna bayesian/grid/random search
- 5 objective metrics (Sharpe, Sortino, return, drawdown, win rate)
- Walk-forward out-of-sample validation
- Parameter stability analysis

</td>
<td width="50%">

### Data (13 Providers)
- 48 Drift markets (42 perp + 6 spot) — free, no key
- Birdeye — any Solana token (free API key)
- CCXT — 100+ centralized exchanges (optional install)
- Pyth — real-time oracle feeds for 20 pairs
- Raydium + Orca — DEX pool data, TVL, volume
- Cross-venue funding (Drift, Hyperliquid, OKX, Bybit, Binance)
- Helius — liquidations, whale tracking (free API key)
- Open interest, orderbook snapshots, oracle prices
- Parquet export/import, incremental sync
- Configurable per provider via `flint.yaml` / CLI

### Trading & Risk
- Paper trading with real Drift prices
- Risk guards (max drawdown, position limits, daily loss)
- Notifications (Telegram, Discord, webhooks)
- Fixed-point precision (Decimal at boundaries)
- WebSocket streaming
- Cross-market correlation matrix

### Web UI
- Strategy Lab with Monaco editor
- Interactive charts (lightweight-charts v5)
- Data Explorer with indicator overlays
- Trade journal with persistent run history
- MEV Scanner dashboard
- Built-in documentation

</td>
</tr>
</table>

---

<a name="data-sources"></a>
## Data Sources

Flint aggregates data from 13 providers into a local DuckDB database. **All core data is free** — no API keys required to start backtesting.

### Free — No Keys Needed

| Provider | Data | Coverage |
|---|---|---|
| **Drift Data API** | OHLCV candles (1m→monthly), funding rates, orderbook L2/L3 | 48 markets, current data |
| **Drift S3** | Historical trade records (archival backfill) | 90+ days of raw trades |
| **Drift Open Interest** | Long/short OI per market | All Drift perp markets |
| **Pyth Network** | Real-time oracle price feeds with confidence intervals | 20 pairs (SOL, BTC, ETH...) |
| **GeckoTerminal** | DEX pool OHLCV for any Solana pool | Any Solana DEX pool |
| **Jupiter** | Swap quotes, routing, price discovery | Any SPL token pair |
| **Raydium** | AMM/CLMM pool data, reserves, fees, TVL, volume | Largest Solana DEX |
| **Orca** | Whirlpool concentrated liquidity positions, pool stats | Second-largest Solana DEX |
| **Hyperliquid** | Hourly funding rates | 17 markets |
| **OKX** | 8h funding rates (normalized to 1h) | Major markets |
| **Bybit** | 8h funding rates (normalized to 1h) | Major markets |
| **Binance** | 8h funding rates (normalized to 1h) | Major markets (US geo-blocked) |

### Free API Key Required (no credit card)

| Provider | Data | Sign Up |
|---|---|---|
| **Birdeye** | OHLCV for **any** Solana token, token metadata, price history | [birdeye.so/developers](https://birdeye.so/developers) |
| **Helius** | Liquidation detection, whale wallet tracking, parsed transactions | [helius.dev](https://helius.dev) |

### CCXT — 100+ Centralized Exchanges

```bash
pip install flint[ccxt]          # optional install
flint data exchanges             # list supported exchanges
flint data markets binance       # list available markets
```

Pull candles, funding rates, orderbooks, and tickers from Binance, Bybit, OKX, Coinbase, Kraken, KuCoin, and dozens more. Symbol mapping (`SOL-PERP` ↔ `SOL/USDT:USDT`) is automatic.

### Local Storage

All data is cached in DuckDB (`./data/flint.duckdb`). No data leaves your machine. 12 tables:

| Table | What It Stores |
|---|---|
| `candles` | OHLCV price data |
| `funding_rates` | Drift funding rates |
| `oracle_prices` | Oracle price snapshots |
| `orderbook_snapshots` | L2 bid/ask depth |
| `venue_funding_rates` | Cross-venue funding (5 venues) |
| `pool_snapshots` | AMM pool reserves |
| `open_interest` | Long/short OI from Drift |
| `liquidations` | Liquidation events (via Helius) |
| `whale_transfers` | Large token movements (via Helius) |
| `dex_volume` | DEX volume per market/venue |
| `token_unlocks` | Token vesting schedules |
| `sync_metadata` | Freshness tracking per provider |

### Configuring Providers

```bash
# Enable/disable via CLI
flint data provider status
flint data provider enable birdeye --api-key YOUR_KEY
flint data provider disable binance

# Or via flint.yaml
providers:
  drift:
    enabled: true
  birdeye:
    enabled: true    # needs FLINT_BIRDEYE_API_KEY in .env
  ccxt:
    enabled: true
    exchange: binance
```

---

<a name="testing"></a>
## Testing

Flint has **497 tests** across 44 test files covering every layer of the platform.

### Test Coverage

| Area | Tests | What's Tested |
|---|---|---|
| **Strategy Engine** | ~40 | v1/v2 API, signal generation, parameter validation, strategy loading, user code hot-reload |
| **Backtest Engine** | ~35 | Fill models (close, next-open, slippage), fee models, SL/TP triggers, limit orders, multi-market sync, v1 backwards compat |
| **Backtest Context** | ~25 | Order lifecycle, position tracking, PnL calculation, funding application, dust guards, 100-order cap |
| **Data Providers** | ~190 | Birdeye, Helius, Pyth, Raydium, Orca, CCXT, Drift API, Drift S3, GeckoTerminal, open interest (all mock-based) |
| **Provider Registry** | ~20 | Registration, enable/disable, config loading, data type routing, status reporting |
| **Store (DuckDB)** | ~50 | All 12 tables — upsert, query, filtering, sync metadata, freshness, thread safety |
| **Models** | ~30 | All dataclasses — creation, defaults, frozen immutability, computed properties |
| **Optimization** | ~15 | Optuna integration, walk-forward, parameter search, metric objectives |
| **Risk Management** | ~15 | Max drawdown, position limits, daily loss, circuit breaker, guard chaining |
| **Analytics** | ~25 | Metrics, tearsheet, Monte Carlo, correlation matrix, rolling correlation |
| **Portfolio** | ~10 | Multi-strategy engine, equal-weight/inverse-vol allocators |
| **Notifications** | ~10 | Telegram, Discord, webhook dispatch (mock HTTP) |
| **Paper Trading** | ~10 | Broker simulation, order matching, state persistence |
| **API** | ~15 | Backtest submission, data endpoints, strategy CRUD, health checks |
| **Other** | ~10 | Config loading, precision math, data quality, journal, WebSocket |

### Running Tests

```bash
pytest tests/ -v                    # run all 497 tests
pytest tests/test_birdeye.py -v     # run a specific test file
pytest tests/ -k "backtest" -v      # run tests matching a keyword
pytest tests/ -x                    # stop on first failure
```

All tests use **mocks for external APIs** — no network calls, no API keys needed, runs in ~5 seconds.

---

## CLI Reference

```
flint init                          # Download data + sample backtest
flint serve                         # Build UI + start API (single command)
flint serve --dev                   # Dev mode: API only (run UI separately)
flint backtest <strategy.py>        # Run backtest
flint optimize <strategy.py>        # Hyperparameter optimization
flint data download                 # Download/update market data
flint data status                   # Show data coverage
flint data provider status          # Show all providers and their status
flint data provider enable <name>   # Enable a provider (with --api-key)
flint data provider disable <name>  # Disable a provider
flint data exchanges                # List CCXT-supported exchanges
flint data markets <exchange>       # List markets on an exchange
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
├── providers/         # 13 providers: Drift, Birdeye, Helius, Pyth, Raydium, Orca, CCXT...
├── connectors/        # Drift (driftpy), Jupiter
├── analytics/         # Metrics, tearsheet, Monte Carlo, correlation
├── indicators.py      # 20 technical indicators
├── precision.py       # Fixed-point math for Solana
├── store.py           # Thread-safe DuckDB store (12 tables)
├── config.py          # Pydantic settings (YAML + env)
├── cli.py             # Typer CLI
├── api/               # FastAPI (30+ endpoints, WebSocket)
│   └── routes/        # backtest, data, paper, journal, optimization
├── notifications/     # Telegram, Discord, webhook
├── journal/           # Backtest run persistence
└── mev/               # Arb detection, liquidation scanning

ui/                    # React 19 + Vite + Tailwind
├── pages/             # Dashboard, Strategy Lab, Data Explorer, Docs, MEV
├── components/        # Charts, editors, metrics cards
└── hooks/             # useBacktest, useOptimize, useJournal

tests/                 # 497 tests across 44 test files
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
| CCXT exchanges | CCXT + Drift Protocol (Solana on-chain) |
| DataFrame-based | Candle objects + indicator functions |

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.9+, FastAPI, DuckDB, Optuna |
| Frontend | React 19, Vite, Tailwind CSS, Monaco Editor, lightweight-charts |
| Data | Drift, Birdeye, Helius, Pyth, Raydium, Orca, GeckoTerminal, CCXT |
| Funding | Drift, Hyperliquid, OKX, Bybit, Binance (5 venues + CCXT) |
| Execution | driftpy (Drift Protocol), Jupiter |
| CLI | Typer + Rich |
| Infra | Docker, WebSocket |

## Configuration

Flint uses `flint.yaml` + environment variables (`FLINT_` prefix) + `.env`:

```yaml
db:
  path: ./data/flint.duckdb

trading:
  default_capital: 10000
  default_fee_rate: 0.0005
  default_markets: ["SOL-PERP", "BTC-PERP", "ETH-PERP"]

providers:
  drift: { enabled: true }
  birdeye: { enabled: false }   # set FLINT_BIRDEYE_API_KEY in .env
  helius: { enabled: false }    # set FLINT_HELIUS_API_KEY in .env
  pyth: { enabled: false }
  ccxt: { enabled: false, exchange: binance }
  funding:
    drift: true
    hyperliquid: true
    okx: true

risk:
  max_drawdown_pct: 0.20
  max_open_trades: 5

api:
  host: 0.0.0.0
  port: 8000
```

See `.env.example` for all environment variable options.

## Development

```bash
pip install -e ".[dev]"          # install with dev dependencies
cd ui && npm install             # install UI dependencies
pytest tests/ -v                 # run 497 tests (~5s)
flint serve                      # API + UI on :8000 (builds UI automatically)
flint serve --dev                # dev mode: API on :8000, run `cd ui && npm run dev` for :5173
```

## License

MIT

---

<p align="center">
  <sub>Built for Solana. Powered by Drift Protocol.</sub>
</p>
