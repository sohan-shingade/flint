# Architecture Overview

Flint is a local-first algorithmic trading, backtesting, and MEV research platform for Solana. This document describes how the major subsystems connect, where data flows, and where to plug in extensions.

---

## 1. Directory Structure

```
flint/
  strategy/            # Strategy ABC, 20 built-in templates, AST-validated loader
  execution/           # ExecutionContext ABC and all concrete implementations
    context.py           # ExecutionContext ABC (market_order, limit_order, stop_order)
    backtest_context.py  # BacktestContext -- candle-replay fills via FillPipeline
    paper_broker.py      # PaperBroker -- same fill models, no on-chain transactions
    live_base.py         # LiveExecutionContext ABC
    drift_live.py        # LiveDriftContext -- driftpy SDK, Solana RPC
    hyperliquid_live.py  # LiveHyperliquidContext -- REST + EIP-712 signing
    ccxt_live.py         # LiveCCXTContext -- Binance, OKX, Bybit, any CCXT exchange
    multi_venue_live.py  # MultiVenueLiveContext -- routes by venue param
    fill_models.py       # FillModel ABC, ClosePriceFill, OrderbookFillModel, FillPipeline
    impact.py            # ImpactStage (4-tier model)
    latency.py           # LatencyStage
    partial_fill.py      # PartialFillStage
    order_tracker.py     # OrderTracker state machine (PENDING -> FILLED/CANCELLED/REJECTED)
    margin.py            # MarginEngine with per-venue configs
    capital.py           # VenueAllocator with per-venue balances
    venue_config.py      # VenueConfig presets (Drift, Hyperliquid, Binance, OKX, Bybit, dYdX, Jupiter)
    vamm.py              # VammCurve (constant-product vAMM fill model)
    tx_costs.py          # TxCostModel ABC + per-venue implementations
    fill_drift.py        # Drift-specific fill logic
    fill_hyperliquid.py  # Hyperliquid-specific fill logic
    fill_cex.py          # CEX-specific fill logic
    fill_jupiter.py      # Jupiter-specific fill logic
    fill_registry.py     # Fill model registry for venue dispatch
    synthetic_depth.py   # Synthetic orderbook generation
  backtest/            # Event-driven engine, parity test, calibration
    engine.py            # BacktestEngine
    parity.py            # ParityTest -- backtest vs paper engine comparison
    calibration.py       # CalibrationEngine (slippage calibration from live fills)
  regimes.py           # 8 market regime definitions (Dec 2023 - Apr 2026)
  optimization/        # Optuna optimizer, walk-forward validation
  paper/               # Paper trading engine + broker (ticks on live WebSocket feeds)
  risk/                # RiskGuard chain, EquityMonitor kill switch
    guards.py            # RiskManager + 6 guard types
    monitor.py           # EquityMonitor (live kill switch)
  portfolio/           # Multi-strategy engine, equal-weight + inverse-vol allocators
  providers/           # 14 data providers, 10 funding venues, WebSocket feeds
    registry.py          # DataProvider ABC, ProviderRegistry
    drift_candles.py     # DriftCandleProvider (48 markets)
    drift_s3.py          # DriftS3Provider (archival trade records)
    drift_api.py         # DriftDataProvider (funding, orderbook)
    funding_rates.py     # 10-venue funding rate collection
    birdeye.py           # Birdeye (any Solana token OHLCV)
    helius.py            # Helius (liquidations, whale tracking)
    pyth.py              # Pyth oracle prices (20 pairs)
    raydium.py           # Raydium AMM/CLMM pool data
    orca.py              # Orca Whirlpool pool data
    gecko.py             # GeckoTerminal DEX pool OHLCV
    jupiter.py           # Jupiter swap quotes
    coingecko.py         # CoinGecko spot candles
    ccxt_provider.py     # CCXT (100+ CEX exchanges, volume data)
    hyperliquid_candles.py  # HyperliquidCandleProvider
    websocket.py         # WebSocketFeed ABC
    drift_ws.py          # DriftWebSocketFeed
    hyperliquid_ws.py    # HyperliquidWebSocketFeed
    pyth_ws.py           # PythWebSocketFeed
    candle_aggregator.py # CandleAggregator (raw trades -> OHLCV bars)
    orca_ticks.py        # OrcaTickFetcher (CLMM tick data)
  connectors/          # Drift (driftpy), Hyperliquid (native REST + EIP-712)
  analytics/           # Metrics, tearsheet, Monte Carlo, correlation
  mev/                 # Arb detection, CLMM tick model
    clmm.py              # CLMMPool (concentrated liquidity modeling)
  indicators.py        # 20 technical indicators (sma, ema, rsi, macd, bollinger, atr, vwap, adx...)
  precision.py         # Fixed-point math for Solana (Decimal at boundaries, int on-chain)
  store.py             # Thread-safe DuckDB store -- single shared connection, 12 tables
  config.py            # Pydantic settings (flint.yaml + .env + FLINT_ env prefix)
  cli.py               # Typer CLI (8 commands)
  api/main.py          # FastAPI app (30+ endpoints, serves built UI from ui/dist/)
  models.py            # Shared dataclasses: Candle, Order, Fill, Signal, FundingRate, etc.
  mcp_server.py        # MCP server for AI integration (17 tools)

rust/                  # Rust backtesting engine (PyO3 bindings -> flint_core)
  src/lib.rs             # PyO3 Python bindings (RustEngine class)
  src/runner.rs          # Main backtest loop orchestration
  src/types.rs           # Shared type definitions
  src/engine/
    fills.rs             # Generic fill models (close, slippage, sqrt impact)
    venue_fills.rs       # Per-venue pipelines (Drift 3-tier, HL CLOB, Jupiter, 10 CEX)
    orders.rs            # Order processing with venue dispatch
    positions.rs         # Position state machine
    fees.rs              # Fee computation
    margin.rs            # Margin/liquidation engine
    capital.rs           # Multi-venue capital allocation
    venue_config.rs      # Per-venue fee/margin/latency configs
    synthetic_depth.rs   # Synthetic orderbook generation
    metrics.rs           # Performance metric computation

ui/                    # React 19 + Vite + Tailwind CSS
  src/pages/
    BacktestLab.tsx      # Strategy editor (Monaco), run + compare backtests
    PaperTrading.tsx     # Paper session management, parity test button
    DataExplorer.tsx     # Browse and download market data
    Dashboard.tsx        # Overview: equity curves, recent runs, system health
    LiveMonitor.tsx      # Live session monitoring, equity, positions, alerts
    MevDashboard.tsx     # Arb opportunity scanner, CLMM analysis
    FundingHeatmap.tsx   # Cross-venue funding rate heatmap
    FillAnalysis.tsx     # Fill quality analysis, slippage breakdown
    Docs.tsx             # Embedded documentation
    Setup.tsx            # Initial configuration and data download
  src/components/        # InteractiveChart (lightweight-charts v5), CodeEditor (Monaco)
  src/hooks/             # useBacktest, useStrategies, useOptimize, useJournal
```

