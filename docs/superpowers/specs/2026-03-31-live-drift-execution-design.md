# Live Drift Execution Layer — Design Spec

> Sub-project 1 of Phase 1 (ROADMAP.md §1.1 + §1.2)
> Date: 2026-03-31

## Overview

Complete the execution layer for live trading on Drift Protocol. This covers order submission, fill tracking, position sync, and the base class scaffolding that future venues (Hyperliquid, etc.) will extend.

### Scope

**In scope:**
- `LiveExecutionContext` — abstract base for all live venue implementations
- `LiveDriftContext` — Drift-specific implementation via driftpy SDK
- `OrderTracker` — order state machine, rate limiting, retry logic
- `WalletAdapter` — signing abstraction (keypair built, browser wallet interface only)
- Config, store, and model additions for live trading
- Timer-based strategy tick loop (event-driven upgrades deferred to Sub-project 2: WebSocket feeds)

**Out of scope (later sub-projects):**
- WebSocket data feeds (Sub-project 2)
- Safety rails, kill switch, Telegram alerts (Sub-project 3)
- Backtest-to-live parity test (Sub-project 3)
- Browser wallet signing implementation (follow-up)

---

## 1. WalletAdapter

**File:** `flint/execution/wallet.py`

Abstract signing interface that decouples transaction signing from venue execution.

```python
class WalletAdapter(abc.ABC):
    @abc.abstractmethod
    async def sign_and_send(self, tx: Transaction, connection: AsyncClient) -> str:
        """Sign a transaction and send it. Returns tx signature."""

    @abc.abstractmethod
    def public_key(self) -> Pubkey:
        """Return the wallet's public key."""
```

### KeypairAdapter (built now)

- Loads keypair from `FLINT_PRIVATE_KEY` env var (base58 encoded)
- Falls back to `flint.yaml` → `live.private_key` (discouraged, logged as warning)
- Signs locally via `solders.Keypair`, sends via `AsyncClient.send_transaction()`
- Validates keypair on construction (fail fast)

### BrowserWalletAdapter (interface only, deferred)

- Will relay unsigned transactions to the React UI via WebSocket
- Frontend signs via `@solana/wallet-adapter` (Phantom, Brave, Solflare, etc.)
- Returns signed transaction to backend for submission
- Requires UI to be open — cannot run unattended
- Interface defined in this sub-project, implementation deferred

---

## 2. OrderTracker

**File:** `flint/execution/order_tracker.py`

Manages the lifecycle of every order from creation to terminal state.

### State Machine

```
pending → submitted → confirmed → filled
                   ↘ failed       ↘ partially_filled
                                  ↘ cancelled
                                  ↘ expired
```

| State | Meaning |
|-------|---------|
| `pending` | Created locally, queued for submission |
| `submitted` | Tx sent to RPC, awaiting on-chain confirmation |
| `confirmed` | On-chain in Drift orderbook, awaiting fill |
| `filled` | Fully filled |
| `partially_filled` | Partial fill received, remainder still open |
| `cancelled` | User-cancelled or auto-cancelled |
| `expired` | Timed out (limit order not filled within N bars) |
| `failed` | Retries exhausted |

State is added to `flint/models.py` as `OrderState` enum.

### Tracked Order Record

Each order tracked with:
- `flint_order_id: str` — UUID, assigned by ExecutionContext
- `venue_order_id: Optional[int]` — Drift's on-chain u32 order ID (set after confirmed)
- `tx_sig: Optional[str]` — Solana transaction signature (set after submitted)
- `state: OrderState` — current state
- `state_history: List[Tuple[OrderState, int]]` — (state, timestamp) transitions for audit
- `retry_count: int` — number of submission attempts
- `order: Order` — the original Flint order object
- `fills: List[Fill]` — fills received against this order

### Timeout Logic

- **Submission timeout**: No tx confirmation within 30s → cancel and retry (up to `max_retries`)
- **Limit order timeout**: No fill within `limit_order_timeout_bars` (configurable, default 10) → auto-cancel
- **Max retries**: Configurable via `live_max_retries` (default 3). After exhaustion, behavior depends on `live_on_order_failure`:
  - `"drop"` (default): Mark as `failed`, log warning, strategy continues
  - `"halt"`: Mark as `failed`, stop the strategy tick loop, require manual restart

### Rate Limiter

