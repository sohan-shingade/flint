# Flint Roadmap

> Living document for Flint's development trajectory. Phases are sequential — each builds on the prior.
> Last updated: 2026-03-31

## Current State

Flint is **production-ready for backtesting and paper trading**. The core infrastructure that future phases build on:

- **ExecutionContext ABC** — venue parameter on every order method, strategies are venue-agnostic
- **VenueAllocator** — per-venue balances, transfer delays/costs, fragmentation metrics
- **MarginEngine** — per-venue configs, liquidation detection per bar
- **VenueConfig** — fee/margin/leverage presets for Drift, Hyperliquid, Binance, OKX, Bybit, dYdX
- **RiskGuard chain** — MaxPositionSize, MaxOpenPositions, MaxDrawdownCircuitBreaker, DailyLossLimit
- **PaperBroker** — full order lifecycle with fill/fee models, multi-venue allocation
- **BacktestContext** — positions keyed by `(venue, market)`, multi-market engine
- **14 data providers + 10 funding venues** — all REST/polling, no WebSocket yet
- **FlintStore** — thread-safe DuckDB with 12 tables
- **536 tests** — all mocked, no network calls

### Key Gaps

- **Zero WebSocket code** — all providers are REST/polling
- **LiveDriftContext is stubbed** — `submit_pending_orders()` not implemented
- **No execution connectors** — Drift connector is read-only, Jupiter is route-finding only
- **No Hyperliquid connector** — only a funding rate provider exists
- **Paper trading ticks on stored candles** — no live data streaming

---

## Phase 1: Live Trading on Drift (Weeks 1-4)

**Goal**: Deploy a strategy on Drift and have it execute real trades with safety guarantees.

**Difficulty**: Medium-Hard | **Risk**: Solana tx reliability will eat more time than expected

### 1.1 Finish LiveDriftContext

**File**: `flint/execution/drift_live.py` (exists, stubbed)

- [ ] Implement `place_order` via driftpy (market + limit orders)
  - Use `drift_client.place_perp_order()` for market orders
  - Use `drift_client.place_perp_order()` with price param for limit orders
  - Handle order params: market index, direction, base amount, price, order type
