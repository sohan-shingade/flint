# Flint — AI Development Guide

Flint is a local-first algorithmic trading, backtesting, and MEV research platform for Solana.

## Quick Reference

```bash
pip install -e .              # install
flint init                    # download data + sample backtest
flint serve                   # API + UI at localhost:8000
flint serve --dev             # dev mode: API only, run UI separately
pytest tests/ -v              # 497 tests (~5s, all mocked)
cd ui && npm run dev          # dev UI at localhost:5173 (proxies API)
```

## Architecture

```
flint/
  strategy/        # Strategy ABC, 10 templates, loader (strategies/user/ for user code)
  execution/       # ExecutionContext ABC, BacktestContext, fill/fee models
  backtest/        # Event-driven engine, accepts List[Candle] or Dict[str, List[Candle]]
  optimization/    # Optuna optimizer, walk-forward
  paper/           # Paper trading engine + broker
  risk/            # Risk guards (max drawdown, position limits, daily loss)
  portfolio/       # Multi-strategy engine, allocators
  providers/       # 13 data providers (registry.py manages enable/disable)
  connectors/      # Drift (driftpy), Jupiter
  analytics/       # Metrics, tearsheet, Monte Carlo, correlation
  indicators.py    # 20 technical indicators (sma, ema, rsi, macd, bollinger, atr, vwap, adx...)
  precision.py     # Fixed-point math for Solana (Decimal at boundaries, int on-chain)
  store.py         # Thread-safe DuckDB store (12 tables, all ops use threading.Lock)
  config.py        # Pydantic settings (flint.yaml + .env + FLINT_ env prefix)
  cli.py           # Typer CLI
  api/main.py      # FastAPI app (30+ endpoints, serves built UI from ui/dist/)
  models.py        # All dataclasses: Candle, FundingRate, Signal, Order, Fill, etc.

ui/                # React 19 + Vite + Tailwind
  pages/           # Dashboard, BacktestLab, DataExplorer, Docs, MevDashboard
  components/      # InteractiveChart (lightweight-charts v5), CodeEditor (Monaco), etc.
  hooks/           # useBacktest, useStrategies, useOptimize, useJournal
```

## Key Patterns

- **Thread safety**: Every `FlintStore` method wraps `self._conn.execute()` in `with self._lock:`
- **Provider pattern**: Inherit `DataProvider` (registry.py), implement `is_available()` + `supported_data_types()`. Register via `flint.yaml` providers section.
- **Strategy API**: v1 returns `Signal.BUY/SELL/HOLD`, v2 uses `ctx.market_order()`, `ctx.stop_order()`, `ctx.get_candles("BTC-PERP")`
- **Multi-market**: Engine accepts `Dict[str, List[Candle]]`. UI auto-detects `ctx.get_candles("MARKET")` calls in strategy code.
- **Config loading**: `load_config()` merges flint.yaml + .env + env vars (FLINT_ prefix). YAML keys flatten: `db.path` → `db_path`.

## Data Providers (13 total)

| Provider | File | Auth | Data |
|---|---|---|---|
| Drift Data API | providers/drift_candles.py | None | OHLCV candles (48 markets) |
| Drift S3 | providers/drift_s3.py | None | Historical trade records |
| Drift OI | providers/open_interest.py | None | Open interest |
| Drift Funding | providers/drift_api.py | None | Funding rates, orderbook |
| Birdeye | providers/birdeye.py | FLINT_BIRDEYE_API_KEY | Any Solana token OHLCV |
| Helius | providers/helius.py | FLINT_HELIUS_API_KEY | Liquidations, whale tracking |
| Pyth | providers/pyth.py | None | Oracle prices (20 pairs) |
| Raydium | providers/raydium.py | None | AMM/CLMM pool data |
| Orca | providers/orca.py | None | Whirlpool pool data |
| GeckoTerminal | providers/gecko.py | None | DEX pool OHLCV |
| Jupiter | providers/jupiter.py | None | Swap quotes |
| CCXT | providers/ccxt_provider.py | Optional | 100+ CEX exchanges |
| Cross-venue funding | providers/funding_rates.py | None | 5 venues normalized to 1h |

## DuckDB Tables (12)

candles, funding_rates, oracle_prices, orderbook_snapshots, venue_funding_rates, pool_snapshots, open_interest, liquidations, whale_transfers, dex_volume, token_unlocks, sync_metadata

## API Endpoints (key ones)

```
POST /api/v1/backtest/run          # Submit backtest (market, markets[], code, dates, capital, fee_rate)
GET  /api/v1/backtest/{id}/results # Poll for results
GET  /api/v1/data/ohlcv            # Query candles
GET  /api/v1/data/markets          # List markets in DB
GET  /api/v1/data/available-markets # List all downloadable markets
POST /api/v1/data/download         # Download market data (market, resolution_s, start_ts, end_ts)
GET  /api/v1/data/providers        # Provider status
GET  /api/v1/strategies            # List built-in strategies
GET  /api/v1/user-strategies       # List user strategies (from strategies/user/)
POST /api/v1/user-strategies       # Save user strategy {name, code}
POST /api/v1/optimize/run          # Run Optuna optimization
GET  /api/v1/health                # Health check
```

## Testing

All tests use mocks — no network calls, no API keys needed. Run from project root:
```bash
pytest tests/ -v              # all 497 tests
pytest tests/ -k "backtest"   # keyword filter
pytest tests/test_birdeye.py  # single file
```

## Common Tasks

**Add a new data provider**: Create `flint/providers/my_provider.py`, inherit `DataProvider`, add to `__init__.py`, add config in `flint.yaml`.

**Add a new API endpoint**: Add to the relevant file in `flint/api/routes/`. Register router in `flint/api/main.py` if new file.

**Add a new strategy template**: Create in `flint/strategy/`, add to builders dict in `flint/api/routes/backtest.py`.

**Modify the UI**: Edit files in `ui/src/`. Run `cd ui && npm run dev` for hot reload. Production build: `npm run build` → served by FastAPI from `ui/dist/`.

## MCP Server

Flint exposes an MCP server at `flint/mcp_server.py` for AI model integration.

```bash
pip install flint[mcp]                              # install MCP dependency
python -m flint.mcp_server                          # run standalone (stdio transport)
claude mcp add flint -- python -m flint.mcp_server  # add to Claude Code
```

Tools: `run_backtest`, `optimize_strategy`, `get_candles`, `download_market_data`, `list_available_markets`, `list_local_markets`, `list_strategies`, `get_funding_rates`, `get_open_interest`, `get_correlation`, `get_data_freshness`

Resources: `flint://guide` (usage overview), `flint://markets` (market list)

## Don'ts

- Don't create a new DuckDB connection — always use the shared `FlintStore` from `app.state.store`
- Don't skip `with self._lock:` in store methods — DuckDB is not thread-safe
- Don't use `git push --force` on main — branch ruleset prevents it
- Don't commit `.env` files — they contain API keys
- Don't put personal strategies in `strategies/user/` in git — they're gitignored
