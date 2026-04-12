# Flint — AI Development Guide

Flint is a local-first algorithmic trading, backtesting, and MEV research platform for Solana.

## Quick Reference

```bash
pip install -e .              # install
flint init                    # download data + sample backtest
flint serve                   # API + UI at localhost:8000
flint serve --dev             # dev mode: API only, run UI separately
pytest tests/ -v              # 1545 tests (~7min, all mocked)
cd ui && npm run dev          # dev UI at localhost:5173 (proxies API)

# Rust engine (optional — 10-50x faster backtests)
pip install maturin           # install build tool
cd rust && maturin develop    # build + install flint_core
pytest tests/test_rust_parity_benchmark.py -v -s  # verify + benchmark
```

## Architecture

```
flint/
  strategy/        # Strategy ABC, 15 templates, loader (strategies/user/ for user code)
  execution/       # ExecutionContext ABC, BacktestContext, fill/fee models, margin, capital
  backtest/        # Event-driven engine, accepts List[Candle] or Dict[str, List[Candle]]
                   # Auto-dispatches to Rust engine (flint_core) when installed
  regimes.py       # 8 market regime definitions for multi-regime backtesting
  optimization/    # Optuna optimizer, walk-forward
  paper/           # Paper trading engine + broker
  risk/            # Risk guards (max drawdown, position limits, daily loss)
  portfolio/       # Multi-strategy engine, allocators (equal-weight, inverse-vol)
  providers/       # 14 data providers + 10 funding venues (registry.py manages enable/disable)
  connectors/      # Drift (driftpy), Jupiter
  analytics/       # Metrics, tearsheet, Monte Carlo, correlation
  indicators.py    # 20 technical indicators (sma, ema, rsi, macd, bollinger, atr, vwap, adx...)
  precision.py     # Fixed-point math for Solana (Decimal at boundaries, int on-chain)
  store.py         # Thread-safe DuckDB store (12 tables, all ops use threading.Lock + transactions)
  config.py        # Pydantic settings (flint.yaml + .env + FLINT_ env prefix)
  cli.py           # Typer CLI (8 commands)
  api/main.py      # FastAPI app (30+ endpoints, serves built UI from ui/dist/)

rust/                # Rust backtesting engine (PyO3 → flint_core)
  src/runner.rs      # Main backtest loop orchestration
  src/lib.rs         # PyO3 Python bindings (RustEngine class)
  src/engine/
    fills.rs         # Generic fill models (close, slippage, sqrt impact)
    venue_fills.rs   # Per-venue pipelines (Drift 3-tier, HL CLOB, Jupiter, 10 CEX)
    orders.rs        # Order processing with venue dispatch
    positions.rs     # Position state machine
    fees.rs          # Fee computation
    margin.rs        # Margin/liquidation engine
    capital.rs       # Multi-venue capital allocation
    venue_config.rs  # Per-venue fee/margin/latency configs
    synthetic_depth.rs # Synthetic orderbook generation
  models.py        # All dataclasses: Candle, FundingRate, Signal, Order, Fill, etc.

ui/                # React 19 + Vite + Tailwind
  pages/           # Dashboard, BacktestLab, DataExplorer, Docs, MevDashboard
  components/      # InteractiveChart (lightweight-charts v5), CodeEditor (Monaco), etc.
  hooks/           # useBacktest, useStrategies, useOptimize, useJournal
```

## Key Patterns

- **Thread safety**: Every `FlintStore` method wraps `self._conn.execute()` in `with self._lock:`. Batched upserts use `BEGIN TRANSACTION` / `COMMIT` for atomicity.
- **Provider pattern**: Inherit `DataProvider` (registry.py), implement `is_available()` + `supported_data_types()`. Register via `flint.yaml` providers section.
- **Strategy API**: v1 returns `Signal.BUY/SELL/HOLD`, v2 uses `ctx.market_order()`, `ctx.stop_order()`, `ctx.get_candles("BTC-PERP")`. All 15 built-in strategies support Optuna optimization via `parameters()`.
- **Multi-market**: Engine accepts `Dict[str, List[Candle]]`. UI auto-detects `ctx.get_candles("MARKET")` calls in strategy code.
- **Config loading**: `load_config()` merges flint.yaml + .env + env vars (FLINT_ prefix). YAML keys flatten: `db.path` → `db_path`.
- **Security**: API binds to `127.0.0.1` by default. Strategy loader blocks non-approved imports and dangerous builtins via AST validation. Backtest engine has a 300s timeout. Max 5 concurrent backtests.

## Data Providers (14 + 10 funding venues)