Token bucket algorithm:
- Max 10 orders per second to RPC
- Max 2 concurrent tx submissions (in-flight)
- Excess orders queue FIFO in `pending` state
- Rate limiter is per-venue (owned by OrderTracker instance)

### Callbacks

OrderTracker exposes callbacks that `LiveExecutionContext` hooks into:
- `on_fill(flint_order_id, fill)` — update positions, cash, persist to store
- `on_fail(flint_order_id, reason)` — log, optionally halt
- `on_cancel(flint_order_id)` — remove from active tracking
- `on_state_change(flint_order_id, old_state, new_state)` — logging, store persistence

### Polling Loop

OrderTracker runs an async polling loop (separate from strategy tick):
- Checks status of all `submitted` and `confirmed` orders
- Calls venue's `_poll_order_status()` for each
- Detects fills by comparing filled amounts
- Fires callbacks on state transitions
- Poll interval: 1-2 seconds (configurable)

---

## 3. LiveExecutionContext

**File:** `flint/execution/live_base.py`

Abstract base class for all live venue implementations. Sits between `ExecutionContext` ABC and venue-specific classes.

### Class Hierarchy

```
ExecutionContext (ABC)           ← unchanged, in context.py
  ├── BacktestContext            ← unchanged
  ├── LiveContext (paper)        ← unchanged
  └── LiveExecutionContext (NEW) ← this spec
       ├── LiveDriftContext      ← this spec
       └── LiveHyperliquidContext (Phase 2)
```

### Responsibilities

1. **Tick loop** — timer-based, fires every `live_tick_interval_s`
2. **Order routing** — strategy order calls → risk guards → OrderTracker → venue
3. **Position state** — local cache, periodically reconciled with venue
4. **Store persistence** — fills, equity snapshots, session metadata
5. **Lifecycle management** — start, stop, error handling

### Tick Loop

Each tick (every `live_tick_interval_s`, default 60s, matches candle resolution):

1. Fetch latest candle from FlintStore or REST API
2. Process any fills that arrived since last tick (from OrderTracker callbacks, already applied)
3. Call `strategy.on_candle(candle)`
4. Submit any new orders queued by the strategy (through OrderTracker)
5. Every N ticks (`live_position_sync_interval`, default 5): reconcile positions with venue
6. Persist equity snapshot to store

The tick loop runs in an asyncio event loop alongside the OrderTracker polling loop.

### Order Flow

```
strategy.market_order(market, side, size)
  → LiveExecutionContext.market_order()
    → RiskManager.evaluate(order, account, positions)  [reject if fails]
    → OrderTracker.submit(order)                       [queues as pending]
      → rate limiter gate
      → _place_order(order) [venue-specific, async]
      → tracker state: pending → submitted → ...
    → return order_id immediately (fire-and-forget)
```

### Position Reconciliation

- Positions maintained in `_positions_cache: Dict[Tuple[str, str], PositionInfo]` keyed by `(venue, market)`
- Updated on every fill callback (immediate)
- Full reconciliation via `_fetch_positions()` every N ticks
- On discrepancy: log warning with details (local vs venue state), trust venue as source of truth

### Abstract Methods (venue subclasses implement)

```python
async def _connect(self) -> None
async def _disconnect(self) -> None
async def _place_order(self, order: Order) -> Tuple[str, Optional[int]]
    # Returns (tx_sig, venue_order_id)
    # venue_order_id may be None if not immediately available from tx response.
    # If None, OrderTracker will call _poll_order_status() to discover it
    # by querying the user's open orders after tx confirmation.
async def _cancel_order(self, venue_order_id: int) -> bool
async def _fetch_positions(self) -> List[PositionInfo]
async def _fetch_balance(self) -> float
async def _poll_order_status(self, venue_order_id: int) -> OrderState
```

### Store Persistence

New tables in FlintStore (see §6):
- `live_sessions` — session metadata (strategy, market, config, start/stop times)
- `live_orders` — full order lifecycle with state transitions
- `live_fills` — every fill with tx signature for audit
- `live_equity_history` — periodic equity snapshots

---

## 4. LiveDriftContext

**File:** `flint/execution/drift_live.py` (rewrite, extends `LiveExecutionContext`)

### Connection (`_connect`)

1. Determine RPC URL:
   - `live_network: "devnet"` → `https://api.devnet.solana.com` (default)
   - `live_network: "mainnet"` → from `solana_rpc_url` config or `https://api.mainnet-beta.solana.com`
   - Direct override via `FLINT_RPC_URL` env var