---

## 2. Execution Hierarchy

`ExecutionContext` is the abstract base class that every strategy receives. It exposes a uniform order API regardless of whether the strategy is running in a backtest, paper session, or live on-chain. All order methods (`market_order`, `limit_order`, `stop_order`, `cancel`, `cancel_all`) accept an optional `venue` parameter. Strategies written against `ExecutionContext` deploy to any venue without modification.

```
ExecutionContext (ABC)
+-- BacktestContext              -- candle-replay, fills via FillPipeline
+-- PaperBroker                  -- same fill models, ticks on live WebSocket feeds
+-- LiveExecutionContext (ABC)
    +-- LiveDriftContext          -- driftpy SDK, Solana RPC
    +-- LiveHyperliquidContext    -- native REST + EIP-712 signing
    +-- LiveCCXTContext           -- Binance, OKX, Bybit, any CCXT exchange
    +-- MultiVenueLiveContext     -- wraps multiple venue contexts, routes by venue param
```

`BacktestContext` replays candle data and fills orders through the FillPipeline. `PaperBroker` uses the same fill models but ticks on real WebSocket candle closes rather than stored history. The three `Live*Context` classes submit real orders to their respective venues and poll for fill confirmations.

Each `LiveExecutionContext` subclass implements: `place_order`, `cancel_order`, `modify_order`, `get_positions`, `get_account`, `poll_fills`, and `sync_on_startup` (reconciles local state with venue state on restart).

