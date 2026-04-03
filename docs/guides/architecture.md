# Architecture Overview

Flint is a local-first algorithmic trading, backtesting, and MEV research platform for Solana. This document describes how the major subsystems connect, where data flows, and where to plug in extensions.

---

## 1. Directory Structure

```
flint/
  strategy/        # Strategy ABC, 20 built-in templates, AST-validated loader
  execution/       # ExecutionContext ABC and all concrete implementations
    context.py       # ExecutionContext ABC
    backtest_context.py  # BacktestContext (candle-replay fills)
    live_base.py     # LiveExecutionContext ABC
    drift_live.py    # LiveDriftContext (driftpy SDK)
    hyperliquid_live.py  # LiveHyperliquidContext (REST + EIP-712)
    multi_venue_live.py  # MultiVenueLiveContext (routes by venue)
    live_context.py  # LiveContext (legacy)
    fill_models.py   # FillModel ABC, ClosePriceFill, OrderbookFillModel, FillPipeline
    impact.py        # ImpactStage (4-tier model)
    latency.py       # LatencyStage
    partial_fill.py  # PartialFillStage
    order_tracker.py # OrderTracker state machine
    margin.py        # MarginEngine with per-venue configs
    capital.py       # VenueAllocator with per-venue balances
    venue_config.py  # VenueConfig presets (Drift, Hyperliquid, Binance, OKX, Bybit, dYdX)
    tx_costs.py      # TxCostModel ABC + SolanaTxCostModel, HyperliquidTxCostModel, CexTxCostModel
    vamm.py          # VammCurve (constant-product vAMM fill model)
  backtest/        # Event-driven engine, parity test
    engine.py        # BacktestEngine
    calibration.py   # CalibrationEngine (slippage calibration)
  optimization/    # Optuna optimizer, walk-forward validation
  paper/           # Paper trading engine + broker (ticks on live feeds)
  risk/            # RiskGuard chain, EquityMonitor kill switch
    guards.py        # RiskManager + guard chain
    monitor.py       # EquityMonitor (live kill switch)
  portfolio/       # Multi-strategy engine, equal-weight + inverse-vol allocators
  providers/       # 15 data providers, 10 funding venues, WebSocket feeds
    registry.py      # DataProvider ABC, ProviderRegistry
    drift_candles.py # DriftCandleProvider (48 markets)
    hyperliquid_candles.py  # HyperliquidCandleProvider
    drift_s3.py      # DriftS3Provider (archival)
    drift_api.py     # DriftDataProvider (funding, orderbook)
    funding_rates.py # 10-venue funding rate collection
    websocket.py     # WebSocketFeed ABC
    drift_ws.py      # DriftWebSocketFeed
    hyperliquid_ws.py  # HyperliquidWebSocketFeed
    pyth_ws.py       # PythWebSocketFeed
    candle_aggregator.py  # CandleAggregator (trades -> OHLCV)
    orca_ticks.py    # OrcaTickFetcher (CLMM tick data)
    birdeye.py, helius.py, pyth.py, raydium.py, orca.py, gecko.py,
    jupiter.py, coingecko.py, ccxt_provider.py
  connectors/      # Drift (driftpy), Hyperliquid (native REST + EIP-712)
  analytics/       # Metrics, tearsheet, Monte Carlo, correlation
  mev/             # Arb detection, CLMM tick model
    clmm.py          # CLMMPool (concentrated liquidity modeling)
  indicators.py    # 20 technical indicators
  precision.py     # Fixed-point math for Solana (Decimal at boundaries)
  store.py         # Thread-safe DuckDB store -- single shared connection
  config.py        # Pydantic settings (flint.yaml + .env + FLINT_ prefix)
  cli.py           # Typer CLI (8+ commands including calibrate)
  api/main.py      # FastAPI app (30+ endpoints, serves built UI)
  models.py        # Shared dataclasses: Candle, Order, Fill, Signal, etc.
  mcp_server.py    # MCP server for AI integration

ui/                # React 19 + Vite + Tailwind CSS
  pages/           # Dashboard, BacktestLab, DataExplorer, Docs, MevDashboard
  components/      # InteractiveChart (lightweight-charts v5), CodeEditor (Monaco)
  hooks/           # useBacktest, useStrategies, useOptimize, useJournal
```

---

## 2. Execution Hierarchy

`ExecutionContext` is the abstract base class that every strategy receives. It exposes a uniform order API regardless of whether the strategy is running in a backtest, paper session, or live on-chain.

```
ExecutionContext (ABC)
+-- BacktestContext              -- candle-replay, fills via FillPipeline
+-- PaperBroker                  -- same fill models, no on-chain tx
+-- LiveExecutionContext (ABC)
    +-- LiveDriftContext          -- driftpy SDK, Solana RPC
    +-- LiveHyperliquidContext    -- native REST + EIP-712 signing
    +-- MultiVenueLiveContext     -- wraps multiple venue contexts, routes by venue param
```

All order methods (`market_order`, `limit_order`, `stop_order`, `cancel`, `cancel_all`) accept an optional `venue` parameter. Strategies written against `ExecutionContext` deploy to any venue without modification.