| Provider | File | Auth | Data |
|---|---|---|---|
| Drift Data API | providers/drift_candles.py | None | OHLCV candles (48 markets) |
| Drift S3 | providers/drift_s3.py | None | Historical trade records, funding |
| Drift OI | providers/open_interest.py | None | Open interest |
| Drift Funding | providers/drift_api.py | None | Funding rates, L2/L3 orderbook |
| Birdeye | providers/birdeye.py | FLINT_BIRDEYE_API_KEY | Any Solana token OHLCV |
| Helius | providers/helius.py | FLINT_HELIUS_API_KEY | Liquidations, whale tracking |
| Pyth | providers/pyth.py | None | Oracle prices (20 pairs) |
| Raydium | providers/raydium.py | None | AMM/CLMM pool data |
| Orca | providers/orca.py | None | Whirlpool pool data |
| GeckoTerminal | providers/gecko.py | None | DEX pool OHLCV |
| Jupiter | providers/jupiter.py | None | Swap quotes |
| CoinGecko | providers/coingecko.py | None | Spot candles (BTC, ETH, etc.) |
| CCXT | providers/ccxt_provider.py | None (bundled) | 100+ CEX exchanges, volume data |
| Cross-venue funding | providers/funding_rates.py | None | 10 venues normalized to 1h |

**Funding venues** (all free, no keys): Drift, Binance, Hyperliquid, OKX, Bybit, Gate.io, Bitget, dYdX + CCXT (mexc, phemex, bitmex).

**Volume venues** (auto-downloaded per-venue): Hyperliquid, OKX, Coinbase, Gate.io, Binance US + Jupiter (Helius tx proxy).

### Jupiter Perps Data Limitations

Jupiter Perps has **no historical borrow rate or volume API**. Historical backfill is not currently available:

- **Borrow rates**: Jupiter's `perps-api.jup.ag` provides current rates only. The `RpcBorrowBackfill` needs the Anchor IDL to deserialize custody account bytes — not yet implemented. Forward collection via `JupiterBorrowCollector` works but only accumulates going forward.
- **Volume**: Approximated from Helius Enhanced Transaction USDC transfers (collateral proxy, not notional). Limited to recent data on free tier.
- **No historical OHLCV**: Jupiter Perps has no candle endpoint. Use Pyth oracle prices instead.

Strategies that depend on Jupiter Perps borrow rate history or volume should not be backtested beyond the data that has been forward-collected.

### Orca / Raydium Data

Orca and Raydium are **spot DEXes** — they have no funding rates (that's a perps concept). Available data:

- **Current pool data**: TVL, reserves, fee rates — via native APIs
- **Historical OHLCV + volume**: Via GeckoTerminal (free, no key) for any Solana pool
- **Tick-level liquidity**: Orca Whirlpools via `OrcaTickFetcher` (on-chain RPC)

## v0.3 Execution Features (dev branch)

| Feature | Files | What |
|---|---|---|
| Orderbook fills | `execution/fill_models.py` | `OrderbookFillModel` walks L2 book for volume-weighted fill prices |
| Multi-venue positions | `execution/backtest_context.py` | Position key is `(venue, market)`, venue param on all order methods |
| Margin / liquidation | `execution/margin.py` | `MarginEngine` with per-venue configs, liquidation detection per bar |
| Capital allocation | `execution/capital.py` | `VenueAllocator` with per-venue balances, transfer delays/costs |
| Venue configs | `execution/venue_config.py` | Fee/margin/leverage presets for Drift, Hyperliquid, Binance, OKX, Bybit, dYdX |

Enable in backtest requests with `margin_tracking: true` and/or `capital_allocation: {"drift": 5000, "hyperliquid": 3000}`.

## DuckDB Tables (12)

candles, venue_funding_rates, oracle_prices, orderbook_snapshots, pool_snapshots, open_interest, liquidations, whale_transfers, dex_volume, token_unlocks, sync_metadata

## API Endpoints (key ones)

```
POST /api/v1/backtest/run          # Submit backtest (market, markets[], code, dates, capital, fee_rate, margin_tracking, capital_allocation)
GET  /api/v1/backtest/{id}/results # Poll for results + progress
GET  /api/v1/backtest/compare      # Compare multiple runs
GET  /api/v1/data/ohlcv            # Query candles
GET  /api/v1/data/markets          # List markets in DB
GET  /api/v1/data/available-markets # List all downloadable markets
POST /api/v1/data/download         # Download market data + funding from all venues
GET  /api/v1/data/check            # Check data coverage for a date range
GET  /api/v1/data/funding          # Funding rates by venue
GET  /api/v1/data/freshness        # Data freshness report
GET  /api/v1/data/correlation      # Cross-market correlation matrix
GET  /api/v1/data/providers        # Provider status
GET  /api/v1/strategies            # List built-in strategies
POST /api/v1/user-strategies       # Save user strategy {name, code}
POST /api/v1/optimize/run          # Run Optuna optimization (1-500 trials)
POST /api/v1/paper/start           # Start paper trading session
GET  /api/v1/journal/runs          # List past backtest runs
POST /api/v1/mev/scan/arb          # Scan for arbitrage routes
GET  /api/v1/health                # Health check
```

## Testing

All tests use mocks — no network calls, no API keys needed. Run from project root:
```bash
pytest tests/ -v              # all 536 tests
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
- Don't access `store._conn` or `store._lock` directly from API routes — add a method to `FlintStore` instead
- Don't use `git push --force` on main — branch ruleset prevents it
- Don't commit `.env` files — they contain API keys
- Don't put personal strategies in `strategies/user/` in git — they're gitignored
- Don't use non-approved imports in user strategies — the AST validator blocks them (only flint, numpy, math, statistics, collections, dataclasses, typing, enum, abc, functools, itertools, operator are allowed)