---

## 3. Backtest Engine

`flint/backtest/engine.py` -- event-driven, single-threaded candle replay.

**Input formats:**

- `List[Candle]` for single-market backtests
- `Dict[str, List[Candle]]` for multi-market backtests (engine auto-detects `ctx.get_candles("MARKET")` calls in strategy code)

**Per-bar cycle:** feed candle(s) to strategy, collect orders, run FillPipeline, update positions and equity.

**Strategy versions:** v1 strategies return `Signal.BUY/SELL/HOLD`. v2 strategies use context-based orders (`ctx.market_order()`, `ctx.stop_order()`, `ctx.get_candles("BTC-PERP")`). Both are supported.

**Constraints:** 300-second timeout per backtest. Maximum 5 concurrent backtest runs.

**Multi-venue:** Position keys are `(venue, market)`. Margin and positions are isolated per venue. Each venue uses its own fill model and fee schedule.

### Rust engine (flint_core)

When the `flint_core` Rust package is installed, the backtest engine auto-dispatches to it for 10-50x faster execution. The Rust engine exposes the same `RustEngine` interface via PyO3 bindings and produces identical results -- verified by parity tests in `tests/test_rust_parity_benchmark.py`.

```bash
pip install maturin
cd rust && maturin develop
```

The Rust engine implements the full backtest loop in `rust/src/runner.rs`, including fill models, venue-specific pipelines, margin tracking, capital allocation, and metric computation. The Python engine remains the fallback when `flint_core` is not installed.

---

## 4. Fill Pipeline

The fill pipeline runs inside `BacktestContext` on every order. It is a chain of stages applied in sequence:

```
Order
  +-- LatencyStage          -- configurable execution latency (ms)
        +-- ImpactStage     -- fill price with market impact (4-tier)
              +-- PartialFillStage -- probabilistic partial fill based on volume
                    +-- Fill
```

### 4-tier impact model

ImpactStage selects the highest-fidelity model available for each order, per bar:

| Tier | Model | When used |
|------|-------|-----------|
| 0 | `VammCurve` (constant-product vAMM) | `vamm_enabled: true` and market has a configured vAMM |
| 1 | Orderbook walk (`OrderbookFillModel`) | L2 snapshot present in FlintStore for the bar |
| 2 | Sqrt participation impact | Volume data available, no orderbook snapshot |
| 3 | Flat bps fallback | No market depth data available |

Tier selection is per-order, per-bar. A strategy running on SOL-PERP with vAMM enabled uses Tier 0; if vAMM is disabled but an L2 snapshot was fetched, it falls to Tier 1 automatically.

### Per-venue fill configs

Venue-specific fill logic lives in dedicated modules (`fill_drift.py`, `fill_hyperliquid.py`, `fill_cex.py`, `fill_jupiter.py`). The `fill_registry.py` dispatches to the correct venue pipeline based on order routing. The Rust engine mirrors this with `venue_fills.rs` covering Drift 3-tier, Hyperliquid CLOB, Jupiter, and 10 CEX venues.

---

## 5. Margin Engine

`flint/execution/margin.py` -- `MarginEngine` with per-venue configs.

Position key is `(venue, market)`, so margin is isolated per venue. On each bar the engine recomputes unrealized PnL, checks maintenance margin, and triggers liquidation if the margin ratio drops below the maintenance threshold.

Venue configs define fees, initial margin, maintenance margin, leverage limits, and liquidation penalties. Presets are defined in `flint/execution/venue_config.py`:

| Venue | Taker fee | Maker fee | Max leverage | Base latency |
|-------|-----------|-----------|-------------|--------------|
| Drift | 10 bps | -2 bps (rebate) | 10x | 8.0s |
| Hyperliquid | 3.5 bps | 1 bps | 20x | 1.0s |
| Binance | 4.5 bps | 2 bps | 50x | 0.2s |
| OKX | 5 bps | 2 bps | 50x | 0.3s |
| Bybit | 5.5 bps | 2 bps | 50x | 0.3s |
| dYdX | 5 bps | 1 bps | 20x | 2.0s |
| Jupiter | 6 bps | 6 bps | 100x | 12.0s |

Enable in backtest requests with `margin_tracking: true`.

---

## 6. Capital Allocator

`flint/execution/capital.py` -- `VenueAllocator` with per-venue balances.

- Maintains separate cash accounts per venue, independent of unrealized PnL.
- Models transfer delays and costs between venues for cross-venue strategy backtests.
- `ctx.transfer(from_venue, to_venue, amount)` models inter-venue capital movement with configurable delays.
- `fragmentation_metrics()` reports capital utilization and idle cash per venue.

Enable in backtest requests with `capital_allocation: {"drift": 5000, "hyperliquid": 3000, "binance": 2000}`.

---

## 7. Regime System

`flint/regimes.py` defines 8 curated market regimes spanning Dec 2023 to Apr 2026, covering five regime types:

| Regime | Type | Period | Description |
|--------|------|--------|-------------|
| Pre-ETF Consolidation | sideways | Dec 2023 - Jan 2024 | Low volatility, awaiting ETF catalyst |
| ETF Bull Run | bull | Jan 2024 - May 2024 | BTC spot ETF approval drives +70% rally |
| Summer Correction | bear | Jun 2024 - Aug 2024 | Profit-taking, BTC -12% |
| Recovery Rally | bull | Sep 2024 - Dec 2024 | Re-acceleration to new ATHs |
| Peak & Distribution | high_vol | Jan 2025 - Mar 2025 | Topping pattern with funding spikes |
| Extended Decline | bear | Apr 2025 - Sep 2025 | Slow grind lower |
| Crash Phase 1 | crash | Oct 2025 - Dec 2025 | Accelerating sell-off, capitulation |
| Crash Phase 2 | crash | Jan 2026 - Apr 2026 | Continued decline, dead cat bounces |

Regimes are used in BacktestLab for multi-regime testing -- run a strategy across all regimes to evaluate robustness. The UI keeps a synced copy in `ui/src/constants/regimes.ts`.

---

## 8. FlintStore (DuckDB)

`flint/store.py` -- single shared DuckDB connection wrapped in a `threading.Lock`.

**Thread safety rule:** every method uses `with self._lock:`. Batched upserts are wrapped in `BEGIN TRANSACTION / COMMIT` for atomicity. Never open a second DuckDB connection or access `_conn`/`_lock` directly from outside the class.

### 12 tables

| Table | Primary key | Description |
|-------|------------|-------------|
| `candles` | `(venue, market, resolution_s, ts)` | OHLCV bars, per-venue |
| `venue_funding_rates` | venue + market + ts | Hourly funding rates by venue |
| `oracle_prices` | market + ts | Pyth oracle price snapshots |
| `orderbook_snapshots` | market + ts | L2 orderbook depth |
| `pool_snapshots` | pool_address + ts | Raydium/Orca AMM pool state |
| `open_interest` | market + ts | Long/short OI by market |
| `liquidations` | tx_sig | On-chain liquidation events |
| `whale_transfers` | tx_sig | Large wallet movements |
| `dex_volume` | market + ts | DEX trading volume |
| `token_unlocks` | token + ts | Token vesting unlock events |
| `tick_snapshots` | `(pool_address, ts)` | CLMM tick data for concentrated liquidity |
| `sync_metadata` | source + key | Last-fetched timestamps per source |

Live trading adds four more tables: `live_sessions`, `live_orders`, `live_fills`, `live_equity_history`.

---

## 9. Risk Guards