- [ ] Implement `cancel_order` via `drift_client.cancel_order(order_id)`
- [ ] Implement `modify_order` via cancel + replace (Drift doesn't have native modify)
- [ ] Fill confirmation polling
  - Poll `drift_client.get_user().get_order()` until order status changes
  - Parse fill events from user account history
  - Handle: fully filled, partially filled, cancelled, expired
- [ ] Position sync on startup
  - Read open positions from `drift_client.get_user().get_perp_position()`
  - Reconcile with local state (FlintStore)
  - Handle: positions opened manually on Drift UI
- [ ] Collateral/margin query
  - Check `get_free_collateral()` before order submission
  - Reject orders that would exceed available margin
- [ ] Error handling
  - RPC failures → retry with exponential backoff (max 3 retries)
  - Transaction dropped → resubmit with higher priority fee
  - Insufficient funds → reject order, log, notify
  - Stale oracle → wait for oracle update, don't submit with stale price
  - Account not found → clear init instructions

**Dependencies**: driftpy SDK (already optional dep), Solana RPC endpoint, funded wallet

**Implemented (Sub-project 1):**
- [x] `LiveExecutionContext` base class for venue-agnostic live trading scaffolding (`flint/execution/live_base.py`)
- [x] `OrderTracker` with state machine, rate limiting, retry logic, callbacks (`flint/execution/order_tracker.py`)
- [x] `WalletAdapter` abstraction with `KeypairAdapter` built, `BrowserWalletAdapter` interface only (`flint/execution/wallet.py`)
- [x] LiveDriftContext rewritten on `LiveExecutionContext` base with all 7 abstract methods implemented
- [x] Timer-based strategy tick loop (poll REST each tick, event-driven deferred to §1.3 WebSocket feeds)
- [x] Live trading tables in FlintStore (live_sessions, live_orders, live_fills, live_equity_history)
- [x] Devnet/mainnet network toggle (`live_network` config, default devnet)

### 1.2 Order Lifecycle Management

**New file**: `flint/execution/order_tracker.py`

- [ ] Order ID mapping: Flint `order_id` (UUID) → Drift on-chain order ID (u32)
- [ ] State machine: `pending → submitted → confirmed → filled/cancelled/expired`
  - `pending`: order created locally, not yet sent
  - `submitted`: tx sent to RPC, awaiting confirmation
  - `confirmed`: tx landed on-chain, order in Drift orderbook
  - `filled`: fill event received (full or partial)
  - `cancelled`: user cancelled or expired
- [ ] Timeout logic
  - If no tx confirmation within 30 seconds → cancel and retry
  - If no fill within N bars (configurable) → cancel limit order
  - Max retry count: 3, then give up and log
- [ ] Rate limiting
  - Max 10 orders per second to RPC
  - Max 2 concurrent tx submissions
  - Queue excess orders, process FIFO

### 1.3 Live Data Feed

**New file**: `flint/providers/websocket.py` (base), `flint/providers/drift_ws.py`

- [ ] WebSocket candle streaming from Drift
  - Subscribe to trade events, aggregate into candles locally
  - Fallback: poll REST every 5s if WS disconnects
- [ ] Real-time funding rate updates
  - Subscribe to funding rate changes via Drift events
  - Update FlintStore on each new rate
- [ ] Oracle price feed via Pyth WebSocket
  - Use Pyth's Hermes WebSocket for sub-second price updates
  - Feed into risk engine for real-time margin monitoring
- [ ] Reconnection logic
  - Exponential backoff: 1s, 2s, 4s, 8s, max 60s
  - On reconnect: backfill missed candles from REST API
  - Health check: if no message for 30s, force reconnect
  - Log all disconnects with duration

**Note**: This is the single biggest new subsystem. No WebSocket code exists anywhere in the codebase today. Design the base class (`WebSocketFeed`) to be reusable for Hyperliquid in Phase 2.

**Implemented (Sub-project 2):**
- [x] `WebSocketFeed` base class with reconnection, health checks, REST fallback (`flint/providers/websocket.py`)
- [x] `CandleAggregator` — raw trades → OHLCV candle bars (`flint/providers/candle_aggregator.py`)
- [x] `DriftWebSocketFeed` — trade streaming + funding rate subscription (`flint/providers/drift_ws.py`)
- [x] `PythWebSocketFeed` — sub-second oracle prices with batch persistence (`flint/providers/pyth_ws.py`)
- [x] Event-driven tick mode (`on_candle_close`) replacing timer-based ticking
- [x] `venue` field on `Candle` dataclass for multi-venue support
- [x] `tick_markets` config for controlling which markets trigger strategy ticks
- [x] `get_oracle_price()` convenience method on ExecutionContext

### 1.4 Safety Rails

**Extend**: `flint/risk/guards.py`

- [ ] Max position size per market (hard cap, separate from margin)
  - Config: `max_position_usd: {SOL-PERP: 10000, BTC-PERP: 50000}`
- [ ] Max drawdown kill switch
  - Monitor equity in real-time (not just per bar)
  - If equity drops X% from peak → flatten all positions immediately
  - Config: `kill_switch_drawdown_pct: 0.15`
  - Require manual restart after kill switch triggers
- [ ] Max orders per minute
  - Sliding window rate limiter
  - Config: `max_orders_per_minute: 30`
  - Prevents runaway strategy loops
- [ ] Dry-run mode
  - Log exactly what would be submitted (market, side, size, price)
  - No actual tx submission
  - Useful for validating a strategy before going live
  - Config: `dry_run: true` in flint.yaml
- [ ] Alerting
  - Telegram bot notifications (simple HTTP POST to Bot API)
  - Events: fill received, position opened/closed, liquidation risk, kill switch triggered, error
  - Config: `alerts.telegram_bot_token` + `alerts.telegram_chat_id`

**Implemented (Sub-project 3):**
- [x] `EquityMonitor` with real-time kill switch — auto-flattens all positions on drawdown breach (`flint/risk/monitor.py`)
- [x] `MaxOrdersPerMinute` risk guard — sliding window rate limiter
- [x] `PerMarketPositionLimit` risk guard — per-market USD notional caps
- [x] Dry-run mode — full pipeline with simulated fills, `tx_sig="DRY_RUN"`
- [x] Alert integration — fills, rejections, failures, kill switch fire Telegram/Discord notifications
- [x] Safety rails config: kill switch threshold, warning threshold, rate limits, per-market limits

### 1.5 Backtest-to-Live Parity Test

- [ ] Run paper engine and backtest engine on same historical window
- [ ] Compare: fill prices, PnL curve, signal timing, position sizes
- [ ] Compute divergence metrics: MAE of fill prices, correlation of equity curves
- [ ] Output parity report (JSON + human-readable)
- [ ] Acceptable threshold: < 2% PnL divergence on a trend-following strategy

**Implemented (Sub-project 3):**
- [x] `ParityTest` class comparing backtest vs paper engine (`flint/backtest/parity.py`)
- [x] `ParityReport` with divergence metrics: PnL divergence, fill price MAE, equity correlation, signal timing match
- [x] CLI: `flint parity --strategy <name> --market <market> --start <date> --end <date>`
- [x] API: `POST /api/v1/backtest/parity`
- [x] Pass/fail threshold: < 2% PnL divergence

### Phase 1 Deliverables

1. A strategy running live on Drift devnet with real order submission
2. WebSocket base class reusable for Phase 2
3. Safety rails that prevent catastrophic loss
4. Parity report showing backtest ≈ live behavior

---

## Phase 2: Hyperliquid Integration (Weeks 4-6)

**Goal**: Second venue working. Strategies deploy to either venue with zero code changes.

**Difficulty**: Medium | **Risk**: Low — Hyperliquid API is cleaner than Drift's on-chain model

### 2.1 Hyperliquid Connector

**New file**: `flint/connectors/hyperliquid.py`

What already exists:
- `HyperliquidFundingProvider` in `flint/providers/funding_rates.py` — has market mappings, API base URL
- `VenueConfig` preset for Hyperliquid — fees, margin, leverage all configured

What to build:
- [ ] REST API client (not CCXT — Hyperliquid's native API has better granularity)
  - Order placement: `POST /exchange` with action `order`
  - Order cancellation: `POST /exchange` with action `cancel`
  - Position query: `POST /info` with type `clearinghouseState`
  - Open orders: `POST /info` with type `openOrders`
- [ ] EIP-712 signing for order authorization
  - Hyperliquid uses Ethereum-style signatures (not Solana)
  - Private key from env var `FLINT_HYPERLIQUID_PRIVATE_KEY`
- [ ] L2 orderbook snapshots via WebSocket
  - Subscribe to `l2Book` channel
  - Maintain local orderbook state (apply deltas)
  - Feed into fill model for accurate impact estimation
- [ ] Market metadata
  - Leverage tiers per market
  - Fee tiers based on volume
  - Min order sizes, tick sizes, lot sizes
  - Fetch on startup, cache locally

**Implemented:**
- [x] `HyperliquidClient` async REST connector with EIP-712 signing (`flint/connectors/hyperliquid.py`)
- [x] Info endpoints: get_meta, get_clearinghouse_state, get_open_orders, get_user_fills, get_candle_snapshot, get_l2_book
- [x] Exchange endpoints: place_order, cancel_order, cancel_all_orders with signed requests
- [x] Market metadata caching (asset indices, tick sizes, lot sizes) from get_meta()
- [x] Precision formatting (format_size, format_price) per asset
- [x] Testnet/mainnet URL + chain ID toggle
- [x] `FLINT_HYPERLIQUID_PRIVATE_KEY` env var authentication (API wallet recommended, withdrawals via Hyperliquid web UI)

### 2.2 Hyperliquid Live Execution

**New file**: `flint/execution/hyperliquid_live.py`

- [ ] Implement `ExecutionContext` interface (same as LiveDriftContext)
  - `market_order()` → Hyperliquid market order
  - `limit_order()` → Hyperliquid limit order with TIF options
  - `stop_order()` → Hyperliquid trigger order
  - `cancel()` / `cancel_all()` → Hyperliquid cancel
- [ ] Fill confirmation via WebSocket user events
  - Subscribe to `user` channel for fill notifications
  - Parse fill price, quantity, fee
  - Update local position state
- [ ] Position sync on startup
  - Query `clearinghouseState` for open positions
  - Reconcile with FlintStore
- [ ] Same safety rails as Drift (reuse RiskGuard chain)

**Implemented:**
- [x] `LiveHyperliquidContext(LiveExecutionContext)` with all 7 abstract methods (`flint/execution/hyperliquid_live.py`)
- [x] Market order simulation via IOC limit with configurable slippage (default 0.3%)
- [x] Position parsing from clearinghouse state (long/short detection, zero filtering)
- [x] Balance extraction from marginSummary.accountValue
- [x] Order status polling: open orders → fills → cancelled fallback
- [x] `HyperliquidWebSocketFeed` with candle, L2 book, and orderUpdates channels (`flint/providers/hyperliquid_ws.py`)
- [x] All safety rails reused (kill switch, risk guards, dry-run mode)

### 2.3 Hyperliquid Backtest Data

- [ ] Historical candles via Hyperliquid API
  - `POST /info` with type `candleSnapshot`
  - Available resolutions: 1m, 5m, 15m, 1h, 4h, 1d
  - Store in FlintStore with `venue="hyperliquid"` tag
- [ ] Historical orderbook snapshots (if available)
  - Useful for OrderbookFillModel in backtests
- [ ] Integrate into existing data download pipeline
  - `flint download --venue hyperliquid --market SOL-PERP`
  - Or via API: `POST /api/v1/data/download` with venue param

**Implemented:**
- [x] `HyperliquidCandleProvider` for historical candle data (`flint/providers/hyperliquid_candles.py`)
- [x] Pagination support (5000 candles per batch)
- [x] All 6 resolutions: 1m, 5m, 15m, 1h, 4h, 1d
- [x] Integrated into data download pipeline (`flint/api/routes/data.py`)
- [x] 17 markets supported via HYPERLIQUID_SYMBOLS mapping

### Phase 2 Deliverables

1. Same strategy deployable to Drift or Hyperliquid with only config change
2. Hyperliquid historical data in FlintStore for backtesting
3. Real-time orderbook feed for live execution

---

## Phase 3: Cross-Venue Strategies (Weeks 6-9)

**Goal**: Strategies that hold positions on multiple venues simultaneously. This is Flint's moat — no open-source framework does this for DeFi perps.

**Difficulty**: Medium | **Risk**: Partial fill handling across venues is tricky

### 3.1 Multi-Venue Execution Context

**Extend**: `flint/execution/live_context.py`

What already exists:
- `BacktestContext` keys positions by `(venue, market)` — multi-venue backtesting works today
- `VenueAllocator` handles per-venue balances and transfers
- All order methods accept `venue: str` parameter
- `sol_btc_pairs.py` template shows multi-venue splits

What to build:
- [ ] `MultiVenueLiveContext` that wraps multiple venue-specific contexts
  - Routes orders to correct venue based on `venue` parameter
  - Aggregates positions across venues for unified view
  - Maintains separate connection state per venue
- [ ] Unified position view
  - `total_exposure(market)` → net size across all venues
  - `net_delta()` → total portfolio delta
  - `per_venue_pnl()` → PnL attribution by venue
- [ ] Per-venue margin isolation
  - Drift margin check doesn't block Hyperliquid order
  - Each venue context manages its own margin independently
  - Alert if total cross-venue exposure exceeds threshold
- [ ] Parallel order submission
  - Submit legs to different venues concurrently (asyncio.gather)
  - Handle partial fills: if leg A fills but leg B doesn't
  - Configurable: `wait_for_all_legs: true` or `independent_legs: true`
  - Timeout per leg: cancel unfilled side after N seconds

**Implemented:**
- [x] `MultiVenueLiveContext(ExecutionContext)` wrapping multiple venue contexts (`flint/execution/multi_venue_live.py`)
- [x] Order routing by venue parameter (market, limit, stop, take_profit, cancel, cancel_all)
- [x] Aggregated `account` property (sum of all venue equity/cash)
- [x] `venue_account(venue)` for per-venue breakdown
- [x] `total_exposure(market)` net size across venues
- [x] `per_venue_pnl()` unrealized PnL per venue
- [x] Paired leg submission (`submit_leg_group`) with timeout and optional auto-unwind
- [x] Configurable tick mode: "primary" (single venue triggers ticks) or "any" (all venues trigger)
- [x] EquityMonitor integration via aggregated account property
- [x] `OrderLeg`, `LegGroup`, `LegGroupResult` dataclasses in models.py

### 3.2 Funding Arb Strategy (Built-In Template)

**New file**: `flint/strategy/funding_arb.py`

- [ ] Monitor funding rate spread across venues
  - Use `ctx.get_funding_by_venue(market)` (already exists in ExecutionContext)
  - Compare: Drift vs Hyperliquid vs Binance (via CCXT)
- [ ] Entry signal
  - Spread exceeds threshold (e.g., > 5bps/hr divergence)
  - Configurable: `min_spread_bps`, `lookback_hours`, `min_spread_duration`
- [ ] Execution
  - Long on low-funding venue, short on high-funding venue
  - Equal size on both legs (delta neutral)
  - Use `ctx.market_order(market, "buy", size, venue="drift")`
  - Use `ctx.market_order(market, "sell", size, venue="hyperliquid")`
- [ ] Exit conditions
  - Spread reverts below threshold
  - Max hold period exceeded (e.g., 24h)
  - Drawdown on the pair exceeds limit
- [ ] Backtest validation
  - Run on historical cross-venue funding data (already in FlintStore)
  - Expect: low volatility, consistent positive carry, Sharpe > 2

**Implemented:**
- [x] `FundingArbStrategy` template with Optuna-optimizable parameters (`flint/strategy/funding_arb.py`)
- [x] Cross-venue funding spread detection via `ctx.get_funding_by_venue()`
- [x] Delta-neutral entry: long low-funding venue, short high-funding venue
- [x] Exit on spread convergence or max hold time
- [x] Min spread duration guard
- [x] Works in both backtest and live modes

### 3.3 Cross-Venue Backtest Engine

**Extend**: `flint/backtest/engine.py`

- [ ] Synchronize candles across venues by timestamp
  - Align to common time grid (use highest common resolution)
  - Handle missing candles: forward-fill from last known price
- [ ] Handle different resolutions
  - Drift 1h candles + Hyperliquid 1m candles
  - Upsample or downsample to match strategy's requested resolution
- [ ] Per-venue fill pipeline
  - Each venue uses its own VenueConfig for fees, impact, latency
  - Drift fills use Drift's fee model, Hyperliquid uses Hyperliquid's
- [ ] Combined analytics
  - Unified equity curve (sum of per-venue equity)
  - PnL attribution by venue
  - Funding income breakdown by venue
  - Correlation of venue-specific returns

**Implemented:**
- [x] `venue:market` composite key parsing in BacktestEngine (`_parse_venue_market`)
- [x] Backward compatible: plain keys (no prefix) default to "default" venue
- [x] Per-venue PnL, trade count, and funding income in BacktestResult
- [x] Candle venue tagging from composite keys

### Phase 3 Deliverables

1. Funding arb strategy running in backtest with historical data
2. Same strategy deployable live on Drift + Hyperliquid simultaneously
3. Cross-venue backtest results with per-venue attribution

---

## Phase 4: Execution Fidelity (Weeks 9-13)

**Goal**: Make backtests trustworthy enough that live performance matches within 5%.

**Difficulty**: Hard | **Risk**: Research-heavy, depends on Phase 1 being live for calibration data

### 4.1 vAMM Curve Modeling

**New file**: `flint/execution/vamm.py`

- [ ] Model Drift's vAMM math
  - K factor (liquidity depth constant)
  - Peg multiplier (oracle price anchor)
  - Base/quote reserves
  - Source of truth: Drift's Rust SDK `amm.rs`
- [ ] Compute vAMM fill price for a given order size
  - `vamm_fill_price(base_amount, direction, amm_state) → price`
  - Account for: spread, inventory skew, oracle divergence
- [ ] Compare fill models on historical data
  - vAMM fill vs OrderbookFillModel vs ClosePriceFill
  - Metric: mean absolute error vs actual Drift fills
  - Need: actual fill data from Phase 1 live trading
- [ ] Publish accuracy report
  - Per-market fill model comparison
  - Recommended fill model per market based on liquidity

### 4.2 Concentrated Liquidity for Arb Detection

**Extend**: `flint/providers/orca.py`, `flint/providers/raydium.py`

- [ ] Replace constant-product math with tick-range model for Orca Whirlpools
  - Tick spacing, liquidity per tick range
  - Price impact as function of liquidity distribution
- [ ] Liquidity distribution snapshots
  - Store tick-level liquidity in FlintStore
  - Historical snapshots for backtest-accurate arb detection
- [ ] More accurate arb profit estimates
  - Current arb scanner uses constant-product → overestimates profit
  - Tick-range model accounts for concentrated liquidity zones

### 4.3 Transaction Cost Model

**New file**: `flint/execution/tx_costs.py`

- [ ] Solana priority fees
  - Historical priority fee distribution (collect from RPC `getRecentPrioritizationFees`)
  - Percentile-based estimation (use p50 for normal, p90 for urgent)
- [ ] Jito tip estimation
  - Historical bundle tip data (if available via Jito API)
  - Model: tip = f(block demand, time of day, market volatility)
- [ ] Total cost model
  - `total_cost = exchange_fee + priority_fee + jito_tip + market_impact`
  - Surface in backtest results: `results.total_costs`, `results.cost_breakdown`
  - Strategy can query: `ctx.estimate_cost(market, size)` before trading
- [ ] Integration with backtest engine
  - Deduct total costs from PnL per trade
  - Show cost drag on equity curve

### 4.4 Slippage Calibration

**Requires**: Real execution data from Phase 1

- [ ] Collect actual Drift fills (price, size, timestamp, market)
- [ ] Compare vs backtest fills on same market/time/size
- [ ] Compute impact coefficient calibration
  - Current: hardcoded per venue in VenueConfig
  - Calibrated: fit from observed fills → `impact = a * size^b`
- [ ] Auto-tune venue configs
  - `flint calibrate --venue drift --market SOL-PERP`
  - Uses last N fills to fit impact model
  - Updates VenueConfig with calibrated coefficients
- [ ] Ongoing drift detection
  - If live fills diverge from model by > threshold → alert
  - Suggests recalibration

**Implemented:**
- [x] `CalibrationEngine` with power-law and square-root model fitting (`flint/backtest/calibration.py`)
- [x] Volatility + ADV normalization for regime-robust calibration
- [x] Huber-like robust regression with iterative reweighting
- [x] Bootstrap 95% confidence intervals on coefficients
- [x] 5-fold cross-validation for model selection (power-law vs sqrt)
- [x] Drift detection with configurable threshold (default 15%)
- [x] CLI: `flint calibrate --venue <venue> --market <market>` (writes to config by default, `--dry-run` to skip)
- [x] API: `POST /api/v1/calibrate` (read-only)
- [x] `CalibrationReport` and `DriftReport` dataclasses with summary() and to_dict()
- [x] `query_live_fills_by_venue()` store method for fill retrieval

### Phase 4 Deliverables

1. vAMM fill model validated against real Drift fills
2. Transaction cost model that includes priority fees + Jito tips
3. Calibrated impact coefficients from live execution data
4. Backtest PnL within 5% of live PnL on same strategy/period

---

## Phase 5: Adoption and Community (Weeks 13-16)

**Goal**: Make Flint usable by people who aren't you.

**Difficulty**: Easy | **Risk**: Low — writing and polish, not engineering risk

### 5.1 Strategy Templates

Each template needs: code, README, backtest results, parameter ranges, known limitations.

- [ ] Funding arb (Drift vs Hyperliquid) — from Phase 3
- [ ] Momentum breakout on Drift perps with Pyth oracle confirmation
- [ ] Mean reversion on funding rate (Bollinger bands on hourly funding)
- [ ] MEV arb scanner (Raydium/Orca pool price discrepancies)
- [ ] Cross-venue basis trade (spot vs perp, or venue A vs venue B)

### 5.2 Documentation

- [ ] Quickstart: install → pull data → run first backtest in < 5 minutes
- [ ] Strategy authoring guide
  - ExecutionContext API reference
  - v1 (Signal-based) vs v2 (ctx-based) strategies
  - Multi-market strategies
  - Optimization with `parameters()` method
- [ ] Data provider guide
  - Which providers, what data, API keys needed
  - How to add a custom provider
- [ ] Live deployment guide
  - Wallet setup, RPC endpoint, risk config
  - Drift devnet testing → mainnet
  - Monitoring and alerting setup
- [ ] Architecture overview
  - How fill pipeline, margin engine, providers connect
  - Data flow diagrams
  - Extension points

### 5.3 Web Dashboard Enhancements

**Extend**: `ui/src/pages/`

- [ ] Live paper trading monitor (real-time position updates)
- [ ] Fill analysis view (slippage, impact, latency per trade)
- [ ] Cross-venue funding spread heatmap
- [ ] MEV opportunity timeline (from arb scanner)
- [ ] Strategy deployment panel (select strategy → configure → deploy to venue)

### 5.4 Community Infrastructure

- [ ] Discord server with channels: strategy-discussion, support, showcase
- [ ] GitHub issue templates: bug report, feature request, strategy idea
- [ ] Contributing guide with dev setup instructions
- [ ] Strategy submission process (PR template + required backtest results)

### Phase 5 Deliverables

1. Five battle-tested strategy templates with docs
2. Complete documentation for self-serve onboarding
3. Dashboard that monitors live and paper trading
4. Community channels set up and welcoming

---

## Timeline Summary

| Phase | Scope | Estimate | Cumulative | Difficulty |
|-------|-------|----------|------------|------------|
| 1. Live Drift | On-chain execution, WS feeds, safety | 3-4 weeks | Week 4 | Medium-Hard |
| 2. Hyperliquid | Second venue, same interface | 2 weeks | Week 6 | Medium |
| 3. Cross-Venue | Multi-venue strategies, funding arb | 2-3 weeks | Week 9 | Medium |
| 4. Execution Fidelity | vAMM, tx costs, calibration | 4 weeks | Week 13 | Hard |
| 5. Adoption | Templates, docs, dashboard, community | 3 weeks | Week 16 | Easy |

**Total: ~14-16 weeks for a solo developer.**

### Dependencies

```
Phase 1 ──→ Phase 2 ──→ Phase 3
                              │
Phase 1 (live data) ─────→ Phase 4
                              │
Phase 3 + Phase 4 ────────→ Phase 5
```

- Phases 1→2→3 are strictly sequential (each venue builds on the prior)
- Phase 4 needs live execution data from Phase 1 (specifically 4.4 slippage calibration)
- Phase 4.1-4.3 (vAMM, CLMM, tx costs) can start during Phase 2-3 since they're research
- Phase 5 can start partially during Phase 3 (docs, templates for single-venue)

### Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Solana tx reliability (dropped txs, stale oracles) | Phase 1 takes 5+ weeks | Start with devnet, build robust retry logic early |
| driftpy SDK breaking changes | Blocks Phase 1 | Pin version, have fallback to raw RPC calls |
| Hyperliquid API changes | Blocks Phase 2 | Use native API (not CCXT), version-pin endpoints |
| Insufficient live fill data for calibration | Phase 4.4 delayed | Run paper + live in parallel to accumulate data faster |
| WebSocket reconnection edge cases | Affects all live phases | Build reconnection into base class (Phase 1), test with network chaos |
| Cross-venue partial fills | Phase 3 complexity | Start with independent legs, add atomic mode later |

---

## Backlog (Not Prioritized)

Features that are real but not worth building until Phases 1-5 are solid.

- **On-chain slot replay** — slot-by-slot Solana state replay for sub-second backtesting. Massive engineering lift.
- **dYdX / GMX integration** — third/fourth venue. Do after Drift + Hyperliquid are proven.
- **Advanced optimization** — Bayesian/genetic search. Existing Optuna + walk-forward is fine for now.
- **Mobile alerts app** — Telegram bot (Phase 1.4) covers this.
- **Rust backtest engine** — Python engine is fast enough. Only rewrite if throughput bottlenecks.
- **Strategy marketplace** — too early. Need users first.
- **Portfolio-level risk engine** — correlation-aware cross-venue stress testing. After Phase 3.
- **CCXT execution wrapper** — generic CEX execution via CCXT. After Hyperliquid proves the pattern.
