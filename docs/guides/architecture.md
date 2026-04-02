# Architecture Overview

Flint is a local-first algorithmic trading and backtesting platform for Solana. This document describes how the major subsystems connect, where data flows, and where to plug in extensions.

---

## 1. Directory Structure

```
flint/
  strategy/        # Strategy ABC, 15 built-in templates, AST-validated loader
  execution/       # ExecutionContext ABC and all concrete implementations
  backtest/        # Event-driven engine, parity test, slippage calibration
  optimization/    # Optuna optimizer, walk-forward validation
  paper/           # Paper trading engine + broker (ticks on live feeds)
  risk/            # RiskGuard chain, EquityMonitor kill switch
  portfolio/       # Multi-strategy engine, equal-weight + inverse-vol allocators
  providers/       # 14 data providers, 10 funding venues, WebSocket feeds
  connectors/      # Drift (driftpy), Hyperliquid (native REST + EIP-712)
  analytics/       # Metrics, tearsheet, Monte Carlo, correlation
  mev/             # Arb detection, CLMM tick model
  indicators.py    # 20 technical indicators
  precision.py     # Fixed-point math for Solana (Decimal at boundaries)
  store.py         # Thread-safe DuckDB store — single shared connection
  config.py        # Pydantic settings (flint.yaml + .env + FLINT_ prefix)
  cli.py           # Typer CLI (8 commands)
  api/main.py      # FastAPI app (30+ endpoints, serves built UI)
  models.py        # Shared dataclasses: Candle, Order, Fill, Signal, etc.

ui/                # React 19 + Vite + Tailwind CSS
  pages/           # Dashboard, BacktestLab, DataExplorer, Docs, MevDashboard
  components/      # InteractiveChart (lightweight-charts v5), CodeEditor (Monaco)
  hooks/           # useBacktest, useStrategies, useOptimize, useJournal
```

---

## 2. Core Components

### ExecutionContext Hierarchy

`ExecutionContext` is the abstract base class that every strategy receives. It exposes a uniform order API regardless of whether the strategy is running in a backtest, paper session, or live on-chain.

```
ExecutionContext (ABC)
├── BacktestContext          — candle-replay, fills via FillPipeline
├── PaperBroker              — same fill models, no on-chain tx
└── LiveExecutionContext (ABC)
    ├── LiveDriftContext     — driftpy SDK, Solana RPC
    ├── LiveHyperliquidContext — native REST + EIP-712 signing
    └── MultiVenueLiveContext — wraps multiple venue contexts, routes by venue param
```

All order methods (`market_order`, `limit_order`, `stop_order`, `cancel`, `cancel_all`) accept an optional `venue` parameter. Strategies written against `ExecutionContext` deploy to any venue without modification.

### BacktestEngine

`flint/backtest/engine.py` — event-driven, single-threaded candle replay.

- Accepts `List[Candle]` (single market) or `Dict[str, List[Candle]]` (multi-market).
- On each bar: feeds candle(s) to strategy → collects orders → runs `FillPipeline` → updates positions and equity.
- Supports v1 strategies (`Signal.BUY/SELL/HOLD`) and v2 strategies (`ctx.market_order()`).
- Enforces a 300-second timeout. Max 5 concurrent backtest runs.
- `venue:market` composite keys enable multi-venue backtesting with per-venue fill models.

### FlintStore

`flint/store.py` — single shared DuckDB connection wrapped in a `threading.Lock`.

**Thread safety rule**: every method uses `with self._lock:`. Batched upserts are wrapped in `BEGIN TRANSACTION / COMMIT` for atomicity. Never open a second DuckDB connection or access `_conn`/`_lock` directly from outside the class.

12 tables: `candles`, `venue_funding_rates`, `oracle_prices`, `orderbook_snapshots`, `pool_snapshots`, `open_interest`, `liquidations`, `whale_transfers`, `dex_volume`, `token_unlocks`, `sync_metadata`, plus live trading tables (`live_sessions`, `live_orders`, `live_fills`, `live_equity_history`).

---

## 3. Fill Pipeline

The fill pipeline runs inside `BacktestContext` on every order. It is a chain of stages, each applied in sequence:

```
Order
  └── LatencyStage          — applies configurable execution latency (ms)
        └── ImpactStage     — computes fill price with market impact (4-tier, see below)
              └── PartialFillStage — probabilistic partial fill based on volume
                    └── Fill
```

### 4-Tier Impact Model (ImpactStage)

ImpactStage selects the highest-fidelity model available for each order:

| Tier | Model | When used |
|------|-------|-----------|
| 0 | `VammCurve` (vAMM) | `vamm_enabled: true` and market has a configured vAMM |
| 1 | Orderbook walk | L2 snapshot present in FlintStore for the bar |
| 2 | Sqrt participation | Volume data available, no orderbook snapshot |
| 3 | Flat bps fallback | No market data available |

For full details on the math behind each tier, see [slippage-models.md](slippage-models.md).