`flint/risk/guards.py` -- `RiskManager` chains 6 guard types. Every order must pass all guards before execution. The same guards apply in backtests, paper trading, and live trading.

| Guard | Behavior |
|-------|---------|
| `MaxPositionSize` | Rejects orders when cumulative USD notional on a market exceeds a cap |
| `MaxOpenPositions` | Rejects new entries when too many positions are open (does not block closes) |
| `MaxDrawdownCircuitBreaker` | Rejects all orders once cumulative drawdown exceeds threshold; latches until manually reset |
| `DailyLossLimit` | Rejects orders after daily loss exceeds USD threshold; auto-resets at day boundary |
| `MaxOrdersPerMinute` | Sliding-window rate limiter (default 30/min) |
| `PerMarketPositionLimit` | Hard per-market USD notional cap from a configured JSON map |

`EquityMonitor` (`flint/risk/monitor.py`) runs continuously in live sessions. When equity drops below `live_kill_switch_drawdown_pct` from peak, it cancels all open orders and closes all positions across all venues, then fires a critical alert. A warning alert fires at `live_drawdown_warning_pct`.

---

## 10. MCP Server

`flint/mcp_server.py` exposes 17 tools for AI model integration via the Model Context Protocol:

```bash
python -m flint.mcp_server                          # run standalone (stdio transport)
claude mcp add flint -- python -m flint.mcp_server   # add to Claude Code
```

**Tools:** `run_backtest`, `list_strategies`, `start_paper_trading`, `stop_paper_trading`, `get_paper_sessions`, `get_paper_status`, `list_journal_runs`, `optimize_strategy`, `get_candles`, `download_market_data`, `list_available_markets`, `list_local_markets`, `get_funding_rates`, `get_open_interest`, `get_correlation`, `get_data_freshness`, `get_provider_status`.

**Resources:** `flint://guide` (usage overview), `flint://markets` (market list).

All tools route through the same FlintStore and engine code paths used by the API and CLI.

---

## 11. React UI

10 pages built with React 19, Vite, and Tailwind CSS. Production build is served by FastAPI from `ui/dist/`. Development server runs at `localhost:5173` and proxies API calls to `localhost:8000`.

| Page | Purpose |
|------|---------|
| BacktestLab | Monaco code editor, run/compare backtests, regime selection, deploy to paper |
| PaperTrading | Paper session management, live equity, parity test button |
| DataExplorer | Browse markets, download data, check coverage and freshness |
| Dashboard | Overview of equity curves, recent runs, system health |
| LiveMonitor | Real-time equity, positions, trade log, alert history |
| MevDashboard | Arb opportunity scanner, CLMM pool analysis |
| FundingHeatmap | Cross-venue funding rate visualization |
| FillAnalysis | Fill quality analysis, slippage breakdown per venue |
| Docs | Embedded documentation |
| Setup | Initial configuration, data download, provider status |

Charts use lightweight-charts v5 (`InteractiveChart` component). The code editor uses Monaco (`CodeEditor` component). Data hooks (`useBacktest`, `useStrategies`, `useOptimize`, `useJournal`) manage API communication and polling.

---

## 12. Data Flow

End-to-end path from source to result:

```
External APIs / WebSockets
        |
        v
   Providers (14 REST + 3 WS feeds + 10 funding venues)
        |  fetch / stream
        v
   FlintStore (DuckDB, thread-safe)
        |  query
        v
   BacktestEngine / PaperBroker / LiveExecutionContext
        |  candles + funding
        v
   Strategy.on_candle()
        |  orders
        v
   RiskManager (6-guard chain)
        |  approved orders
        v
   FillPipeline (latency -> impact -> partial fill)
        |  fills
        v
   TxCostModel (per-venue network costs)
        |  costed fills
        v
   BacktestResult / live_fills table
```

For live sessions, the path from Providers to Strategy runs on every WebSocket candle close. Fills go to `live_fills` in FlintStore and trigger alert notifications.

For backtests, the Rust engine (when installed) replaces the Python BacktestEngine in this pipeline with identical logic running 10-50x faster.