### OrderTracker State Machine

`flint/execution/order_tracker.py` -- `OrderTracker` manages the lifecycle of each order:

```
PENDING --> SUBMITTED --> FILLED
                      --> PARTIALLY_FILLED --> FILLED
                      --> CANCELLED
                      --> REJECTED
```

In backtests, orders transition instantly from PENDING to FILLED. In live trading, `OrderTracker` polls the venue for fill confirmations and handles partial fills, rejections, and cancellations.

---

## 3. BacktestEngine

`flint/backtest/engine.py` -- event-driven, single-threaded candle replay.

- Accepts `List[Candle]` (single market) or `Dict[str, List[Candle]]` (multi-market).
- On each bar: feeds candle(s) to strategy -> collects orders -> runs `FillPipeline` -> updates positions and equity.
- Supports v1 strategies (`Signal.BUY/SELL/HOLD`) and v2 strategies (`ctx.market_order()`).
- Enforces a 300-second timeout. Max 5 concurrent backtest runs.
- `venue:market` composite keys enable multi-venue backtesting with per-venue fill models and fee schedules.
- Position keys are `(venue, market)` -- margin and positions are isolated per venue.

---

## 4. Fill Pipeline

The fill pipeline runs inside `BacktestContext` on every order. It is a chain of stages, each applied in sequence:

```
Order
  +-- LatencyStage          -- applies configurable execution latency (ms)
        +-- ImpactStage     -- computes fill price with market impact (4-tier)
              +-- PartialFillStage -- probabilistic partial fill based on volume
                    +-- Fill
```

### 4-Tier Impact Model (ImpactStage)

ImpactStage selects the highest-fidelity model available for each order:

| Tier | Model | When used |
|------|-------|-----------|
| 0 | `VammCurve` (vAMM) | `vamm_enabled: true` and market has a configured vAMM |
| 1 | Orderbook walk (`OrderbookFillModel`) | L2 snapshot present in FlintStore for the bar |
| 2 | Sqrt participation | Volume data available, no orderbook snapshot |
| 3 | Flat bps fallback | No market depth data available |

The tier selection is per-order, per-bar. A strategy running on SOL-PERP with vAMM enabled uses Tier 0; if vAMM is disabled but an L2 snapshot was fetched, it uses Tier 1 automatically.

### FillPipeline class

`FillPipeline` (in `flint/execution/fill_models.py`) subclasses `FillModel` so the engine can use it as a drop-in replacement. It chains the stages together and produces a final `Fill` with venue-specific pricing.

For full details on the math behind each tier, see [slippage-models.md](slippage-models.md).

---

## 5. Margin Engine

`flint/execution/margin.py` -- `MarginEngine` with per-venue configs.

- Position key is `(venue, market)` -- margin is isolated per venue.
- On each bar: recomputes unrealized PnL, checks maintenance margin, triggers liquidation if breached.
- Venue configs (fees, initial margin, maintenance margin, leverage limits) live in `flint/execution/venue_config.py` with presets for Drift, Hyperliquid, Binance, OKX, Bybit, and dYdX.
- Enable in backtest: `margin_tracking: true` in the request body.

### MarginState

`MarginState` tracks per-venue margin accounting:

- Initial margin required
- Maintenance margin requirement
- Unrealized PnL
- Margin ratio (triggers liquidation when below maintenance)

---

## 6. Capital Allocator

`flint/execution/capital.py` -- `VenueAllocator` with per-venue balances.

- Per-venue cash accounts, separate from unrealized PnL.
- Models transfer delays and costs between venues (useful for cross-venue strategy backtests).
- Enable via `capital_allocation: {"drift": 5000, "hyperliquid": 3000}` in the backtest request.
- `fragmentation_metrics()` reports capital utilization and idle cash per venue.
- `ctx.transfer(from_venue, to_venue, amount)` models inter-venue capital movement with configurable delays.

---

## 7. Transaction Cost Models

`flint/execution/tx_costs.py` -- `TxCostModel` ABC with per-venue implementations.

Each fill can include a network-level cost on top of exchange fees:

| Venue | Model | Components |
|-------|-------|-----------|
| Drift | `SolanaTxCostModel` | Priority fee (p50/p90) + Jito bundle tip |
| Hyperliquid | `HyperliquidTxCostModel` | Negligible L1 settlement cost |
| CEX | `CexTxCostModel` | Zero network cost |

Pre-trade cost estimation is available in strategies:

```python
cost = ctx.estimate_cost("SOL-PERP", size_usd=1000)
if cost.total > max_acceptable_cost:
    return  # skip this bar
```

---

## 8. WebSocket Feeds

All live data arrives through a hierarchy of WebSocket feed classes:

```
WebSocketFeed (ABC)              -- reconnection logic, health checks, REST fallback
  +-- DriftWebSocketFeed         -- trade streaming + funding rate subscription
  +-- HyperliquidWebSocketFeed   -- candle, L2 book, orderUpdates channels
  +-- PythWebSocketFeed          -- sub-second oracle prices (Hermes)
```