2. Create `WalletAdapter` (KeypairAdapter from `FLINT_PRIVATE_KEY`)
3. Create `AsyncClient(rpc_url)`
4. Create `DriftClient(connection, wallet, env)` where env = `devnet` or `mainnet`
5. `await drift_client.subscribe()`
6. Initial position sync via `_fetch_positions()`
7. Log: network, public key, free collateral, open positions

### Order Placement (`_place_order`)

1. Convert `Order` to driftpy `OrderParams`:
   - Market index from `MARKET_TO_INDEX[order.market]`
   - Direction: `PositionDirection.LONG` or `SHORT` from `order.side`
   - Base amount: `to_drift_base(order.size)` from `precision.py`
   - Price: `to_drift_price(order.price)` for limit orders, 0 for market
   - Order type: `MARKET`, `LIMIT`, `TRIGGER_MARKET` (stop), `TRIGGER_LIMIT` (take profit)
2. Pre-flight check: `_fetch_balance()` → reject if insufficient collateral
3. Call `await drift_client.place_perp_order(order_params)`
4. Return `(tx_sig, order_id_from_tx)`

### Order Cancellation (`_cancel_order`)

1. Call `await drift_client.cancel_order(venue_order_id)`
2. Return `True` on success, `False` on failure (order already filled, not found)

### Position Sync (`_fetch_positions`)

1. `user.get_perp_positions()` — all open perp positions
2. For each non-zero position:
   - Reverse-map `market_index` → symbol via `INDEX_TO_MARKET`
   - Compute side from `base_asset_amount` sign
   - Compute unrealized PnL from oracle price: `user.get_perp_market_account(index)` → oracle data
3. Return `List[PositionInfo]`

### Balance Query (`_fetch_balance`)

1. `user.get_free_collateral()` → returns integer in QUOTE_PRECISION
2. Convert via `from_drift_base()` with QUOTE_PRECISION
3. Used by base class to reject orders that would exceed available margin

### Order Status Polling (`_poll_order_status`)

1. `user.get_order(venue_order_id)` → Drift order object
2. Map Drift status to `OrderState`:
   - Order exists, no fills → `confirmed`
   - `base_asset_amount_filled > 0` but `< base_asset_amount` → `partially_filled`
   - `base_asset_amount_filled == base_asset_amount` → `filled`
   - Order not found (cancelled/expired) → `cancelled`
3. On fill detection: construct `Fill` object with actual fill price, size, fee, tx_sig

### Drift-Specific Error Handling

| Error | Detection | Response |
|-------|-----------|----------|
| RPC failure | `ClientError` / timeout | Retry with exponential backoff (1s, 2s, 4s), max 3 |
| Tx dropped | No confirmation after 30s | Resubmit with higher compute unit price (+25%) |
| Insufficient funds | `get_free_collateral() < required` | Reject order immediately, log remaining collateral |
| Stale oracle | Oracle confidence interval too wide or age > 10s | Wait up to 10s for fresh oracle, reject if still stale |
| Account not found | First-time user on Drift | Include `initialize_user` instructions in first tx |

### Market Mapping

Keep existing `MARKET_TO_INDEX` dict. Add reverse:
```python
INDEX_TO_MARKET = {v: k for k, v in MARKET_TO_INDEX.items()}
```

---

## 5. Config Additions

**File:** `flint/config.py`

New fields in `FlintConfig`:

```python
# Live trading
live_network: str = "devnet"                  # "devnet" | "mainnet"
live_tick_interval_s: int = 60                # strategy tick interval
live_on_order_failure: str = "drop"           # "drop" | "halt"
live_max_retries: int = 3                     # order submission retries
live_position_sync_interval: int = 5          # reconcile every N ticks
live_limit_order_timeout_bars: int = 10       # auto-cancel unfilled limits
live_rate_limit_orders_per_sec: int = 10      # max orders per second
live_rate_limit_concurrent_tx: int = 2        # max in-flight transactions
live_wallet_mode: str = "keypair"             # "keypair" | "browser" (browser deferred)
```

Environment variable overrides follow existing pattern: `FLINT_LIVE_NETWORK=mainnet`, etc.

---

## 6. Store Additions

**File:** `flint/store.py`

### New Tables

