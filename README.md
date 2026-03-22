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
  <a href="#features"><img src="https://img.shields.io/badge/tests-497_passing-57c84d?style=flat-square&labelColor=141418" alt="497 tests"></a>
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

---

## Data Sources

Flint aggregates data from multiple sources into a local DuckDB database. **All core data is free** — no API keys required for backtesting.

### Built-in Providers

| Provider | Data | Auth | Coverage |
|---|---|---|---|
| **Drift Data API** | OHLCV candles (1m→monthly), funding rates, orderbook L2/L3 | None (free) | 48 markets, current data |
| **Drift S3** | Historical trade records (archival backfill) | None (free) | 90+ days of raw trades |
| **GeckoTerminal** | DEX pool OHLCV for any Solana pool | None (free) | Any Solana DEX pool |
| **Jupiter** | Swap quotes, routing, price discovery | None (free) | Any SPL token pair |
| **Hyperliquid** | Hourly funding rates | None (free) | 17 markets |
| **OKX** | 8h funding rates (normalized to 1h) | None (free) | Major markets |
| **Bybit** | 8h funding rates (normalized to 1h) | None (free) | Major markets |
| **Binance** | 8h funding rates (normalized to 1h) | None (free) | Major markets (US geo-blocked) |
| **Raydium** | AMM/CLMM pool data, reserves, fees, TVL | None (free) | Largest Solana DEX |
| **Orca** | Whirlpool CL positions, pool stats | None (free) | Second-largest Solana DEX |

### Optional Providers (API key required, all free tier)