`WebSocketFeed` base class behavior:

- Reconnection with exponential backoff (1s -> 2s -> 4s ... max 60s).
- Health check: forces reconnect if no message received for 30 seconds.
- On reconnect: backfills missed candles from the REST provider.

`CandleAggregator` (`flint/providers/candle_aggregator.py`) converts raw trade events from WebSocket streams into OHLCV candle bars.

Paper trading uses the same WebSocket feeds as live trading -- it ticks on real candle closes rather than stored historical candles.

---

## 9. FlintStore (DuckDB)

`flint/store.py` -- single shared DuckDB connection wrapped in a `threading.Lock`.

**Thread safety rule**: every method uses `with self._lock:`. Batched upserts are wrapped in `BEGIN TRANSACTION / COMMIT` for atomicity. Never open a second DuckDB connection or access `_conn`/`_lock` directly from outside the class.

### Tables

Data tables:

| Table | Primary Key | Description |
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

Live trading tables: `live_sessions`, `live_orders`, `live_fills`, `live_equity_history`.

---

## 10. Risk Guards

`flint/risk/guards.py` -- `RiskManager` runs a chain of guards before each order is submitted.

| Guard | Config key | Behavior |
|-------|-----------|---------|
| `MaxPositionSize` | `live_per_market_position_limits` | Rejects orders exceeding USD notional cap |
| `MaxOpenPositions` | `max_open_positions` | Rejects new entries when too many positions open |
| `MaxDrawdownCircuitBreaker` | `max_drawdown_pct` | Rejects all orders after cumulative drawdown |
| `DailyLossLimit` | `daily_loss_limit_usd` | Rejects orders after daily loss exceeds threshold |
| `MaxOrdersPerMinute` | `live_max_orders_per_minute` | Sliding window rate limiter |

`EquityMonitor` (`flint/risk/monitor.py`) runs continuously in live sessions. When equity drops below `live_kill_switch_drawdown_pct` from peak, it cancels all open orders and closes all positions immediately across all venues, then fires a critical alert. A warning alert fires at `live_drawdown_warning_pct`.

---

## 11. Calibration Engine

`flint/backtest/calibration.py` -- `CalibrationEngine` fits slippage model coefficients from observed live fills.

- Fits both sqrt (fixed b=0.5) and free power-law models
- Selects via 5-fold cross-validation
- Detects coefficient drift at 15% threshold
- Writes calibrated coefficients back to `VenueConfig`

Run via CLI:

```bash
flint calibrate --venue drift --market SOL-PERP
```

See [slippage-models.md](slippage-models.md) for full details.

---

## 12. MEV and CLMM

`flint/mev/clmm.py` -- `CLMMPool` models concentrated liquidity pools (Orca Whirlpool, Raydium CLMM).

- Computes swap outputs given tick data
- Detects arbitrage opportunities between CLMM price and oracle/perp price
- Used by `MevArbMonitor` strategy and the MEV dashboard

`flint/providers/orca_ticks.py` -- `OrcaTickFetcher` fetches CLMM tick data from Orca's on-chain accounts and stores it in the `tick_snapshots` table.

---

## 13. Data Flow

End-to-end data path from source to backtest/live result:

```
External APIs / WebSockets
        |
        v
   Providers (15 REST + 3 WS feeds)
        |  fetch / stream
        v
   FlintStore (DuckDB, thread-safe)
        |  query
        v
   BacktestEngine / LiveExecutionContext
        |  candles + funding
        v
   Strategy.on_candle()
        |  orders
        v
   RiskManager (guard chain)
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

---

## 14. Extension Points

### New venue (live execution)

1. Create `flint/execution/my_venue_live.py`, subclass `LiveExecutionContext`.
2. Implement the 7 abstract methods: `place_order`, `cancel_order`, `modify_order`, `get_positions`, `get_account`, `poll_fills`, `sync_on_startup`.
3. Add a `VenueConfig` preset in `flint/execution/venue_config.py`.
4. Register in `MultiVenueLiveContext` venue routing.
5. Create a `WebSocketFeed` subclass in `flint/providers/` for live data.

### New data provider

1. Create `flint/providers/my_provider.py`, subclass `DataProvider`.
2. Implement `is_available()` and `supported_data_types()`.
3. Add to `flint/providers/__init__.py`.
4. Register in `flint.yaml` providers section.

### New strategy

1. Create `flint/strategy/my_strategy.py`, subclass `Strategy`.
2. Implement `on_candle()` with v1 signals or v2 context-based orders.
3. Optionally implement `parameters()` for Optuna optimization.
4. Register in the builders dict in `flint/api/routes/backtest.py`.

### New fill model

1. Create a class that implements the `FillModel` interface.
2. Add a new stage class in `flint/execution/fill_models.py`.
3. Insert into `FillPipeline` construction in `BacktestContext.__init__`.

### New WebSocket feed

1. Create `flint/providers/my_venue_ws.py`, subclass `WebSocketFeed`.
2. Implement `connect()`, `subscribe()`, `on_message()`, and `parse_candle()`.
3. The base class handles reconnection, health checks, and REST fallback automatically.
