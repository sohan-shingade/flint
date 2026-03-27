# Paper Trading Realism Overhaul — Design Spec

## Problem Statement

The paper trading engine is substantially less realistic than the backtest engine. Strategies that perform well in backtesting behave differently in paper trading because: fills are priced at candle close with no slippage, funding rates are never applied, position state is lost on restart, LiveContext doesn't expose funding/orderbook/OI data that strategies depend on, candle history goes stale after startup, and multi-venue strategies cannot run at all.

This spec closes every realism gap between backtesting and paper trading. After implementation, paper trading should produce results within 5% of what a real Drift account would see, and multi-venue strategies should work identically to backtesting.

## Goals

1. Apply funding rate payments to open perp positions every hour
2. Use realistic fill pricing with slippage and optional orderbook simulation
3. Persist full session state (positions, orders, trades) and resume after restart
4. Make LiveContext functionally equivalent to BacktestContext for data access
5. Keep candle history rolling so indicators never go stale
6. Add configurable order latency simulation
7. Update position PnL between candles using the price ticker
8. Add limit order timeouts
9. Expose margin metrics (leverage, free margin, liquidation prices) through the API
10. Support multi-venue paper trading with per-venue capital, positions, and transfers

## Non-Goals

- WebSocket streaming to the UI (polling is fine for now)
- Cross-margin simulation across venues (documented limitation, same as Freqtrade)
- Real on-chain order execution (that's the live trading feature)

---

## 1. Funding Rate Application

### What

Apply hourly funding rate charges/credits to open perpetual positions. Drift settles funding every hour. Longs pay shorts when funding is positive, shorts pay longs when negative.

### How

**New method on PaperBroker** — `apply_funding(market, rate, mark_price)`:
- For each open position in the market:
  - `payment = position_size * mark_price * funding_rate`
  - If LONG and rate > 0: deduct from cash (long pays)
  - If SHORT and rate > 0: add to cash (short receives)
  - Inverse for negative rates
  - Accumulate in `self.total_funding`
  - Record in `paper_funding_payments` table

**Funding collection loop** in `_run_live_session`:
- Every iteration (10s), check if a new funding rate has been stored by the collector since the last application
- Query `venue_funding_rates` for the session's market with `ts > last_funding_ts`
- Apply each new rate to open positions
- Use the Drift venue's funding rates (primary), with fallback to cross-venue average

**New DuckDB table** — `paper_funding_payments`:
| Column | Type | Description |
|--------|------|-------------|
| session_id | VARCHAR | FK to paper_sessions |
| ts | BIGINT | Funding timestamp |
| market | VARCHAR | Market |
| rate | DOUBLE | Hourly funding rate |
| payment | DOUBLE | Amount paid (+) or received (-) |
| position_size | DOUBLE | Position size at time of payment |
| mark_price | DOUBLE | Mark price used |

### Impact
- Funding payments of 3-10% annualized will now be reflected in paper P&L
- Funding-based strategies (FundingHarvest, MultiVenueFunding) will work in paper trading
- Equity curve will show funding drags/boosts accurately

---

## 2. Realistic Fill Pricing

### What

Replace `ClosePriceFill` with `SlippageFill` as the default fill model for paper trading. Optionally support `OrderbookFillModel` when orderbook data is available.

### How

**Change PaperBroker default fill model**:
```python
# Current: self.fill_model = fill_model or ClosePriceFill()
# New:     self.fill_model = fill_model or SlippageFill(slippage_bps=5.0)
```

`SlippageFill` (already implemented in `fill_models.py`) adds configurable basis points of slippage to the fill price:
- BUY orders fill at `close * (1 + slippage_bps/10000)`
- SELL orders fill at `close * (1 - slippage_bps/10000)`

Default 5 bps matches Drift's typical spread for SOL/BTC/ETH perps.

**When orderbook data is available** (from collector), use `OrderbookFillModel` for more accurate fills that walk the book. This is opt-in via a parameter on deploy.

**Also apply the venue's fee schedule** instead of flat 5bps:
- Drift: 10bps taker, -2bps maker (from `venue_config.py`)
- Use `DriftFeeModel` instead of `FlatFeeModel`

### Impact
- Fill prices reflect actual spread costs
- Large orders show worse fills (if using orderbook model)
- Fee structure matches Drift exactly

---

## 3. Session Persistence and Resumption

### What

Persist full broker state (positions, pending orders, cash) to DuckDB on every candle. On server restart, automatically resume all sessions that were in "live" status.

### How

**Persist on every candle** (not every 10):
- `session_store.save_positions(session_id, broker.positions)`
- `session_store.save_equity_snapshots(session_id, [snapshot])` — every candle, not batched
- `session_store.save_trades(session_id, new_closed_trades)` — after any trade closes

**New method** — `PaperTradingEngine.resume_sessions()`:
- Query `paper_sessions` for all sessions with `status IN ('live', 'replaying')`
- For each:
  1. Load session metadata (strategy code, params, market, capital, risk config)
  2. Load positions from `paper_positions`
  3. Load last equity snapshot to get cash/equity
  4. Reconstruct strategy via `load_user_strategy(code, params)`
  5. Create `PaperBroker` with restored cash and inject positions
  6. Create `LiveContext` wrapping the broker
  7. Attach `RiskGuard` with stored risk config
  8. Launch `_run_live_session` task
  9. Set `last_candle_ts` from the latest equity snapshot timestamp
- Log which sessions were resumed and which failed

**Call on startup** in `flint/api/main.py` lifespan handler:
```python
paper_engine.resume_sessions()
```

**Handle open orders on restart**: Treat all pending orders as cancelled (same as Freqtrade's approach). Log a warning.

### Impact
- Server restarts no longer lose position state
- Users can deploy strategies and trust they'll survive maintenance windows
- Equity history is complete (no gaps from restarts)

---

## 4. LiveContext Data Access (Funding, Orderbook, OI, Candles)

### What

Make `LiveContext` expose the same data access methods as `BacktestContext` by querying DuckDB for historical data.

### How

**Add methods to LiveContext** that query the store:

```python
def get_funding_rates(self, market=None, lookback=24):
    """Query venue_funding_rates from DuckDB."""
    mkt = market or self._current_market
    now = int(time.time())
    start = now - lookback * 3600
    rates = self._store.query_venue_funding(mkt, start, now)
    return [(r.ts, r.rate) for r in rates]

def get_funding_by_venue(self, market=None, lookback=24):
    """Query funding rates grouped by venue."""
    mkt = market or self._current_market
    now = int(time.time())
    start = now - lookback * 3600
    return self._store.query_funding_by_venue(mkt, start, now)

def get_orderbook(self, market=None):
    """Get latest orderbook snapshot from DuckDB."""
    mkt = market or self._current_market
    snapshots = self._store.query_orderbook_snapshots(mkt, limit=1)
    return snapshots[0] if snapshots else None

def get_open_interest(self, market=None):
    """Get latest open interest from DuckDB."""
    mkt = market or self._current_market
    # query open_interest table
    ...

def get_candles(self, market, lookback=50):
    """Get recent candles for any market from DuckDB."""
    candles = self._store.query_candles(market, self._resolution_s)
    return candles[-lookback:] if candles else []

def log(self, message):
    """Log strategy messages."""
    import logging
    logging.getLogger("flint.paper.strategy").info("[%s] %s", self._session_id, message)
```

**LiveContext needs a store reference**: Pass `store` to the constructor:
```python
class LiveContext(ExecutionContext):
    def __init__(self, broker, store=None, resolution_s=3600, session_id=""):
        self._broker = broker
        self._store = store
        self._resolution_s = resolution_s
        self._session_id = session_id
```

Update `deploy_session` to pass the store when creating LiveContext.

### Impact
- Funding rate strategies (FundingHarvest, MultiVenueFunding) work in paper trading
- Strategies using `ctx.get_orderbook()` get real Drift orderbook data
- Strategies using `ctx.get_candles("BTC-PERP")` while trading SOL can access cross-market data
- Strategy behavior matches backtesting exactly

---

## 5. Rolling Candle History

### What

Keep the candle history list updated as new candles arrive, maintaining a rolling window. Currently history is loaded once at startup (200 candles) and new candles are appended without trimming or being included in the history passed to the strategy.

### How

In `_run_live_session`, the history list already gets `history.append(candle)` for each new candle. The fix is to also trim it to prevent unbounded growth:

```python
for candle in candles:
    history.append(candle)
    if len(history) > 500:
        history = history[-500:]  # keep rolling window
    session.last_candle_ts = candle.ts
    # ... rest of processing
```

The 500-candle window (vs current 200) gives 20+ days of hourly data — enough for any indicator combination (50-period SMA + 14 RSI + 20 BB = needs ~55 bars, but longer windows help with trend detection).

### Impact
- Indicators always have fresh data
- No memory leak from unbounded history growth
- Strategies with long lookback periods work correctly

---

## 6. Order Latency Simulation

### What

Simulate the 1-3 second delay between order submission and fill that exists on Drift. Orders submitted on candle N don't fill until candle N+1 (for hourly candles this is a minor effect, but for 5m/15m candles it matters).

### How

**Use the existing `FillPipeline` with latency stage** instead of raw `SlippageFill`:

```python
from .fill_models import FillPipeline

fill_model = FillPipeline(
    slippage_bps=5.0,
    base_latency_s=venue_config.base_latency_s,  # 1.0s for Drift
    latency_jitter_s=venue_config.latency_jitter_s,  # 0.5s for Drift
)
```

For hourly candles, the latency is negligible (1s vs 3600s candle). But the infrastructure is in place for when users run 5m or 1m candle strategies.

**Make this configurable** via the deploy request:
```json
{
  "fill_model": "slippage",      // "slippage" (default), "orderbook", "close"
  "slippage_bps": 5.0,
  "latency_enabled": true
}
```

### Impact
- Order fills are slightly more realistic
- Infrastructure ready for sub-hourly strategies
- Configurable per-session (users can disable for faster testing)

---

## 7. Inter-Candle PnL Updates via Price Ticker

### What

Use the `PriceTicker` (which polls Drift DLOB every 5s) to update position mark prices and unrealized PnL between candle arrivals. Currently PnL only updates once per candle (hourly).

### How

In `_run_live_session`, after the candle processing block and before `await asyncio.sleep(10)`, add a price update step:

```python
# Update mark prices from ticker (between candles)
ticker = getattr(self, 'price_ticker', None)
if ticker and session.broker.positions:
    mark = ticker.get_price(session.market)
    if mark:
        for market, pos in session.broker.positions.items():
            if market == session.market:
                old_mark = pos.get("mark_price", pos["entry_price"])
                pos["mark_price"] = mark
                if pos["side"] == "long":
                    pos["unrealized_pnl"] = (mark - pos["entry_price"]) * pos["size"]
                else:
                    pos["unrealized_pnl"] = (pos["entry_price"] - mark) * pos["size"]
```

This means:
- Candle arrives → strategy runs, orders processed, equity snapshot saved
- Between candles (every 10s) → mark price updated from DLOB ticker
- UI polling (every 2s) → sees near-real-time PnL

### Impact
- Position PnL reflects current market price, not hour-old candle close
- Risk guard drawdown checks use fresher prices
- UI shows responsive equity changes

---

## 8. Limit Order Timeouts

### What

Add configurable timeout for limit orders. If a limit order hasn't filled within N bars, automatically cancel it. Prevents orders from hanging forever.

### How

**Add `timeout_bars` field to Order model** (or track submission time):

In `PaperBroker.process_candle()`, before checking fills, expire old orders:

```python
# Expire timed-out limit orders
now_ts = candle.ts
expired = []
for order in self.pending_orders:
    if order.order_type in ("limit", "stop", "take_profit"):
        age_bars = (now_ts - order.ts) // self._resolution_s
        if age_bars >= self._limit_timeout_bars:
            expired.append(order)
for order in expired:
    self.pending_orders.remove(order)
    logger.info("Order %s expired after %d bars", order.order_id, self._limit_timeout_bars)
```

Default `_limit_timeout_bars = 24` (24 hours for 1H candles). Configurable via deploy request.

### Impact
- Stale limit orders don't hang forever
- More realistic — real exchanges have order expiry policies
- Prevents strategies from accumulating hundreds of unfilled orders

---

## 9. Enhanced API Response with Margin Metrics

### What

The `/paper/status/{id}` endpoint should return margin metrics (leverage, free margin, liquidation prices, margin ratio) and the funding payments history.

### How

**Extend the status response** in `paper.py`:

```python
# In get_status or the status endpoint handler
status_dict = session.to_dict()
status_dict["margin"] = {
    "leverage": broker.leverage,
    "margin_used": round(broker.margin_used, 2),
    "free_margin": round(broker.free_margin, 2),
    "margin_ratio": round(broker.margin_ratio, 4),
    "liquidation_prices": {
        market: round(broker.get_liquidation_price(market), 2)
        for market in broker.positions
    },
}
status_dict["funding"] = {
    "total_paid": round(broker.total_funding, 4),
    "last_rate": last_funding_rate,  # from funding history
}
```

**Also add `GET /paper/{session_id}/equity-history`** endpoint:
- Returns the full equity curve from `paper_equity_history` table
- Includes `is_replay` flag for chart rendering

### Impact
- UI can display margin gauges, leverage, liquidation distances
- Users understand their margin utilization in real time
- Funding P&L is visible and trackable

---

## 10. Multi-Venue Paper Trading

### What

Support strategies that trade across multiple venues (Drift, Hyperliquid, Binance, etc.) with independent capital pools, per-venue fee/margin configs, and transfer mechanics. The backtest engine already supports this via `(venue, market)` position keys and `VenueAllocator` — paper trading needs to match.

### Current State

PaperBroker positions are keyed by market string only (`Dict[str, dict]`). There is a single `self.cash` balance. The `venue` parameter on order methods is accepted but ignored — all orders route to the same position pool.

BacktestContext uses `(venue, market)` tuple keys, `VenueAllocator` for per-venue balances, and the full transfer system with delays and costs. Multi-venue strategies like `MultiVenueFundingStrategy` use `ctx.get_funding_by_venue()` for cross-venue analysis.

### How

**1. Change PaperBroker position keying from `Dict[str, dict]` to `Dict[tuple, dict]`:**

```python
# Current:
self.positions: Dict[str, dict] = {}  # "SOL-PERP" -> position

# New:
self.positions: Dict[tuple, dict] = {}  # ("drift", "SOL-PERP") -> position
```

Every method that accesses positions must be updated to use `(venue, market)` keys: `_apply_fill()`, `process_candle()`, `cancel_all()`, equity computation, etc.

Add a `venue` field to each position dict:
```python
self.positions[("drift", "SOL-PERP")] = {
    "market": "SOL-PERP",
    "venue": "drift",
    "side": "long",
    "size": 10.0,
    "entry_price": 128.50,
    ...
}
```

For backward compatibility, methods that take `market` without `venue` default to `venue="default"`.

**2. Integrate VenueAllocator for per-venue capital:**

When `capital_allocation` is provided in the deploy request (e.g., `{"drift": 5000, "hyperliquid": 3000}`), create a `VenueAllocator` and route all cash operations through it:

```python
if capital_allocation:
    from ..execution.capital import VenueAllocator
    self._allocator = VenueAllocator(capital_allocation)
    self.cash = self._allocator.total_cash
else:
    self._allocator = None
    self.cash = initial_capital
```

Cash debits (fills, fees) route to the order's venue. Cash credits (PnL, position closes) route to the position's venue.

**3. Implement venue methods on LiveContext:**

LiveContext already inherits the venue method signatures from the base `ExecutionContext` ABC (`venue_balance`, `venue_balances`, `venue_positions`, `transfer`). Implement them by delegating to PaperBroker's allocator:

```python
def venue_balance(self, venue: str) -> float:
    if self._broker._allocator:
        return self._broker._allocator.available(venue)
    return self._broker.cash

def venue_balances(self) -> dict:
    if self._broker._allocator:
        return dict(self._broker._allocator._balances)
    return {"default": self._broker.cash}

def transfer(self, from_venue: str, to_venue: str, amount: float) -> bool:
    if not self._broker._allocator:
        return False
    t = self._broker._allocator.transfer(from_venue, to_venue, amount, int(time.time()))
    return t is not None

def venue_positions(self, venue: str) -> list:
    return [p for key, p in self._broker.positions.items() if key[0] == venue]
```

**4. Process transfers in the live loop:**

In `_run_live_session`, after processing candles, process arrived transfers:

```python
if hasattr(session.broker, '_allocator') and session.broker._allocator:
    session.broker._allocator.process_arrivals(int(time.time()))
    session.broker.cash = session.broker._allocator.total_cash
```

**5. Per-venue funding rate application:**

When applying funding rates, apply them to positions on the specific venue:

```python
for (venue, market), pos in broker.positions.items():
    # Get funding rate for this venue+market combination
    rates = store.query_venue_funding(market, start_ts, end_ts)
    for rate in rates:
        if rate.source == venue:  # Apply venue-specific rate
            payment = pos["size"] * rate.mark_price * rate.rate
            # Debit/credit the correct venue's balance
```

**6. Per-venue margin and liquidation:**

Each venue has different margin requirements (from `venue_config.py`). The risk guard should check margin per-venue:

```python
for (venue, market), pos in broker.positions.items():
    venue_config = get_venue_config(venue)
    notional = pos["size"] * mark_prices.get(market, pos["entry_price"])
    margin_required = notional * venue_config.maintenance_margin
    venue_equity = allocator.available(venue) + unrealized_pnl_on_venue
    if venue_equity <= margin_required:
        # Liquidate positions on this venue only
```

**7. Deploy request changes:**

Add `capital_allocation` to the deploy request (already exists in BacktestRequest):

```json
{
  "strategy_code": "...",
  "market": "SOL-PERP",
  "initial_capital": 10000,
  "capital_allocation": {
    "drift": 5000,
    "hyperliquid": 3000,
    "dydx": 2000
  }
}
```

When `capital_allocation` is provided, `initial_capital` is ignored (the sum of allocations is the total capital).

**8. Persistence changes:**

The `paper_positions` table already has a `market` column. Add a `venue` column:

```sql
ALTER TABLE paper_positions ADD COLUMN venue VARCHAR NOT NULL DEFAULT 'default';
```

Update position save/load to include venue. The `paper_sessions` table gets a `capital_allocation` column (JSON, nullable — NULL means single-venue).

### Impact
- Multi-venue strategies (funding arbitrage, cross-venue hedging) work in paper trading
- Per-venue P&L tracking shows which venues contribute/drag performance
- Transfer delays simulate real cross-venue capital movement (30min Drift→Hyperliquid)
- Each venue applies its own fee schedule and margin requirements
- Risk guard checks liquidation per-venue (isolated margin by default)

---

## Implementation Scope

### Files to Modify

| File | Changes |
|------|---------|
| `flint/execution/paper_broker.py` | Multi-venue position keying `(venue, market)`, integrate `VenueAllocator`, add `apply_funding()`, change default fill model to `SlippageFill`, add limit order timeouts, add `close_all_positions()`, per-venue fee models |
| `flint/execution/live_context.py` | Add store reference, implement `get_funding_rates()`, `get_funding_by_venue()`, `get_orderbook()`, `get_candles()`, `get_open_interest()`, `log()`, `venue_balance()`, `venue_balances()`, `venue_positions()`, `transfer()` |
| `flint/paper/engine.py` | Add funding application loop, inter-candle PnL updates, rolling history trim, session resumption, persist every candle, process venue transfers, multi-venue deploy |
| `flint/paper/session_store.py` | Add `paper_funding_payments` table operations, add venue column to positions, add method to get latest equity for recovery |
| `flint/paper/risk_guard.py` | Per-venue margin/liquidation checks instead of global |
| `flint/api/routes/paper.py` | Extend status response with margin/funding/venue data, add equity-history endpoint, accept capital_allocation in deploy |
| `flint/api/main.py` | Call `resume_sessions()` on startup |
| `flint/store.py` | Add `paper_funding_payments` table DDL, add venue column to `paper_positions` |

### Files to Create

| File | Purpose |
|------|---------|
| `tests/test_paper_funding.py` | Tests for funding rate application |
| `tests/test_paper_resume.py` | Tests for session resumption |
| `tests/test_live_context_data.py` | Tests for LiveContext data access methods |
| `tests/test_paper_multi_venue.py` | Tests for multi-venue positions, capital allocation, transfers |

### Estimated Tasks: 10 (one per gap/feature)

### Dependencies

```
Task 1 (funding) ──────────────┐
Task 2 (fills) ────────────────┤
Task 10 (multi-venue broker) ──┤──→ Task 3 (resumption) ──→ Task 9 (API)
Task 4 (LiveContext data) ─────┤
Task 5 (rolling history) ──────┘
Task 6 (latency) ── depends on Task 2
Task 7 (inter-candle PnL) ── independent
Task 8 (limit timeouts) ── independent
```

Tasks 1, 2, 4, 5, 7, 8, 10 are independent and can be parallelized.
Task 6 depends on Task 2 (fill model).
Task 3 depends on Tasks 1, 2, 10 (need funding + fills + multi-venue persisted before resumption).
Task 9 depends on all others (API exposes everything).