| Provider | Data | Sign Up | What It Unlocks |
|---|---|---|---|
| **Birdeye** | OHLCV for *any* Solana token, token metadata, price history | [birdeye.so/developers](https://birdeye.so/developers) | Backtest any SPL token, not just Drift perps |
| **Helius** | Parsed transactions, token transfers, program events, DAS | [helius.dev](https://helius.dev) | On-chain events, whale tracking, liquidation data |
| **Pyth Network** | Real-time oracle feeds with confidence intervals | No key needed | Sub-second price updates for 100+ assets |

### CCXT — Any Exchange

Flint integrates with [CCXT](https://github.com/ccxt/ccxt) for access to **100+ centralized exchanges** through a unified API. This means you can pull candle data, funding rates, orderbooks, and tickers from Binance, Bybit, OKX, Coinbase, Kraken, KuCoin, and dozens more.

```bash
pip install flint[ccxt]          # install with CCXT support
flint data exchanges             # list supported exchanges
flint data markets binance       # list available markets on Binance
```

```yaml
# flint.yaml
providers:
  ccxt:
    enabled: true
    exchange: binance            # any CCXT-supported exchange
```

```python
# Use in strategies or scripts
from flint.providers import CCXTProvider

provider = CCXTProvider(exchange="bybit")
candles = provider.fetch_candles("SOL/USDT", 3600, start_ts, end_ts)
markets = provider.list_markets(quote="USDT")
```

Symbol mapping between Flint format (`SOL-PERP`) and exchange format (`SOL/USDT:USDT`) is handled automatically.

### Data Stored Locally

All data is cached in DuckDB (`./data/flint.duckdb`). No data leaves your machine.

| Table | Fields | Primary Key |
|---|---|---|
| `candles` | market, resolution, OHLCV | (market, resolution_s, ts) |
| `funding_rates` | market, rate, oracle/mark price, slot | (market, ts) |
| `oracle_prices` | market, price, slot | (market, ts) |
| `orderbook_snapshots` | market, bid/ask prices and sizes | (market, ts) |
| `venue_funding_rates` | venue, market, hourly rate, mark/index price | (venue, market, ts) |
| `pool_snapshots` | pool address, dex, reserves, fee rate | (pool_address, ts) |
| `open_interest` | market, long/short OI | (market, ts) |
| `liquidations` | market, side, size, price, tx_sig | (market, ts, tx_sig) |
| `whale_transfers` | wallet, token, amount, direction, tx_sig | (token_mint, ts, tx_sig) |
| `dex_volume` | market, dex, volume USD, txn count | (market, dex, ts) |
| `token_unlocks` | token, unlock time, amount | (token_mint, unlock_ts) |
| `sync_metadata` | provider, market, data type, last sync | (provider, market, data_type) |

### Cross-Venue Funding

Flint normalizes funding rates across 5 venues to a common hourly format, computes a benchmark (equal-weight average), and calculates dislocation scores — enabling cross-venue funding arbitrage strategies.

```
Drift (1h native) ──┐
Hyperliquid (1h)  ───┤
OKX (8h → 1h)    ───┼──→ Benchmark (avg) ──→ Dislocation z-score per venue
Bybit (8h → 1h)  ───┤
Binance (8h → 1h) ──┘
```

---

## Configuring Data Sources

Every data source is opt-in and configurable via `flint.yaml`, `.env`, or CLI. Enable only what you need.

### Via `flint.yaml`

```yaml
providers:
  drift:
    enabled: true       # always-on, no key needed
    candles: true
    funding_rates: true
    orderbook: true

  birdeye:
    enabled: true       # enable Birdeye for spot token data
    # api_key set via FLINT_BIRDEYE_API_KEY env var

  helius:
    enabled: true       # enable on-chain event tracking
    # api_key set via FLINT_HELIUS_API_KEY env var

  pyth:
    enabled: true       # real-time oracle feeds

  funding:
    drift: true
    hyperliquid: true
    okx: true
    binance: false      # geo-blocked from US
    bybit: false

dex:
  raydium:
    enabled: true       # track Raydium pool data
  orca:
    enabled: false
```

### Via `.env`

```bash
# API keys for optional providers
FLINT_BIRDEYE_API_KEY=your_key_here
FLINT_HELIUS_API_KEY=your_key_here
```

### Via CLI

```bash
# Enable a provider
flint data provider enable birdeye --api-key YOUR_KEY

# Disable a provider
flint data provider disable binance

# Check provider status
flint data provider status

# Download data from a specific provider
flint data download --provider birdeye --token SOL
flint data download --provider drift --market SOL-PERP

# List available markets per provider
flint data provider markets drift
flint data provider markets birdeye
```

### Adding Custom Providers

Implement the `DataProvider` interface and register in `flint.yaml`:

```python
from flint.providers.base import DataProvider

class MyExchangeProvider(DataProvider):
    name = "my-exchange"

    def fetch_candles(self, market, resolution_s, start_ts, end_ts):
        # Your API calls here
        return [Candle(...), ...]

    def fetch_funding_rates(self, market, start_ts, end_ts):
        return [FundingRate(...), ...]
```

```yaml
providers:
  custom:
    - module: my_providers.exchange
      class: MyExchangeProvider
      enabled: true
      config:
        api_key: ${MY_EXCHANGE_KEY}
```

## CLI Reference

```
flint init                          # Download data + sample backtest
flint serve                         # Start API + UI
flint backtest <strategy.py>        # Run backtest
flint optimize <strategy.py>        # Hyperparameter optimization
flint data download                 # Download/update market data
flint data status                   # Show data coverage
flint data provider status          # Show all data providers and their status
flint data provider enable birdeye  # Enable a provider (with --api-key)
flint data provider disable binance # Disable a provider
flint data exchanges                # List CCXT-supported exchanges
flint data markets binance          # List markets on an exchange
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
├── providers/         # Drift, Birdeye, Helius, Pyth, Raydium, Orca, CCXT
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
| CCXT (CEX only) | Drift Protocol (Solana on-chain) |
| DataFrame-based | Candle objects + indicator functions |

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.9+, FastAPI, DuckDB, Optuna |
| Frontend | React 19, Vite, Tailwind CSS, Monaco Editor, lightweight-charts |
| Data | Drift Data API, Drift S3, GeckoTerminal, Birdeye, Pyth, CCXT (100+ exchanges), Parquet |
| Funding | Drift, Hyperliquid, OKX, Bybit, Binance (5 venues + any via CCXT) |
| DEX Data | Raydium (AMM/CLMM), Orca (Whirlpools), Jupiter (swaps) |
| Execution | driftpy (Drift Protocol), Jupiter |
| CLI | Typer + Rich |
| Infra | Docker, WebSocket |

## Roadmap

### Next Up

| Feature | Category | Description |
|---|---|---|
| **Spot token backtesting** | Breadth | Backtest strategies on any Solana SPL token via Birdeye data |
| **LP strategy framework** | Breadth | Simulate concentrated liquidity on Raydium/Orca (IL, fee yields) |
| **Raydium + Orca pool data** | Breadth | AMM reserves, CLMM positions, LP fee tracking |
| **Open interest tracking** | Depth | Long/short OI from Drift for crowding and divergence signals |
| **Liquidation detection** | Depth | Parse Drift program events via Helius for cascade detection |
| **Whale wallet tracking** | Depth | Monitor large token movements for smart money signals |
| **Perp-spot basis engine** | Depth | Automated basis tracking between Drift perps and spot prices |
| **Pyth WebSocket streaming** | Operational | Sub-second oracle updates for paper/live trading |
| **Tick-level Drift data** | Operational | Fill-level trade records for volume profile analysis |
| **Incremental sync** | Operational | Track `last_sync_ts` per source, only fetch deltas |
| **Data freshness dashboard** | Operational | UI showing staleness per market/source |
| **Cross-market correlation** | Operational | Rolling correlation matrix for portfolio construction |
| **Adaptive resolution** | Operational | Auto-select candle resolution based on backtest length |
| **Liquidation heatmap** | UI | Visualize liquidation clusters relative to current price |
| **Multi-venue execution** | Trading | Route orders across Drift + Jupiter for best execution |
| **Strategy marketplace** | Community | Share/import strategies with performance badges |

### Possible Future Integrations

| Integration | Type | What It Enables |
|---|---|---|
| **Marinade Finance** | Staking | mSOL yield data for basis strategies |
| **Jito** | MEV | Tip data, bundle analysis, priority fee markets |
| **Tensor** | NFT | NFT floor price feeds for exotic strategies |
| **Switchboard** | Oracle | Alternative oracle feeds, VRF |
| **Kamino** | DeFi | Vault yields, auto-compounding data |
| **Marginfi** | Lending | Borrow rates, utilization for carry trades |
| **Token unlock schedules** | On-chain | Vesting account monitoring for supply pressure |

Want to contribute a provider or feature? See [Adding Custom Providers](#adding-custom-providers) above.

---

## Configuration

Flint uses `flint.yaml` + environment variables (`FLINT_` prefix) + `.env` file:

```yaml
db:
  path: ./data/flint.duckdb

trading:
  default_capital: 10000
  default_fee_rate: 0.0005
  default_markets: ["SOL-PERP", "BTC-PERP", "ETH-PERP"]

collector:
  enabled: true
  candle_backfill_days: 90

providers:
  drift:
    enabled: true
  birdeye:
    enabled: false    # set FLINT_BIRDEYE_API_KEY to enable
  helius:
    enabled: false    # set FLINT_HELIUS_API_KEY to enable
  pyth:
    enabled: false
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
# Setup
pip install -e ".[dev]"
cd ui && npm install

# Run tests
pytest tests/ -v          # 497 tests

# Dev servers
flint serve               # API on :8000, UI on :5173
```

## License

MIT

---

<p align="center">
  <sub>Built for Solana. Powered by Drift Protocol.</sub>
</p>