```sql
CREATE TABLE IF NOT EXISTS live_sessions (
    session_id VARCHAR PRIMARY KEY,
    strategy_name VARCHAR NOT NULL,
    market VARCHAR NOT NULL,
    network VARCHAR NOT NULL,           -- "devnet" | "mainnet"
    venue VARCHAR NOT NULL DEFAULT 'drift',
    initial_capital DOUBLE,
    config_snapshot VARCHAR,            -- JSON of live config at start
    status VARCHAR DEFAULT 'running',   -- running | stopped | halted | error
    started_at BIGINT NOT NULL,
    stopped_at BIGINT
);

CREATE TABLE IF NOT EXISTS live_orders (
    order_id VARCHAR PRIMARY KEY,       -- Flint UUID
    session_id VARCHAR NOT NULL,
    venue_order_id INTEGER,             -- Drift on-chain order ID
    market VARCHAR NOT NULL,
    side VARCHAR NOT NULL,
    order_type VARCHAR NOT NULL,
    size DOUBLE NOT NULL,
    price DOUBLE,
    state VARCHAR NOT NULL,             -- OrderState enum value
    retry_count INTEGER DEFAULT 0,
    tx_sig VARCHAR,                     -- Solana tx signature
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    state_history VARCHAR               -- JSON array of [state, timestamp] pairs
);

CREATE TABLE IF NOT EXISTS live_fills (
    fill_id VARCHAR PRIMARY KEY,
    order_id VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    market VARCHAR NOT NULL,
    side VARCHAR NOT NULL,
    price DOUBLE NOT NULL,
    size DOUBLE NOT NULL,
    fee DOUBLE NOT NULL,
    tx_sig VARCHAR NOT NULL,            -- Solana tx signature for audit
    venue VARCHAR NOT NULL DEFAULT 'drift',
    is_partial BOOLEAN DEFAULT FALSE,
    ts BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS live_equity_history (
    session_id VARCHAR NOT NULL,
    ts BIGINT NOT NULL,
    equity DOUBLE NOT NULL,
    cash DOUBLE NOT NULL,
    unrealized_pnl DOUBLE NOT NULL,
    PRIMARY KEY (session_id, ts)
);
```

### New FlintStore Methods

- `create_live_session(session_id, strategy_name, market, network, venue, capital, config)`
- `update_live_session_status(session_id, status, stopped_at=None)`
- `upsert_live_order(order_id, session_id, market, side, order_type, size, price, state, ...)`
- `insert_live_fill(fill_id, order_id, session_id, market, side, price, size, fee, tx_sig, ...)`
- `insert_live_equity(session_id, ts, equity, cash, unrealized_pnl)`
- `get_live_session(session_id) → dict`
- `get_live_fills(session_id, market=None) → List[dict]`
- `get_live_equity_history(session_id) → List[dict]`

All methods follow existing pattern: `with self._lock:` + `self._conn.execute()`.

---

## 7. Model Additions

**File:** `flint/models.py`

```python
class OrderState(enum.Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"
```

This is distinct from the existing `OrderStatus` enum (which is simpler: PENDING, FILLED, PARTIALLY_FILLED, CANCELLED). `OrderState` captures the full on-chain lifecycle. `OrderStatus` remains for backtest/paper use.

---

## 8. Roadmap Update

Add to ROADMAP.md §1.1:

- Timer-based strategy tick loop (poll REST each tick, event-driven deferred to §1.3 WebSocket feeds)
- `WalletAdapter` abstraction with `KeypairAdapter` (built) and `BrowserWalletAdapter` (interface only, implementation deferred)

---

## Testing Strategy

All tests mocked — no RPC calls, no driftpy network access.

- **OrderTracker**: Unit test state machine transitions, timeout logic, rate limiter, retry behavior, callback firing
- **LiveExecutionContext**: Mock venue methods, test tick loop, order flow through risk guards, position reconciliation logic, store persistence
- **LiveDriftContext**: Mock `DriftClient`, test order param conversion, position parsing, error handling per scenario (stale oracle, dropped tx, insufficient funds)
- **WalletAdapter**: Mock signing, test KeypairAdapter loads key correctly, rejects invalid keys
- **Integration**: End-to-end test: create session → submit order → mock fill → verify store state

---

## Dependencies

- `driftpy` — Drift Protocol Python SDK (already optional dep)
- `solders` — Solana keypair/transaction handling (comes with driftpy)
- `solana` — Solana RPC client (comes with driftpy)
- No new dependencies required