---

## 4. Margin Engine

`flint/execution/margin.py` — `MarginEngine` with per-venue configs.

- Position key is `(venue, market)` — margin is isolated per venue.
- On each bar: recomputes unrealized PnL, checks maintenance margin, triggers liquidation if breached.
- Venue configs (fees, initial margin, maintenance margin, leverage limits) live in `flint/execution/venue_config.py` with presets for Drift, Hyperliquid, Binance, OKX, Bybit, and dYdX.
- Enable in backtest: `margin_tracking: true` in the request body.

---

## 5. Capital Allocator

`flint/execution/capital.py` — `VenueAllocator` with per-venue balances.

- Per-venue cash accounts, separate from unrealized PnL.
- Models transfer delays and costs between venues (useful for cross-venue strategy backtests).
- Enable via `capital_allocation: {"drift": 5000, "hyperliquid": 3000}` in the backtest request.
- `fragmentation_metrics()` reports capital utilization and idle cash per venue.

---

## 6. WebSocket Feeds

All live data arrives through a hierarchy of WebSocket feed classes:

```
WebSocketFeed (ABC)           — reconnection logic, health checks, REST fallback
  ├── DriftWebSocketFeed      — trade streaming + funding rate subscription
  ├── HyperliquidWebSocketFeed — candle, L2 book, orderUpdates channels
  └── PythWebSocketFeed       — sub-second oracle prices (Hermes)
```

`WebSocketFeed` base class behavior:
- Reconnection with exponential backoff (1s → 2s → 4s … max 60s).
- Health check: forces reconnect if no message received for 30 seconds.
- On reconnect: backfills missed candles from the REST provider.

`CandleAggregator` (`flint/providers/candle_aggregator.py`) converts raw trade events from WebSocket streams into OHLCV candle bars.

Paper trading uses the same WebSocket feeds as live trading — it ticks on real candle closes rather than stored historical candles.

---

## 7. Risk Guards

`flint/risk/guards.py` — `RiskManager` runs a chain of guards before each order is submitted.

| Guard | Config key | Behavior |
|-------|-----------|---------|
| `MaxPositionSize` | `live_per_market_position_limits` | Rejects orders exceeding USD notional cap |
| `MaxOpenPositions` | `max_open_positions` | Rejects new entries when too many positions open |
| `MaxDrawdownCircuitBreaker` | `max_drawdown_pct` | Rejects all orders after cumulative drawdown |
| `DailyLossLimit` | `daily_loss_limit_usd` | Rejects orders after daily loss exceeds threshold |
| `MaxOrdersPerMinute` | `live_max_orders_per_minute` | Sliding window rate limiter |

`EquityMonitor` (`flint/risk/monitor.py`) runs continuously in live sessions. When equity drops below `live_kill_switch_drawdown_pct` from peak, it cancels all open orders and closes all positions immediately, then fires a critical alert. A warning alert fires at `live_drawdown_warning_pct`.

---

## 8. Data Flow

End-to-end data path from source to backtest result:

```
External APIs / WebSockets
        │
        ▼
   Providers (14 REST + WS feeds)
        │  fetch / stream
        ▼
   FlintStore (DuckDB, thread-safe)
        │  query
        ▼
   BacktestEngine / LiveExecutionContext
        │  candles + funding
        ▼
   Strategy.on_bar() / Strategy.generate()
        │  orders
        ▼
   RiskManager (guard chain)
        │  approved orders
        ▼
   FillPipeline (latency → impact → partial fill)
        │  fills
        ▼
   BacktestResult / live_fills table
```

For live sessions, the path from Providers to Strategy runs on every WebSocket candle close. Fills go to `live_fills` in FlintStore and trigger alert notifications.

---

## 9. Extension Points

### New venue (live execution)

1. Create `flint/execution/my_venue_live.py`, subclass `LiveExecutionContext`.
2. Implement the 7 abstract methods: `place_order`, `cancel_order`, `modify_order`, `get_positions`, `get_account`, `poll_fills`, `sync_on_startup`.
3. Add a `VenueConfig` preset in `flint/execution/venue_config.py`.
4. Register in `MultiVenueLiveContext` venue routing.

### New data provider

1. Create `flint/providers/my_provider.py`, subclass `DataProvider`.
2. Implement `is_available()` and `supported_data_types()`.
3. Add to `flint/providers/__init__.py`.
4. Register in `flint.yaml` providers section.

### New strategy

1. Create `flint/strategy/my_strategy.py`, subclass `Strategy`.
2. Implement `generate()` (v1) or `on_bar()` (v2 with `ExecutionContext`).
3. Optionally implement `parameters()` for Optuna optimization.
4. Register in the builders dict in `flint/api/routes/backtest.py`.

### New fill model

1. Create a class that implements the `FillModel` interface.
2. Add a new `Stage` subclass in `flint/execution/fill_models.py`.
3. Insert into `FillPipeline` construction in `BacktestContext.__init__`.
