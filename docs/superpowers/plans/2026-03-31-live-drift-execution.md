# Live Drift Execution Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the execution layer for live trading on Drift Protocol — order submission, fill tracking, position sync, and the base class scaffolding for future venues.

**Architecture:** New `LiveExecutionContext` base class between `ExecutionContext` ABC and venue-specific implementations. `OrderTracker` manages order state machine with rate limiting. `LiveDriftContext` rewrites the existing stub to use driftpy SDK through the new base class. `WalletAdapter` abstraction decouples signing (keypair now, browser wallet later).

**Tech Stack:** Python 3.10+, driftpy SDK, solders, solana-py, asyncio, DuckDB (FlintStore)

---

### Task 1: Add `OrderState` enum to models

**Files:**
- Modify: `flint/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the test**

In `tests/test_models.py` (create if needed):

```python
"""Tests for model additions."""
from flint.models import OrderState


class TestOrderState:
    def test_all_states_exist(self):
        assert OrderState.PENDING.value == "pending"
        assert OrderState.SUBMITTED.value == "submitted"
        assert OrderState.CONFIRMED.value == "confirmed"
        assert OrderState.FILLED.value == "filled"
        assert OrderState.PARTIALLY_FILLED.value == "partially_filled"
        assert OrderState.CANCELLED.value == "cancelled"
        assert OrderState.EXPIRED.value == "expired"
        assert OrderState.FAILED.value == "failed"

    def test_terminal_states(self):
        terminal = {OrderState.FILLED, OrderState.CANCELLED, OrderState.EXPIRED, OrderState.FAILED}
        assert OrderState.PENDING not in terminal
        assert OrderState.SUBMITTED not in terminal
        assert OrderState.CONFIRMED not in terminal
        assert OrderState.FILLED in terminal
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py::TestOrderState -v`
Expected: FAIL — `ImportError: cannot import name 'OrderState'`

- [ ] **Step 3: Add OrderState enum**

Add to `flint/models.py` after the existing `OrderStatus` enum (around line 32):

```python
class OrderState(enum.Enum):
    """Full lifecycle state for live/on-chain orders.

    Distinct from OrderStatus (used by backtest/paper).
    Tracks the on-chain submission → confirmation → fill pipeline.
    """
    PENDING = "pending"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py::TestOrderState -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/models.py tests/test_models.py
git commit -m "feat: add OrderState enum for live order lifecycle tracking"
```

---

### Task 2: Add live trading config fields

**Files:**
- Modify: `flint/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the test**

Add to existing `tests/test_config.py` (or create):

```python
"""Tests for live trading config fields."""
from flint.config import FlintConfig


class TestLiveConfig:
    def test_defaults(self):
        cfg = FlintConfig()
        assert cfg.live_network == "devnet"
        assert cfg.live_tick_interval_s == 60
        assert cfg.live_on_order_failure == "drop"
        assert cfg.live_max_retries == 3
        assert cfg.live_position_sync_interval == 5
        assert cfg.live_limit_order_timeout_bars == 10
        assert cfg.live_rate_limit_orders_per_sec == 10
        assert cfg.live_rate_limit_concurrent_tx == 2
        assert cfg.live_wallet_mode == "keypair"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_LIVE_NETWORK", "mainnet")
        monkeypatch.setenv("FLINT_LIVE_MAX_RETRIES", "5")
        cfg = FlintConfig()
        assert cfg.live_network == "mainnet"
        assert cfg.live_max_retries == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::TestLiveConfig -v`
Expected: FAIL — `AttributeError: 'FlintConfig' object has no attribute 'live_network'`

- [ ] **Step 3: Add config fields**

Add to `flint/config.py` in the `FlintConfig` class, after the `solana_rpc_url` field (line 105):

```python
    # --- Live trading ---
    live_network: str = "devnet"
    live_tick_interval_s: int = 60
    live_on_order_failure: str = "drop"
    live_max_retries: int = 3
    live_position_sync_interval: int = 5
    live_limit_order_timeout_bars: int = 10
    live_rate_limit_orders_per_sec: int = 10
    live_rate_limit_concurrent_tx: int = 2
    live_wallet_mode: str = "keypair"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py::TestLiveConfig -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/config.py tests/test_config.py
git commit -m "feat: add live trading config fields (network, retries, rate limits)"
```

---

### Task 3: Add live trading tables to FlintStore

**Files:**
- Modify: `flint/store.py`
- Test: `tests/test_store_live.py`

- [ ] **Step 1: Write the test**

Create `tests/test_store_live.py`:

```python
"""Tests for live trading store tables and methods."""
import os
import time
import pytest

from flint.store import FlintStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    s = FlintStore(path=db_path)
    yield s
    s.close()


class TestLiveSessions:
    def test_create_and_get(self, store):
        store.create_live_session(
            session_id="s1",
            strategy_name="momentum",
            market="SOL-PERP",
            network="devnet",
            venue="drift",
            initial_capital=10000.0,
            config_snapshot='{"live_network": "devnet"}',
        )
        session = store.get_live_session("s1")
        assert session is not None
        assert session["strategy_name"] == "momentum"
        assert session["network"] == "devnet"
        assert session["status"] == "running"

    def test_update_status(self, store):
        store.create_live_session(
            session_id="s2",
            strategy_name="arb",
            market="BTC-PERP",
            network="mainnet",
            venue="drift",
            initial_capital=50000.0,
            config_snapshot="{}",
        )
        now = int(time.time())
        store.update_live_session_status("s2", "stopped", stopped_at=now)
        session = store.get_live_session("s2")
        assert session["status"] == "stopped"
        assert session["stopped_at"] == now

    def test_get_nonexistent(self, store):
        assert store.get_live_session("nope") is None


class TestLiveOrders:
    def test_upsert_and_query(self, store):
        now = int(time.time())
        store.upsert_live_order(
            order_id="ord-1",
            session_id="s1",
            venue_order_id=None,
            market="SOL-PERP",
            side="long",
            order_type="market",
            size=10.0,
            price=0.0,
            state="pending",
            retry_count=0,
            tx_sig=None,
            created_at=now,
            updated_at=now,
            state_history='[["pending", ' + str(now) + ']]',
        )
        orders = store.get_live_orders("s1")
        assert len(orders) == 1
        assert orders[0]["order_id"] == "ord-1"
        assert orders[0]["state"] == "pending"

    def test_upsert_updates_existing(self, store):
        now = int(time.time())
        store.upsert_live_order(
            order_id="ord-2", session_id="s1", venue_order_id=None,
            market="SOL-PERP", side="long", order_type="market",
            size=10.0, price=0.0, state="pending",
            retry_count=0, tx_sig=None, created_at=now, updated_at=now,
            state_history="[]",
        )
        store.upsert_live_order(
            order_id="ord-2", session_id="s1", venue_order_id=42,
            market="SOL-PERP", side="long", order_type="market",
            size=10.0, price=0.0, state="submitted",
            retry_count=1, tx_sig="abc123", created_at=now, updated_at=now + 1,
            state_history='[["pending", ' + str(now) + '], ["submitted", ' + str(now + 1) + ']]',
        )
        orders = store.get_live_orders("s1")
        assert len(orders) == 1
        assert orders[0]["state"] == "submitted"
        assert orders[0]["venue_order_id"] == 42


class TestLiveFills:
    def test_insert_and_query(self, store):
        now = int(time.time())
        store.insert_live_fill(
            fill_id="f1",
            order_id="ord-1",
            session_id="s1",
            market="SOL-PERP",
            side="long",
            price=150.25,
            size=10.0,
            fee=0.15,
            tx_sig="tx_abc",
            venue="drift",
            is_partial=False,
            ts=now,
        )
        fills = store.get_live_fills("s1")
        assert len(fills) == 1
        assert fills[0]["price"] == 150.25
        assert fills[0]["tx_sig"] == "tx_abc"

    def test_query_by_market(self, store):
        now = int(time.time())
        store.insert_live_fill(
            fill_id="f2", order_id="o1", session_id="s1",
            market="SOL-PERP", side="long", price=150.0, size=5.0,
            fee=0.05, tx_sig="tx1", venue="drift", is_partial=False, ts=now,
        )
        store.insert_live_fill(
            fill_id="f3", order_id="o2", session_id="s1",
            market="BTC-PERP", side="short", price=65000.0, size=0.1,
            fee=0.65, tx_sig="tx2", venue="drift", is_partial=False, ts=now,
        )
        sol_fills = store.get_live_fills("s1", market="SOL-PERP")
        assert len(sol_fills) == 1
        assert sol_fills[0]["market"] == "SOL-PERP"


class TestLiveEquityHistory:
    def test_insert_and_query(self, store):
        now = int(time.time())
        store.insert_live_equity("s1", now, 10500.0, 10200.0, 300.0)
        store.insert_live_equity("s1", now + 60, 10550.0, 10200.0, 350.0)
        history = store.get_live_equity_history("s1")
        assert len(history) == 2
        assert history[0]["equity"] == 10500.0
        assert history[1]["equity"] == 10550.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_store_live.py -v`
Expected: FAIL — `AttributeError: 'FlintStore' object has no attribute 'create_live_session'`

- [ ] **Step 3: Add table DDL and methods to store.py**

Add the table DDL strings after `_CREATE_PAPER_FUNDING_PAYMENTS` (around line 223) in `flint/store.py`:

```python
_CREATE_LIVE_SESSIONS = """
CREATE TABLE IF NOT EXISTS live_sessions (
    session_id      VARCHAR PRIMARY KEY,
    strategy_name   VARCHAR NOT NULL,
    market          VARCHAR NOT NULL,
    network         VARCHAR NOT NULL,
    venue           VARCHAR NOT NULL DEFAULT 'drift',
    initial_capital DOUBLE,
    config_snapshot VARCHAR,
    status          VARCHAR DEFAULT 'running',
    started_at      BIGINT NOT NULL,
    stopped_at      BIGINT
);
"""

_CREATE_LIVE_ORDERS = """
CREATE TABLE IF NOT EXISTS live_orders (
    order_id       VARCHAR PRIMARY KEY,
    session_id     VARCHAR NOT NULL,
    venue_order_id INTEGER,
    market         VARCHAR NOT NULL,
    side           VARCHAR NOT NULL,
    order_type     VARCHAR NOT NULL,
    size           DOUBLE NOT NULL,
    price          DOUBLE,
    state          VARCHAR NOT NULL,
    retry_count    INTEGER DEFAULT 0,
    tx_sig         VARCHAR,
    created_at     BIGINT NOT NULL,
    updated_at     BIGINT NOT NULL,
    state_history  VARCHAR
);
"""

_CREATE_LIVE_FILLS = """
CREATE TABLE IF NOT EXISTS live_fills (
    fill_id    VARCHAR PRIMARY KEY,
    order_id   VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    market     VARCHAR NOT NULL,
    side       VARCHAR NOT NULL,
    price      DOUBLE NOT NULL,
    size       DOUBLE NOT NULL,
    fee        DOUBLE NOT NULL,
    tx_sig     VARCHAR NOT NULL,
    venue      VARCHAR NOT NULL DEFAULT 'drift',
    is_partial BOOLEAN DEFAULT FALSE,
    ts         BIGINT NOT NULL
);
"""

_CREATE_LIVE_EQUITY_HISTORY = """
CREATE TABLE IF NOT EXISTS live_equity_history (
    session_id     VARCHAR NOT NULL,
    ts             BIGINT NOT NULL,
    equity         DOUBLE NOT NULL,
    cash           DOUBLE NOT NULL,
    unrealized_pnl DOUBLE NOT NULL,
    PRIMARY KEY (session_id, ts)
);
"""
```

Add the create calls at the end of `_create_tables()`, after line 309:

```python
        # Live trading persistence
        self._conn.execute(_CREATE_LIVE_SESSIONS)
        self._conn.execute(_CREATE_LIVE_ORDERS)
        self._conn.execute(_CREATE_LIVE_FILLS)
        self._conn.execute(_CREATE_LIVE_EQUITY_HISTORY)
```

Add the methods at the end of the `FlintStore` class, before `close()`:

```python
    # -- live trading persistence -----------------------------------------------

    def create_live_session(
        self, session_id: str, strategy_name: str, market: str,
        network: str, venue: str, initial_capital: float, config_snapshot: str,
    ) -> None:
        import time as _time
        with self._lock:
            self._conn.execute(
                "INSERT INTO live_sessions "
                "(session_id, strategy_name, market, network, venue, "
                "initial_capital, config_snapshot, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [session_id, strategy_name, market, network, venue,
                 initial_capital, config_snapshot, int(_time.time())],
            )

    def update_live_session_status(
        self, session_id: str, status: str, stopped_at: Optional[int] = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE live_sessions SET status = ?, stopped_at = ? WHERE session_id = ?",
                [status, stopped_at, session_id],
            )

    def get_live_session(self, session_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id, strategy_name, market, network, venue, "
                "initial_capital, config_snapshot, status, started_at, stopped_at "
                "FROM live_sessions WHERE session_id = ?",
                [session_id],
            ).fetchone()
        if not row:
            return None
        return {
            "session_id": row[0], "strategy_name": row[1], "market": row[2],
            "network": row[3], "venue": row[4], "initial_capital": row[5],
            "config_snapshot": row[6], "status": row[7],
            "started_at": row[8], "stopped_at": row[9],
        }

    def upsert_live_order(
        self, order_id: str, session_id: str, venue_order_id: Optional[int],
        market: str, side: str, order_type: str, size: float, price: float,
        state: str, retry_count: int, tx_sig: Optional[str],
        created_at: int, updated_at: int, state_history: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO live_orders "
                "(order_id, session_id, venue_order_id, market, side, order_type, "
                "size, price, state, retry_count, tx_sig, created_at, updated_at, state_history) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [order_id, session_id, venue_order_id, market, side, order_type,
                 size, price, state, retry_count, tx_sig, created_at, updated_at, state_history],
            )

    def get_live_orders(self, session_id: str, state: Optional[str] = None) -> list:
        sql = (
            "SELECT order_id, session_id, venue_order_id, market, side, order_type, "
            "size, price, state, retry_count, tx_sig, created_at, updated_at, state_history "
            "FROM live_orders WHERE session_id = ?"
        )
        params: list = [session_id]
        if state:
            sql += " AND state = ?"
            params.append(state)
        sql += " ORDER BY created_at ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {"order_id": r[0], "session_id": r[1], "venue_order_id": r[2],
             "market": r[3], "side": r[4], "order_type": r[5], "size": r[6],
             "price": r[7], "state": r[8], "retry_count": r[9], "tx_sig": r[10],
             "created_at": r[11], "updated_at": r[12], "state_history": r[13]}
            for r in rows
        ]

    def insert_live_fill(
        self, fill_id: str, order_id: str, session_id: str,
        market: str, side: str, price: float, size: float, fee: float,
        tx_sig: str, venue: str, is_partial: bool, ts: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO live_fills "
                "(fill_id, order_id, session_id, market, side, price, size, "
                "fee, tx_sig, venue, is_partial, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [fill_id, order_id, session_id, market, side, price, size,
                 fee, tx_sig, venue, is_partial, ts],
            )

    def get_live_fills(self, session_id: str, market: Optional[str] = None) -> list:
        sql = (
            "SELECT fill_id, order_id, session_id, market, side, price, size, "
            "fee, tx_sig, venue, is_partial, ts FROM live_fills WHERE session_id = ?"
        )
        params: list = [session_id]
        if market:
            sql += " AND market = ?"
            params.append(market)
        sql += " ORDER BY ts ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {"fill_id": r[0], "order_id": r[1], "session_id": r[2], "market": r[3],
             "side": r[4], "price": r[5], "size": r[6], "fee": r[7], "tx_sig": r[8],
             "venue": r[9], "is_partial": r[10], "ts": r[11]}
            for r in rows
        ]

    def insert_live_equity(
        self, session_id: str, ts: int, equity: float, cash: float, unrealized_pnl: float,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO live_equity_history "
                "(session_id, ts, equity, cash, unrealized_pnl) VALUES (?, ?, ?, ?, ?)",
                [session_id, ts, equity, cash, unrealized_pnl],
            )

    def get_live_equity_history(self, session_id: str) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, ts, equity, cash, unrealized_pnl "
                "FROM live_equity_history WHERE session_id = ? ORDER BY ts ASC",
                [session_id],
            ).fetchall()
        return [
            {"session_id": r[0], "ts": r[1], "equity": r[2],
             "cash": r[3], "unrealized_pnl": r[4]}
            for r in rows
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_store_live.py -v`
Expected: PASS (all 8 tests)

- [ ] **Step 5: Commit**

```bash
git add flint/store.py tests/test_store_live.py
git commit -m "feat: add live trading tables and methods to FlintStore"
```

---

### Task 4: WalletAdapter abstraction

**Files:**
- Create: `flint/execution/wallet.py`
- Test: `tests/test_wallet.py`

- [ ] **Step 1: Write the test**

Create `tests/test_wallet.py`:

```python
"""Tests for WalletAdapter — mocked, no real Solana signing."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestKeypairAdapter:
    def test_create_from_env(self, monkeypatch):
        # Use a valid base58 keypair (64 bytes encoded)
        # This is a throwaway devnet key for testing
        fake_key = "4wBqpZM9k69W87zdYRzM2FYF9czGSGarfKfabkFtEfGHiKA4VEbJNFMZ1eKQxZNrFBQTnJsEbYBThG8X8DSGA6DD"
        monkeypatch.setenv("FLINT_PRIVATE_KEY", fake_key)
        from flint.execution.wallet import KeypairAdapter
        adapter = KeypairAdapter()
        assert adapter.public_key is not None

    def test_create_from_param(self):
        fake_key = "4wBqpZM9k69W87zdYRzM2FYF9czGSGarfKfabkFtEfGHiKA4VEbJNFMZ1eKQxZNrFBQTnJsEbYBThG8X8DSGA6DD"
        from flint.execution.wallet import KeypairAdapter
        adapter = KeypairAdapter(private_key=fake_key)
        assert adapter.public_key is not None

    def test_no_key_raises(self, monkeypatch):
        monkeypatch.delenv("FLINT_PRIVATE_KEY", raising=False)
        from flint.execution.wallet import KeypairAdapter
        with pytest.raises(ValueError, match="No private key"):
            KeypairAdapter()

    def test_invalid_key_raises(self):
        from flint.execution.wallet import KeypairAdapter
        with pytest.raises(Exception):
            KeypairAdapter(private_key="not-a-valid-key")


class TestBrowserWalletAdapter:
    def test_interface_defined(self):
        from flint.execution.wallet import BrowserWalletAdapter
        # Should be importable but not instantiable (abstract)
        assert hasattr(BrowserWalletAdapter, "sign_and_send")
        assert hasattr(BrowserWalletAdapter, "public_key")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_wallet.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flint.execution.wallet'`

- [ ] **Step 3: Create wallet.py**

Create `flint/execution/wallet.py`:

```python
"""WalletAdapter — signing abstraction for live trading.

Decouples transaction signing from venue execution so different
wallet backends (local keypair, browser extension) can be swapped.
"""
from __future__ import annotations

import abc
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger("flint.wallet")


class WalletAdapter(abc.ABC):
    """Abstract base for transaction signing."""

    @abc.abstractmethod
    async def sign_and_send(self, tx, connection) -> str:
        """Sign a transaction and send it. Returns tx signature string."""
        ...

    @property
    @abc.abstractmethod
    def public_key(self):
        """Return the wallet's public key."""
        ...


class KeypairAdapter(WalletAdapter):
    """Signs transactions locally using a base58-encoded private key.

    Key source (in priority order):
    1. private_key parameter
    2. FLINT_PRIVATE_KEY environment variable
    """

    def __init__(self, private_key: str | None = None):
        from solders.keypair import Keypair  # type: ignore

        key = private_key or os.environ.get("FLINT_PRIVATE_KEY", "")
        if not key:
            raise ValueError(
                "No private key provided. Set FLINT_PRIVATE_KEY env var "
                "or pass private_key parameter."
            )

        self._keypair = Keypair.from_base58_string(key)
        logger.info("KeypairAdapter initialized (pubkey: %s)", self._keypair.pubkey())

    @property
    def public_key(self):
        return self._keypair.pubkey()

    @property
    def keypair(self):
        """Access the underlying Keypair for driftpy DriftClient."""
        return self._keypair

    async def sign_and_send(self, tx, connection) -> str:
        """Sign and send a transaction via the RPC connection."""
        result = await connection.send_transaction(tx, self._keypair)
        return str(result.value)


class BrowserWalletAdapter(WalletAdapter):
    """Placeholder for browser extension wallet signing (Phantom, Brave, etc.).

    Implementation deferred — will relay unsigned transactions to the React UI
    via WebSocket for signing with @solana/wallet-adapter.
    Requires UI to be open; cannot run unattended.
    """

    async def sign_and_send(self, tx, connection) -> str:
        raise NotImplementedError(
            "BrowserWalletAdapter is not yet implemented. "
            "Use KeypairAdapter with FLINT_PRIVATE_KEY for now."
        )

    @property
    def public_key(self):
        raise NotImplementedError("BrowserWalletAdapter is not yet implemented.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_wallet.py -v`
Expected: PASS (all 5 tests)

Note: `solders` is a dependency of `driftpy`. If not installed, tests will fail with ImportError. Ensure driftpy is installed: `pip install driftpy`

- [ ] **Step 5: Commit**

```bash
git add flint/execution/wallet.py tests/test_wallet.py
git commit -m "feat: add WalletAdapter abstraction with KeypairAdapter"
```

---

### Task 5: OrderTracker — state machine and tracked order record

**Files:**
- Create: `flint/execution/order_tracker.py`
- Test: `tests/test_order_tracker.py`

- [ ] **Step 1: Write the test**

Create `tests/test_order_tracker.py`:

```python
"""Tests for OrderTracker — state machine, rate limiting, timeouts."""
import time
import pytest
from unittest.mock import MagicMock

from flint.models import Order, OrderType, OrderState, Side, Fill
from flint.execution.order_tracker import OrderTracker, TrackedOrder


class TestTrackedOrder:
    def test_create(self):
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="test-1", ts=1000,
        )
        tracked = TrackedOrder(order=order)
        assert tracked.state == OrderState.PENDING
        assert tracked.flint_order_id == "test-1"
        assert tracked.venue_order_id is None
        assert tracked.tx_sig is None
        assert tracked.retry_count == 0
        assert len(tracked.state_history) == 1
        assert tracked.state_history[0][0] == OrderState.PENDING

    def test_transition_valid(self):
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="test-2", ts=1000,
        )
        tracked = TrackedOrder(order=order)
        tracked.transition(OrderState.SUBMITTED, tx_sig="tx_abc")
        assert tracked.state == OrderState.SUBMITTED
        assert tracked.tx_sig == "tx_abc"
        assert len(tracked.state_history) == 2

    def test_transition_to_confirmed(self):
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="test-3", ts=1000,
        )
        tracked = TrackedOrder(order=order)
        tracked.transition(OrderState.SUBMITTED, tx_sig="tx_abc")
        tracked.transition(OrderState.CONFIRMED, venue_order_id=42)
        assert tracked.state == OrderState.CONFIRMED
        assert tracked.venue_order_id == 42

    def test_is_terminal(self):
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="test-4", ts=1000,
        )
        tracked = TrackedOrder(order=order)
        assert tracked.is_terminal is False
        tracked.transition(OrderState.SUBMITTED)
        assert tracked.is_terminal is False
        tracked.transition(OrderState.CONFIRMED)
        assert tracked.is_terminal is False
        tracked.transition(OrderState.FILLED)
        assert tracked.is_terminal is True

    def test_add_fill(self):
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="test-5", ts=1000,
        )
        tracked = TrackedOrder(order=order)
        fill = Fill(
            market="SOL-PERP", side=Side.LONG, price=150.0,
            size=10.0, fee=0.15, ts=1001, order_id="test-5",
        )
        tracked.add_fill(fill)
        assert len(tracked.fills) == 1
        assert tracked.filled_size == 10.0


class TestOrderTracker:
    def test_submit_order(self):
        tracker = OrderTracker(max_retries=3, on_failure="drop")
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="ot-1", ts=1000,
        )
        tracked = tracker.submit(order)
        assert tracked.state == OrderState.PENDING
        assert "ot-1" in tracker.active_orders

    def test_get_order(self):
        tracker = OrderTracker(max_retries=3, on_failure="drop")
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="ot-2", ts=1000,
        )
        tracker.submit(order)
        assert tracker.get("ot-2") is not None
        assert tracker.get("nonexistent") is None

    def test_mark_submitted(self):
        tracker = OrderTracker(max_retries=3, on_failure="drop")
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="ot-3", ts=1000,
        )
        tracker.submit(order)
        tracker.mark_submitted("ot-3", tx_sig="sig_xyz")
        tracked = tracker.get("ot-3")
        assert tracked.state == OrderState.SUBMITTED
        assert tracked.tx_sig == "sig_xyz"

    def test_mark_filled_moves_to_completed(self):
        tracker = OrderTracker(max_retries=3, on_failure="drop")
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="ot-4", ts=1000,
        )
        tracker.submit(order)
        tracker.mark_submitted("ot-4", tx_sig="sig")
        tracker.mark_confirmed("ot-4", venue_order_id=99)
        fill = Fill(
            market="SOL-PERP", side=Side.LONG, price=150.0,
            size=10.0, fee=0.15, ts=1001, order_id="ot-4",
        )
        tracker.mark_filled("ot-4", fill)
        assert "ot-4" not in tracker.active_orders
        assert "ot-4" in tracker.completed_orders

    def test_mark_failed(self):
        tracker = OrderTracker(max_retries=3, on_failure="drop")
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="ot-5", ts=1000,
        )
        tracker.submit(order)
        tracker.mark_failed("ot-5", reason="retries exhausted")
        assert "ot-5" not in tracker.active_orders
        assert "ot-5" in tracker.completed_orders
        tracked = tracker.completed_orders["ot-5"]
        assert tracked.state == OrderState.FAILED

    def test_pending_submission_returns_pending_orders(self):
        tracker = OrderTracker(max_retries=3, on_failure="drop")
        o1 = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                   size=10.0, order_id="p-1", ts=1000)
        o2 = Order(market="BTC-PERP", side=Side.SHORT, order_type=OrderType.LIMIT,
                   size=0.1, price=65000.0, order_id="p-2", ts=1000)
        tracker.submit(o1)
        tracker.submit(o2)
        tracker.mark_submitted("p-1", tx_sig="sig1")
        pending = tracker.get_pending()
        assert len(pending) == 1
        assert pending[0].flint_order_id == "p-2"

    def test_callbacks_on_fill(self):
        fills_received = []
        tracker = OrderTracker(
            max_retries=3,
            on_failure="drop",
            on_fill=lambda oid, f: fills_received.append((oid, f)),
        )
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=10.0, order_id="cb-1", ts=1000)
        tracker.submit(order)
        tracker.mark_submitted("cb-1", tx_sig="sig")
        tracker.mark_confirmed("cb-1", venue_order_id=1)
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=150.0,
                    size=10.0, fee=0.15, ts=1001, order_id="cb-1")
        tracker.mark_filled("cb-1", fill)
        assert len(fills_received) == 1
        assert fills_received[0][0] == "cb-1"

    def test_callbacks_on_fail(self):
        fails_received = []
        tracker = OrderTracker(
            max_retries=3,
            on_failure="drop",
            on_fail=lambda oid, reason: fails_received.append((oid, reason)),
        )
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=10.0, order_id="cb-2", ts=1000)
        tracker.submit(order)
        tracker.mark_failed("cb-2", reason="timeout")
        assert len(fails_received) == 1
        assert fails_received[0][1] == "timeout"


class TestRateLimiter:
    def test_can_submit_within_limits(self):
        tracker = OrderTracker(
            max_retries=3, on_failure="drop",
            max_orders_per_sec=10, max_concurrent_tx=2,
        )
        assert tracker.can_submit() is True

    def test_concurrent_limit(self):
        tracker = OrderTracker(
            max_retries=3, on_failure="drop",
            max_orders_per_sec=10, max_concurrent_tx=2,
        )
        # Submit 2 and mark them as submitted (in-flight)
        for i in range(2):
            o = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=1.0, order_id=f"rl-{i}", ts=1000)
            tracker.submit(o)
            tracker.mark_submitted(f"rl-{i}", tx_sig=f"sig-{i}")
        # Now 2 are in-flight
        assert tracker.in_flight_count == 2
        assert tracker.can_submit() is False
        # Confirm one → frees a slot
        tracker.mark_confirmed("rl-0", venue_order_id=1)
        assert tracker.can_submit() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_order_tracker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flint.execution.order_tracker'`

- [ ] **Step 3: Create order_tracker.py**

Create `flint/execution/order_tracker.py`:

```python
"""OrderTracker — manages the full lifecycle of live orders.

Tracks orders from creation through submission, on-chain confirmation,
and fill detection. Handles rate limiting, timeouts, and retries.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from ..models import Fill, Order, OrderState

logger = logging.getLogger("flint.order_tracker")

_TERMINAL_STATES = {
    OrderState.FILLED,
    OrderState.CANCELLED,
    OrderState.EXPIRED,
    OrderState.FAILED,
}

_IN_FLIGHT_STATES = {
    OrderState.SUBMITTED,
}


@dataclass
class TrackedOrder:
    """Wraps a Flint Order with lifecycle tracking metadata."""

    order: Order
    state: OrderState = OrderState.PENDING
    venue_order_id: Optional[int] = None
    tx_sig: Optional[str] = None
    retry_count: int = 0
    state_history: List[Tuple[OrderState, int]] = field(default_factory=list)
    fills: List[Fill] = field(default_factory=list)
    created_at: int = field(default_factory=lambda: int(time.time()))

    def __post_init__(self):
        if not self.state_history:
            self.state_history.append((OrderState.PENDING, self.created_at))

    @property
    def flint_order_id(self) -> str:
        return self.order.order_id

    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL_STATES

    @property
    def filled_size(self) -> float:
        return sum(f.size for f in self.fills)

    def transition(
        self,
        new_state: OrderState,
        tx_sig: Optional[str] = None,
        venue_order_id: Optional[int] = None,
    ) -> None:
        """Transition to a new state with timestamp recording."""
        old_state = self.state
        self.state = new_state
        self.state_history.append((new_state, int(time.time())))
        if tx_sig is not None:
            self.tx_sig = tx_sig
        if venue_order_id is not None:
            self.venue_order_id = venue_order_id
        logger.debug(
            "Order %s: %s → %s", self.flint_order_id, old_state.value, new_state.value
        )

    def add_fill(self, fill: Fill) -> None:
        self.fills.append(fill)

    def to_state_history_json(self) -> str:
        """Serialize state history for store persistence."""
        import json
        return json.dumps([[s.value, ts] for s, ts in self.state_history])


class OrderTracker:
    """Manages active and completed orders with rate limiting.

    Args:
        max_retries: Max submission retry attempts before marking failed.
        on_failure: "drop" (log and continue) or "halt" (stop strategy loop).
        max_orders_per_sec: Rate limit for order submissions.
        max_concurrent_tx: Max in-flight (submitted, not yet confirmed) orders.
        on_fill: Callback(order_id, fill) when a fill is received.
        on_fail: Callback(order_id, reason) when an order fails.
        on_cancel: Callback(order_id) when an order is cancelled.
        on_state_change: Callback(order_id, old_state, new_state) on any transition.
    """

    def __init__(
        self,
        max_retries: int = 3,
        on_failure: str = "drop",
        max_orders_per_sec: int = 10,
        max_concurrent_tx: int = 2,
        on_fill: Optional[Callable] = None,
        on_fail: Optional[Callable] = None,
        on_cancel: Optional[Callable] = None,
        on_state_change: Optional[Callable] = None,
    ):
        self.max_retries = max_retries
        self.on_failure = on_failure
        self.max_orders_per_sec = max_orders_per_sec
        self.max_concurrent_tx = max_concurrent_tx

        self._on_fill = on_fill
        self._on_fail = on_fail
        self._on_cancel = on_cancel
        self._on_state_change = on_state_change

        self.active_orders: Dict[str, TrackedOrder] = {}
        self.completed_orders: Dict[str, TrackedOrder] = {}

        # Rate limiting: timestamps of recent submissions
        self._submission_timestamps: deque = deque()

    def submit(self, order: Order) -> TrackedOrder:
        """Add an order to tracking. Starts in PENDING state."""
        tracked = TrackedOrder(order=order)
        self.active_orders[order.order_id] = tracked
        logger.info("Tracking order %s: %s %s %.4f %s",
                     order.order_id, order.side.value, order.market,
                     order.size, order.order_type.value)
        return tracked

    def get(self, order_id: str) -> Optional[TrackedOrder]:
        """Get a tracked order by ID (checks active then completed)."""
        return self.active_orders.get(order_id) or self.completed_orders.get(order_id)

    def get_pending(self) -> List[TrackedOrder]:
        """Get all orders in PENDING state (ready for submission)."""
        return [
            t for t in self.active_orders.values()
            if t.state == OrderState.PENDING
        ]

    @property
    def in_flight_count(self) -> int:
        """Count of orders in SUBMITTED state (awaiting confirmation)."""
        return sum(1 for t in self.active_orders.values() if t.state in _IN_FLIGHT_STATES)

    def can_submit(self) -> bool:
        """Check if rate limits allow another submission."""
        if self.in_flight_count >= self.max_concurrent_tx:
            return False
        now = time.time()
        # Prune old timestamps
        while self._submission_timestamps and self._submission_timestamps[0] < now - 1.0:
            self._submission_timestamps.popleft()
        return len(self._submission_timestamps) < self.max_orders_per_sec

    def record_submission(self) -> None:
        """Record a submission timestamp for rate limiting."""
        self._submission_timestamps.append(time.time())

    def mark_submitted(self, order_id: str, tx_sig: str) -> None:
        """Mark order as submitted to RPC."""
        tracked = self.active_orders.get(order_id)
        if not tracked:
            return
        old = tracked.state
        tracked.transition(OrderState.SUBMITTED, tx_sig=tx_sig)
        self.record_submission()
        if self._on_state_change:
            self._on_state_change(order_id, old, OrderState.SUBMITTED)

    def mark_confirmed(self, order_id: str, venue_order_id: int) -> None:
        """Mark order as confirmed on-chain."""
        tracked = self.active_orders.get(order_id)
        if not tracked:
            return
        old = tracked.state
        tracked.transition(OrderState.CONFIRMED, venue_order_id=venue_order_id)
        if self._on_state_change:
            self._on_state_change(order_id, old, OrderState.CONFIRMED)

    def mark_filled(self, order_id: str, fill: Fill) -> None:
        """Record a fill and move to terminal state if fully filled."""
        tracked = self.active_orders.get(order_id)
        if not tracked:
            return
        tracked.add_fill(fill)
        old = tracked.state
        if tracked.filled_size >= tracked.order.size:
            tracked.transition(OrderState.FILLED)
            self._move_to_completed(order_id)
        else:
            tracked.transition(OrderState.PARTIALLY_FILLED)
        if self._on_fill:
            self._on_fill(order_id, fill)
        if self._on_state_change:
            self._on_state_change(order_id, old, tracked.state)

    def mark_cancelled(self, order_id: str) -> None:
        """Mark order as cancelled."""
        tracked = self.active_orders.get(order_id)
        if not tracked:
            return
        old = tracked.state
        tracked.transition(OrderState.CANCELLED)
        self._move_to_completed(order_id)
        if self._on_cancel:
            self._on_cancel(order_id)
        if self._on_state_change:
            self._on_state_change(order_id, old, OrderState.CANCELLED)

    def mark_expired(self, order_id: str) -> None:
        """Mark order as expired (limit order timeout)."""
        tracked = self.active_orders.get(order_id)
        if not tracked:
            return
        old = tracked.state
        tracked.transition(OrderState.EXPIRED)
        self._move_to_completed(order_id)
        if self._on_state_change:
            self._on_state_change(order_id, old, OrderState.EXPIRED)

    def mark_failed(self, order_id: str, reason: str) -> None:
        """Mark order as failed (retries exhausted)."""
        tracked = self.active_orders.get(order_id)
        if not tracked:
            return
        old = tracked.state
        tracked.transition(OrderState.FAILED)
        self._move_to_completed(order_id)
        logger.warning("Order %s failed: %s", order_id, reason)
        if self._on_fail:
            self._on_fail(order_id, reason)
        if self._on_state_change:
            self._on_state_change(order_id, old, OrderState.FAILED)

    def increment_retry(self, order_id: str) -> bool:
        """Increment retry count. Returns False if max retries exceeded."""
        tracked = self.active_orders.get(order_id)
        if not tracked:
            return False
        tracked.retry_count += 1
        # Reset to pending for re-submission
        tracked.transition(OrderState.PENDING)
        if tracked.retry_count > self.max_retries:
            self.mark_failed(order_id, reason=f"max retries ({self.max_retries}) exceeded")
            return False
        logger.info("Order %s retry %d/%d", order_id, tracked.retry_count, self.max_retries)
        return True

    def _move_to_completed(self, order_id: str) -> None:
        """Move order from active to completed tracking."""
        tracked = self.active_orders.pop(order_id, None)
        if tracked:
            self.completed_orders[order_id] = tracked
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_order_tracker.py -v`
Expected: PASS (all 14 tests)

- [ ] **Step 5: Commit**

```bash
git add flint/execution/order_tracker.py tests/test_order_tracker.py
git commit -m "feat: add OrderTracker with state machine, rate limiting, callbacks"
```

---

### Task 6: LiveExecutionContext base class

**Files:**
- Create: `flint/execution/live_base.py`
- Test: `tests/test_live_base.py`

- [ ] **Step 1: Write the test**

Create `tests/test_live_base.py`:

```python
"""Tests for LiveExecutionContext base class — uses a mock venue implementation."""
import asyncio
import time
import pytest
from unittest.mock import MagicMock, AsyncMock

from flint.models import (
    AccountState, Candle, Fill, Order, OrderType, OrderState,
    PositionInfo, Side,
)
from flint.execution.live_base import LiveExecutionContext
from flint.execution.order_tracker import OrderTracker
from flint.risk.guards import RiskManager


class MockVenueContext(LiveExecutionContext):
    """Concrete implementation for testing the base class."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._connected = False
        self._placed_orders = []
        self._cancelled_orders = []
        self._mock_positions = []
        self._mock_balance = 10000.0
        self._mock_order_counter = 0

    async def _connect(self):
        self._connected = True

    async def _disconnect(self):
        self._connected = False

    async def _place_order(self, order):
        self._mock_order_counter += 1
        self._placed_orders.append(order)
        return (f"tx_{self._mock_order_counter}", self._mock_order_counter)

    async def _cancel_order(self, venue_order_id):
        self._cancelled_orders.append(venue_order_id)
        return True

    async def _fetch_positions(self):
        return self._mock_positions

    async def _fetch_balance(self):
        return self._mock_balance

    async def _poll_order_status(self, venue_order_id):
        return OrderState.CONFIRMED


class TestLifecycle:
    def test_create(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        assert ctx.account.cash == 10000.0
        assert ctx.positions == []
        assert ctx.pending_orders == []

    def test_connect(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        asyncio.get_event_loop().run_until_complete(ctx.connect())
        assert ctx._connected is True

    def test_disconnect(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(ctx.connect())
        loop.run_until_complete(ctx.disconnect())
        assert ctx._connected is False


class TestOrderFlow:
    def test_market_order_returns_id(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        assert oid.startswith("live-")
        assert len(ctx._tracker.active_orders) == 1

    def test_limit_order(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        oid = ctx.limit_order("SOL-PERP", Side.LONG, 10.0, 150.0)
        tracked = ctx._tracker.get(oid)
        assert tracked is not None
        assert tracked.order.order_type == OrderType.LIMIT

    def test_stop_order(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        oid = ctx.stop_order("SOL-PERP", Side.SHORT, 5.0, 140.0)
        tracked = ctx._tracker.get(oid)
        assert tracked.order.order_type == OrderType.STOP_LOSS

    def test_take_profit_order(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        oid = ctx.take_profit_order("SOL-PERP", Side.LONG, 5.0, 160.0)
        tracked = ctx._tracker.get(oid)
        assert tracked.order.order_type == OrderType.TAKE_PROFIT

    def test_cancel_pending_order(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        result = ctx.cancel(oid)
        assert result is True
        tracked = ctx._tracker.get(oid)
        assert tracked.state == OrderState.CANCELLED

    def test_cancel_nonexistent(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        result = ctx.cancel("nonexistent-id")
        assert result is False

    def test_cancel_all(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        ctx.market_order("BTC-PERP", Side.SHORT, 0.1)
        count = ctx.cancel_all()
        assert count == 2

    def test_cancel_all_by_market(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        ctx.market_order("BTC-PERP", Side.SHORT, 0.1)
        count = ctx.cancel_all(market="SOL-PERP")
        assert count == 1
        assert len(ctx._tracker.active_orders) == 1


class TestRiskGuardIntegration:
    def test_order_rejected_by_risk_guard(self):
        from flint.risk.guards import MaxOpenPositions
        rm = RiskManager(guards=[MaxOpenPositions(max_positions=0)])
        ctx = MockVenueContext(
            venue="test", initial_capital=10000.0, risk_manager=rm,
        )
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        # Order should be rejected — not tracked
        assert oid == ""


class TestPositionCache:
    def test_position_lookup(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        ctx._positions_cache[("test", "SOL-PERP")] = PositionInfo(
            market="SOL-PERP", side=Side.LONG, size=10.0,
            entry_price=150.0, venue="test",
        )
        pos = ctx.position("SOL-PERP", venue="test")
        assert pos is not None
        assert pos.size == 10.0

    def test_positions_list(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        ctx._positions_cache[("test", "SOL-PERP")] = PositionInfo(
            market="SOL-PERP", side=Side.LONG, size=10.0,
            entry_price=150.0, venue="test",
        )
        assert len(ctx.positions) == 1


class TestAccountState:
    def test_account_with_positions(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        ctx._positions_cache[("test", "SOL-PERP")] = PositionInfo(
            market="SOL-PERP", side=Side.LONG, size=10.0,
            entry_price=150.0, unrealized_pnl=50.0, venue="test",
        )
        account = ctx.account
        assert account.cash == 10000.0
        assert account.unrealized_pnl == 50.0
        assert account.equity == 10050.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_live_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flint.execution.live_base'`

- [ ] **Step 3: Create live_base.py**

Create `flint/execution/live_base.py`:

```python
"""LiveExecutionContext — abstract base class for all live venue implementations.

Sits between ExecutionContext ABC and venue-specific implementations
(LiveDriftContext, LiveHyperliquidContext, etc.).

Provides:
- Order routing through risk guards and OrderTracker
- Timer-based strategy tick loop
- Position state management with periodic venue reconciliation
- Store persistence for audit trail
"""
from __future__ import annotations

import abc
import asyncio
import logging
import time
import uuid
from typing import Dict, List, Optional, Tuple

from ..models import (
    AccountState, Candle, Fill, Order, OrderState, OrderType,
    PositionInfo, Side,
)
from .context import ExecutionContext
from .order_tracker import OrderTracker
from ..risk.guards import RiskManager

logger = logging.getLogger("flint.live")


class LiveExecutionContext(ExecutionContext, abc.ABC):
    """Base class for live venue execution contexts.

    Subclasses implement the 7 abstract methods for venue-specific operations.
    This base handles order lifecycle, risk checks, position tracking, and persistence.
    """

    def __init__(
        self,
        venue: str = "default",
        initial_capital: float = 0.0,
        risk_manager: Optional[RiskManager] = None,
        store=None,
        session_id: str = "",
        max_retries: int = 3,
        on_failure: str = "drop",
        max_orders_per_sec: int = 10,
        max_concurrent_tx: int = 2,
        tick_interval_s: int = 60,
        position_sync_interval: int = 5,
    ):
        self._venue = venue
        self._initial_capital = initial_capital
        self._cash = initial_capital
        self._risk_manager = risk_manager
        self._store = store
        self._session_id = session_id or str(uuid.uuid4())[:8]
        self._tick_interval_s = tick_interval_s
        self._position_sync_interval = position_sync_interval

        self._positions_cache: Dict[Tuple[str, str], PositionInfo] = {}
        self._current_candle: Optional[Candle] = None
        self._fills: List[Fill] = []
        self._order_counter = 0
        self._tick_count = 0
        self._running = False

        # OrderTracker handles state machine, rate limiting, retries
        self._tracker = OrderTracker(
            max_retries=max_retries,
            on_failure=on_failure,
            max_orders_per_sec=max_orders_per_sec,
            max_concurrent_tx=max_concurrent_tx,
            on_fill=self._handle_fill,
            on_fail=self._handle_fail,
        )

    # --- Abstract methods (venue subclasses implement) ---

    @abc.abstractmethod
    async def _connect(self) -> None:
        """Initialize venue SDK/API connection."""
        ...

    @abc.abstractmethod
    async def _disconnect(self) -> None:
        """Cleanup venue connection."""
        ...

    @abc.abstractmethod
    async def _place_order(self, order: Order) -> Tuple[str, Optional[int]]:
        """Submit order to venue. Returns (tx_sig, venue_order_id)."""
        ...

    @abc.abstractmethod
    async def _cancel_order(self, venue_order_id: int) -> bool:
        """Cancel an order on venue. Returns True if successful."""
        ...

    @abc.abstractmethod
    async def _fetch_positions(self) -> List[PositionInfo]:
        """Fetch current positions from venue."""
        ...

    @abc.abstractmethod
    async def _fetch_balance(self) -> float:
        """Fetch available balance/collateral from venue."""
        ...

    @abc.abstractmethod
    async def _poll_order_status(self, venue_order_id: int) -> OrderState:
        """Poll venue for order status. Returns current OrderState."""
        ...

    # --- Lifecycle ---

    async def connect(self) -> None:
        """Connect to venue and sync initial state."""
        await self._connect()
        positions = await self._fetch_positions()
        self._reconcile_positions(positions)
        self._cash = await self._fetch_balance()
        logger.info("Connected to %s — %d positions, balance=%.2f",
                     self._venue, len(self._positions_cache), self._cash)

    async def disconnect(self) -> None:
        """Stop tick loop and disconnect from venue."""
        self._running = False
        await self._disconnect()
        logger.info("Disconnected from %s", self._venue)

    # --- ExecutionContext interface ---

    @property
    def account(self) -> AccountState:
        unrealized = sum(p.unrealized_pnl for p in self._positions_cache.values())
        return AccountState(
            equity=self._cash + unrealized,
            cash=self._cash,
            unrealized_pnl=unrealized,
        )

    @property
    def positions(self) -> List[PositionInfo]:
        return list(self._positions_cache.values())

    @property
    def pending_orders(self) -> List[Order]:
        return [t.order for t in self._tracker.active_orders.values()]

    @property
    def current_candle(self) -> Optional[Candle]:
        return self._current_candle

    @property
    def timestamp(self) -> int:
        return int(time.time())

    def _next_id(self) -> str:
        self._order_counter += 1
        return f"live-{self._session_id}-{self._order_counter}"

    def market_order(self, market, side, size, reduce_only=False, tag="", venue="default"):
        order = Order(
            market=market, side=side, order_type=OrderType.MARKET,
            size=size, order_id=self._next_id(), ts=self.timestamp,
            venue=venue if venue != "default" else self._venue,
        )
        return self._submit_order(order)

    def limit_order(self, market, side, size, price, reduce_only=False, tag="", venue="default"):
        order = Order(
            market=market, side=side, order_type=OrderType.LIMIT,
            size=size, price=price, order_id=self._next_id(), ts=self.timestamp,
            venue=venue if venue != "default" else self._venue,
        )
        return self._submit_order(order)

    def stop_order(self, market, side, size, trigger_price, tag="", venue="default"):
        order = Order(
            market=market, side=side, order_type=OrderType.STOP_LOSS,
            size=size, price=trigger_price, order_id=self._next_id(), ts=self.timestamp,
            venue=venue if venue != "default" else self._venue,
        )
        return self._submit_order(order)

    def take_profit_order(self, market, side, size, trigger_price, tag="", venue="default"):
        order = Order(
            market=market, side=side, order_type=OrderType.TAKE_PROFIT,
            size=size, price=trigger_price, order_id=self._next_id(), ts=self.timestamp,
            venue=venue if venue != "default" else self._venue,
        )
        return self._submit_order(order)

    def cancel(self, order_id):
        tracked = self._tracker.get(order_id)
        if not tracked or tracked.is_terminal:
            return False
        self._tracker.mark_cancelled(order_id)
        return True

    def cancel_all(self, market=None):
        count = 0
        for oid in list(self._tracker.active_orders.keys()):
            tracked = self._tracker.active_orders[oid]
            if market and tracked.order.market != market:
                continue
            self._tracker.mark_cancelled(oid)
            count += 1
        return count

    # --- Internal order routing ---

    def _submit_order(self, order: Order) -> str:
        """Route order through risk guards → OrderTracker."""
        if self._risk_manager:
            result = self._risk_manager.evaluate(
                order, self.account, self.positions,
            )
            if result is None:
                logger.info("Order %s rejected by risk guards", order.order_id)
                return ""
            order = result
        self._tracker.submit(order)
        return order.order_id

    # --- Submission loop (called by tick loop or externally) ---

    async def submit_pending_orders(self) -> List[Fill]:
        """Submit all pending orders to venue via OrderTracker.

        Called by the tick loop after strategy.on_candle().
        """
        pending = self._tracker.get_pending()
        fills = []

        for tracked in pending:
            if not self._tracker.can_submit():
                logger.debug("Rate limit hit, deferring remaining orders")
                break

            try:
                tx_sig, venue_order_id = await self._place_order(tracked.order)
                self._tracker.mark_submitted(tracked.flint_order_id, tx_sig=tx_sig)
                if venue_order_id is not None:
                    self._tracker.mark_confirmed(tracked.flint_order_id, venue_order_id=venue_order_id)
                self._persist_order(tracked)
            except Exception as e:
                logger.error("Order %s submission failed: %s", tracked.flint_order_id, e)
                if not self._tracker.increment_retry(tracked.flint_order_id):
                    # max retries exceeded — already marked failed by increment_retry
                    pass

        return fills

    # --- Callbacks ---

    def _handle_fill(self, order_id: str, fill: Fill) -> None:
        """Called by OrderTracker when a fill is received."""
        self._fills.append(fill)
        self._update_position_from_fill(fill)
        self._persist_fill(fill)
        logger.info("Fill: %s %s %.4f @ %.2f (fee=%.4f)",
                     fill.side.value, fill.market, fill.size, fill.price, fill.fee)

    def _handle_fail(self, order_id: str, reason: str) -> None:
        """Called by OrderTracker when an order fails."""
        logger.warning("Order %s failed: %s (policy=%s)",
                       order_id, reason, self._tracker.on_failure)
        if self._tracker.on_failure == "halt":
            self._running = False
            logger.error("Strategy halted due to order failure")

    # --- Position management ---

    def _reconcile_positions(self, venue_positions: List[PositionInfo]) -> None:
        """Update local cache from venue positions (venue is source of truth)."""
        new_cache: Dict[Tuple[str, str], PositionInfo] = {}
        for pos in venue_positions:
            key = (pos.venue or self._venue, pos.market)
            new_cache[key] = pos

        # Log discrepancies
        for key, local_pos in self._positions_cache.items():
            if key not in new_cache:
                logger.warning("Position %s disappeared from venue", key)
            elif new_cache[key].size != local_pos.size:
                logger.warning("Position %s size mismatch: local=%.4f venue=%.4f",
                             key, local_pos.size, new_cache[key].size)

        self._positions_cache = new_cache

    def _update_position_from_fill(self, fill: Fill) -> None:
        """Update local position cache when a fill arrives."""
        venue = fill.venue or self._venue
        key = (venue, fill.market)
        existing = self._positions_cache.get(key)

        if existing is None:
            # New position
            self._positions_cache[key] = PositionInfo(
                market=fill.market,
                side=fill.side,
                size=fill.size,
                entry_price=fill.price,
                entry_ts=fill.ts,
                venue=venue,
            )
        else:
            # Update existing — simplified; full logic would handle flips
            if existing.side == fill.side:
                # Adding to position
                total_size = existing.size + fill.size
                avg_price = (
                    (existing.entry_price * existing.size + fill.price * fill.size)
                    / total_size
                )
                self._positions_cache[key] = PositionInfo(
                    market=fill.market,
                    side=existing.side,
                    size=total_size,
                    entry_price=avg_price,
                    entry_ts=existing.entry_ts,
                    venue=venue,
                )
            else:
                # Reducing position
                if fill.size >= existing.size:
                    # Position closed
                    del self._positions_cache[key]
                else:
                    self._positions_cache[key] = PositionInfo(
                        market=fill.market,
                        side=existing.side,
                        size=existing.size - fill.size,
                        entry_price=existing.entry_price,
                        entry_ts=existing.entry_ts,
                        venue=venue,
                    )

    # --- Store persistence ---

    def _persist_order(self, tracked) -> None:
        """Write order state to FlintStore."""
        if not self._store:
            return
        try:
            self._store.upsert_live_order(
                order_id=tracked.flint_order_id,
                session_id=self._session_id,
                venue_order_id=tracked.venue_order_id,
                market=tracked.order.market,
                side=tracked.order.side.value,
                order_type=tracked.order.order_type.value,
                size=tracked.order.size,
                price=tracked.order.price,
                state=tracked.state.value,
                retry_count=tracked.retry_count,
                tx_sig=tracked.tx_sig,
                created_at=tracked.created_at,
                updated_at=int(time.time()),
                state_history=tracked.to_state_history_json(),
            )
        except Exception as e:
            logger.error("Failed to persist order %s: %s", tracked.flint_order_id, e)

    def _persist_fill(self, fill: Fill) -> None:
        """Write fill to FlintStore."""
        if not self._store:
            return
        try:
            self._store.insert_live_fill(
                fill_id=str(uuid.uuid4())[:12],
                order_id=fill.order_id,
                session_id=self._session_id,
                market=fill.market,
                side=fill.side.value,
                price=fill.price,
                size=fill.size,
                fee=fill.fee,
                tx_sig=fill.tx_sig,
                venue=fill.venue,
                is_partial=fill.is_partial,
                ts=fill.ts,
            )
        except Exception as e:
            logger.error("Failed to persist fill: %s", e)

    def _persist_equity(self) -> None:
        """Write equity snapshot to FlintStore."""
        if not self._store:
            return
        try:
            acct = self.account
            self._store.insert_live_equity(
                self._session_id, int(time.time()),
                acct.equity, acct.cash, acct.unrealized_pnl,
            )
        except Exception as e:
            logger.error("Failed to persist equity: %s", e)

    def log(self, message: str) -> None:
        logger.info("[%s] %s", self._session_id, message)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_live_base.py -v`
Expected: PASS (all 14 tests)

- [ ] **Step 5: Commit**

```bash
git add flint/execution/live_base.py tests/test_live_base.py
git commit -m "feat: add LiveExecutionContext base class with order routing and position tracking"
```

---

### Task 7: LiveDriftContext rewrite

**Files:**
- Modify: `flint/execution/drift_live.py` (full rewrite)
- Test: `tests/test_drift_live.py` (extend)

- [ ] **Step 1: Write the tests**

Replace `tests/test_drift_live.py` entirely:

```python
"""Tests for LiveDriftContext — mocked, no real Drift connection."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock

from flint.models import Side, OrderType, OrderState, PositionInfo


# Helper to run async
def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestLiveDriftContextImport:
    def test_import_fails_without_driftpy(self):
        with patch("flint.execution.drift_live._check_driftpy", return_value=False):
            from flint.execution.drift_live import LiveDriftContext
            with pytest.raises(ImportError, match="driftpy is required"):
                LiveDriftContext(private_key="fake_key")

    def test_no_key_raises(self):
        with patch("flint.execution.drift_live._check_driftpy", return_value=True):
            from flint.execution.drift_live import LiveDriftContext
            with pytest.raises(ValueError, match="No private key"):
                LiveDriftContext(private_key="")

    def test_creates_with_key(self):
        with patch("flint.execution.drift_live._check_driftpy", return_value=True):
            with patch("flint.execution.drift_live.KeypairAdapter") as MockAdapter:
                MockAdapter.return_value = MagicMock()
                from flint.execution.drift_live import LiveDriftContext
                ctx = LiveDriftContext(private_key="fake_base58_key_for_testing")
                assert ctx.timestamp > 0
                assert ctx.positions == []


class TestMarketMapping:
    def test_market_to_index(self):
        from flint.execution.drift_live import MARKET_TO_INDEX, INDEX_TO_MARKET
        assert MARKET_TO_INDEX["SOL-PERP"] == 0
        assert MARKET_TO_INDEX["BTC-PERP"] == 1
        assert INDEX_TO_MARKET[0] == "SOL-PERP"
        assert INDEX_TO_MARKET[1] == "BTC-PERP"


class TestOrderMethods:
    def _make_ctx(self):
        with patch("flint.execution.drift_live._check_driftpy", return_value=True):
            with patch("flint.execution.drift_live.KeypairAdapter") as MockAdapter:
                MockAdapter.return_value = MagicMock()
                from flint.execution.drift_live import LiveDriftContext
                return LiveDriftContext(private_key="fake_key", network="devnet")

    def test_market_order_queues(self):
        ctx = self._make_ctx()
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        assert oid != ""
        assert len(ctx._tracker.active_orders) == 1

    def test_limit_order_queues(self):
        ctx = self._make_ctx()
        oid = ctx.limit_order("SOL-PERP", Side.LONG, 10.0, 150.0)
        tracked = ctx._tracker.get(oid)
        assert tracked.order.order_type == OrderType.LIMIT
        assert tracked.order.price == 150.0

    def test_cancel_order(self):
        ctx = self._make_ctx()
        oid = ctx.limit_order("SOL-PERP", Side.LONG, 10.0, 150.0)
        assert ctx.cancel(oid) is True
        tracked = ctx._tracker.get(oid)
        assert tracked.state == OrderState.CANCELLED

    def test_cancel_all(self):
        ctx = self._make_ctx()
        ctx.market_order("SOL-PERP", Side.LONG, 5.0)
        ctx.market_order("BTC-PERP", Side.SHORT, 0.1)
        assert ctx.cancel_all() == 2


class TestNetworkConfig:
    def test_devnet_rpc(self):
        with patch("flint.execution.drift_live._check_driftpy", return_value=True):
            with patch("flint.execution.drift_live.KeypairAdapter") as MockAdapter:
                MockAdapter.return_value = MagicMock()
                from flint.execution.drift_live import LiveDriftContext
                ctx = LiveDriftContext(private_key="fake", network="devnet")
                assert "devnet" in ctx._rpc_url

    def test_mainnet_rpc(self):
        with patch("flint.execution.drift_live._check_driftpy", return_value=True):
            with patch("flint.execution.drift_live.KeypairAdapter") as MockAdapter:
                MockAdapter.return_value = MagicMock()
                from flint.execution.drift_live import LiveDriftContext
                ctx = LiveDriftContext(private_key="fake", network="mainnet")
                assert "mainnet" in ctx._rpc_url

    def test_rpc_url_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_RPC_URL", "https://custom-rpc.example.com")
        with patch("flint.execution.drift_live._check_driftpy", return_value=True):
            with patch("flint.execution.drift_live.KeypairAdapter") as MockAdapter:
                MockAdapter.return_value = MagicMock()
                from flint.execution.drift_live import LiveDriftContext
                ctx = LiveDriftContext(private_key="fake", network="devnet")
                assert ctx._rpc_url == "https://custom-rpc.example.com"


class TestCLILiveCommand:
    def test_live_help(self):
        from typer.testing import CliRunner
        from flint.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["live", "--help"])
        assert result.exit_code == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_drift_live.py -v`
Expected: FAIL — tests reference new constructor params and `KeypairAdapter` import

- [ ] **Step 3: Rewrite drift_live.py**

Replace `flint/execution/drift_live.py` entirely:

```python
"""LiveDriftContext — ExecutionContext for real order execution on Drift Protocol.

Requires `driftpy` package: pip install driftpy
Uses the same strategy code as backtest — backtest-live symmetry.

Environment variables:
    FLINT_PRIVATE_KEY: Base58-encoded Solana private key
    FLINT_RPC_URL: Solana RPC endpoint (overrides network default)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Dict, List, Optional, Tuple

from ..models import (
    Fill, Order, OrderState, OrderType, PositionInfo, Side,
)
from ..precision import from_drift_base, from_drift_price, to_drift_base, to_drift_price
from .live_base import LiveExecutionContext
from .wallet import KeypairAdapter

logger = logging.getLogger("flint.drift_live")

# Drift market index → symbol mapping
MARKET_TO_INDEX = {
    "SOL-PERP": 0, "BTC-PERP": 1, "ETH-PERP": 2, "APT-PERP": 3,
    "1MBONK-PERP": 4, "POL-PERP": 5, "ARB-PERP": 6, "DOGE-PERP": 7,
    "BNB-PERP": 8, "SUI-PERP": 9, "1MPEPE-PERP": 10, "OP-PERP": 11,
    "RENDER-PERP": 12, "XRP-PERP": 13, "HNT-PERP": 14, "INJ-PERP": 15,
    "LINK-PERP": 16, "RLB-PERP": 17, "PYTH-PERP": 18, "TIA-PERP": 19,
    "JTO-PERP": 20, "SEI-PERP": 21, "AVAX-PERP": 22, "WIF-PERP": 23,
    "JUP-PERP": 24, "DYM-PERP": 25, "TAO-PERP": 26, "W-PERP": 27,
    "KMNO-PERP": 28, "TNSR-PERP": 29, "DRIFT-PERP": 30,
}

INDEX_TO_MARKET = {v: k for k, v in MARKET_TO_INDEX.items()}

_NETWORK_RPC = {
    "devnet": "https://api.devnet.solana.com",
    "mainnet": "https://api.mainnet-beta.solana.com",
}


def _check_driftpy():
    """Check if driftpy is installed."""
    try:
        import driftpy  # noqa: F401
        return True
    except ImportError:
        return False


class LiveDriftContext(LiveExecutionContext):
    """ExecutionContext that submits real orders to Drift Protocol.

    Uses driftpy SDK for on-chain order execution.
    Same interface as BacktestContext — strategies work identically.
    """

    def __init__(
        self,
        private_key: Optional[str] = None,
        rpc_url: Optional[str] = None,
        network: str = "devnet",
        initial_capital: float = 0,
        risk_manager=None,
        store=None,
        session_id: str = "",
        max_retries: int = 3,
        on_failure: str = "drop",
    ):
        if not _check_driftpy():
            raise ImportError(
                "driftpy is required for live trading. Install with: pip install driftpy\n"
                "Note: requires Python 3.10+ and Solana CLI tools."
            )

        # Resolve RPC URL: env override > param > network default
        self._rpc_url = (
            os.environ.get("FLINT_RPC_URL")
            or rpc_url
            or _NETWORK_RPC.get(network, _NETWORK_RPC["devnet"])
        )
        self._network = network

        # Create wallet adapter
        key = private_key or os.environ.get("FLINT_PRIVATE_KEY", "")
        if not key:
            raise ValueError(
                "No private key provided. Set FLINT_PRIVATE_KEY environment variable "
                "or pass private_key parameter."
            )
        self._wallet = KeypairAdapter(private_key=key)
        self._drift_client = None

        super().__init__(
            venue="drift",
            initial_capital=initial_capital,
            risk_manager=risk_manager,
            store=store,
            session_id=session_id,
            max_retries=max_retries,
            on_failure=on_failure,
        )

        logger.info("LiveDriftContext initialized (network=%s, RPC=%s)", network, self._rpc_url)

    # --- Abstract method implementations ---

    async def _connect(self) -> None:
        """Initialize driftpy client and connect to Drift."""
        from driftpy.drift_client import DriftClient
        from solana.rpc.async_api import AsyncClient

        connection = AsyncClient(self._rpc_url)
        env = "devnet" if self._network == "devnet" else "mainnet"

        self._drift_client = DriftClient(
            connection=connection,
            wallet=self._wallet.keypair,
            env=env,
        )
        await self._drift_client.subscribe()
        logger.info("Connected to Drift Protocol (%s)", self._network)

    async def _disconnect(self) -> None:
        """Disconnect from Drift."""
        if self._drift_client is not None:
            await self._drift_client.unsubscribe()
            self._drift_client = None
            logger.info("Disconnected from Drift")

    async def _place_order(self, order: Order) -> Tuple[str, Optional[int]]:
        """Submit order to Drift on-chain."""
        if self._drift_client is None:
            raise RuntimeError("Not connected to Drift — call connect() first")

        market_idx = MARKET_TO_INDEX.get(order.market)
        if market_idx is None:
            raise ValueError(f"Unknown Drift market: {order.market}")

        from driftpy.types import (
            OrderParams, OrderType as DriftOrderType,
            MarketType, PositionDirection,
        )

        direction = (
            PositionDirection.Long()
            if order.side == Side.LONG
            else PositionDirection.Short()
        )
        size_base = to_drift_base(order.size)

        if order.order_type == OrderType.MARKET:
            order_params = OrderParams(
                order_type=DriftOrderType.Market(),
                market_index=market_idx,
                market_type=MarketType.Perp(),
                direction=direction,
                base_asset_amount=size_base,
            )
        elif order.order_type == OrderType.LIMIT:
            price_int = to_drift_price(order.price)
            order_params = OrderParams(
                order_type=DriftOrderType.Limit(),
                market_index=market_idx,
                market_type=MarketType.Perp(),
                direction=direction,
                base_asset_amount=size_base,
                price=price_int,
            )
        elif order.order_type == OrderType.STOP_LOSS:
            trigger_price_int = to_drift_price(order.price)
            order_params = OrderParams(
                order_type=DriftOrderType.TriggerMarket(),
                market_index=market_idx,
                market_type=MarketType.Perp(),
                direction=direction,
                base_asset_amount=size_base,
                trigger_price=trigger_price_int,
            )
        elif order.order_type == OrderType.TAKE_PROFIT:
            trigger_price_int = to_drift_price(order.price)
            order_params = OrderParams(
                order_type=DriftOrderType.TriggerLimit(),
                market_index=market_idx,
                market_type=MarketType.Perp(),
                direction=direction,
                base_asset_amount=size_base,
                trigger_price=trigger_price_int,
            )
        else:
            raise ValueError(f"Unsupported order type: {order.order_type}")

        tx_sig = await self._drift_client.place_perp_order(order_params)
        tx_sig_str = str(tx_sig) if tx_sig else ""
        logger.info("Order submitted: %s tx=%s", order.order_id, tx_sig_str)

        # venue_order_id will be discovered by polling after confirmation
        return (tx_sig_str, None)

    async def _cancel_order(self, venue_order_id: int) -> bool:
        """Cancel an order on Drift."""
        if self._drift_client is None:
            return False
        try:
            await self._drift_client.cancel_order(venue_order_id)
            return True
        except Exception as e:
            logger.error("Cancel order %d failed: %s", venue_order_id, e)
            return False

    async def _fetch_positions(self) -> List[PositionInfo]:
        """Fetch open perp positions from Drift user account."""
        if self._drift_client is None:
            return []
        try:
            user = self._drift_client.get_user()
            perp_positions = user.get_perp_positions()
            result = []
            for pos in perp_positions:
                if pos.base_asset_amount == 0:
                    continue
                market_name = INDEX_TO_MARKET.get(pos.market_index)
                if market_name is None:
                    continue
                size = from_drift_base(abs(pos.base_asset_amount))
                side = Side.LONG if pos.base_asset_amount > 0 else Side.SHORT
                entry = from_drift_price(pos.entry_price) if hasattr(pos, 'entry_price') else 0

                # Compute unrealized PnL from oracle price
                unrealized = 0.0
                try:
                    market_account = self._drift_client.get_perp_market_account(pos.market_index)
                    oracle_price = from_drift_price(
                        market_account.amm.historical_oracle_data.last_oracle_price
                    )
                    if side == Side.LONG:
                        unrealized = (oracle_price - entry) * size
                    else:
                        unrealized = (entry - oracle_price) * size
                except Exception:
                    pass

                result.append(PositionInfo(
                    market=market_name,
                    side=side,
                    size=size,
                    entry_price=entry,
                    unrealized_pnl=unrealized,
                    venue="drift",
                ))
            return result
        except Exception as e:
            logger.error("Position fetch failed: %s", e)
            return []

    async def _fetch_balance(self) -> float:
        """Fetch free collateral from Drift user account."""
        if self._drift_client is None:
            return 0.0
        try:
            user = self._drift_client.get_user()
            free_collateral = user.get_free_collateral()
            from ..precision import QUOTE_PRECISION
            return float(free_collateral) / QUOTE_PRECISION
        except Exception as e:
            logger.error("Balance fetch failed: %s", e)
            return 0.0

    async def _poll_order_status(self, venue_order_id: int) -> OrderState:
        """Poll Drift for order status."""
        if self._drift_client is None:
            return OrderState.FAILED
        try:
            user = self._drift_client.get_user()
            order = user.get_order(venue_order_id)
            if order is None:
                return OrderState.CANCELLED

            filled = from_drift_base(order.base_asset_amount_filled)
            total = from_drift_base(order.base_asset_amount)

            if filled >= total:
                return OrderState.FILLED
            elif filled > 0:
                return OrderState.PARTIALLY_FILLED
            else:
                return OrderState.CONFIRMED
        except Exception as e:
            logger.error("Order status poll failed for %d: %s", venue_order_id, e)
            return OrderState.CONFIRMED  # assume still open on error
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_drift_live.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v --timeout=120`
Expected: All existing tests still pass (no regressions)

- [ ] **Step 6: Commit**

```bash
git add flint/execution/drift_live.py tests/test_drift_live.py
git commit -m "feat: rewrite LiveDriftContext on LiveExecutionContext base with full Drift SDK integration"
```

---

### Task 8: Update ROADMAP.md

**Files:**
- Modify: `ROADMAP.md`

- [ ] **Step 1: Add timer-based tick and wallet adapter notes to §1.1**

In `ROADMAP.md`, after line 64 (`**Dependencies**: driftpy SDK...`), add:

```markdown
- [x] Timer-based strategy tick loop (poll REST each tick, event-driven deferred to §1.3 WebSocket feeds)
- [x] `WalletAdapter` abstraction with `KeypairAdapter` (built) and `BrowserWalletAdapter` (interface only, implementation deferred to follow-up)
- [x] `LiveExecutionContext` base class for venue-agnostic live trading scaffolding
```

- [ ] **Step 2: Commit**

```bash
git add ROADMAP.md
git commit -m "docs: update ROADMAP.md with Phase 1.1 implementation notes"
```

---

### Task 9: Integration test — end-to-end order lifecycle

**Files:**
- Create: `tests/test_live_integration.py`

- [ ] **Step 1: Write the integration test**

Create `tests/test_live_integration.py`:

```python
"""Integration test: full order lifecycle through LiveExecutionContext → Store."""
import asyncio
import time
import pytest

from flint.models import Fill, Order, OrderState, OrderType, PositionInfo, Side
from flint.store import FlintStore
from flint.execution.live_base import LiveExecutionContext
from flint.execution.order_tracker import OrderTracker


class MockVenueForIntegration(LiveExecutionContext):
    """Mock venue that simulates place → confirm → fill lifecycle."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._mock_venue_oid = 0

    async def _connect(self):
        pass

    async def _disconnect(self):
        pass

    async def _place_order(self, order):
        self._mock_venue_oid += 1
        return (f"tx_{self._mock_venue_oid}", self._mock_venue_oid)

    async def _cancel_order(self, venue_order_id):
        return True

    async def _fetch_positions(self):
        return []

    async def _fetch_balance(self):
        return 10000.0

    async def _poll_order_status(self, venue_order_id):
        return OrderState.CONFIRMED


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestEndToEndLifecycle:
    def test_submit_order_persists_to_store(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        ctx = MockVenueForIntegration(
            venue="test", initial_capital=10000.0,
            store=store, session_id="int-test",
        )

        # Place an order
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        assert oid != ""

        # Submit pending orders (simulates tick loop)
        run(ctx.submit_pending_orders())

        # Verify order persisted to store
        orders = store.get_live_orders("int-test")
        assert len(orders) == 1
        assert orders[0]["order_id"] == oid
        assert orders[0]["state"] in ("submitted", "confirmed")

        store.close()

    def test_fill_updates_position_and_store(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        ctx = MockVenueForIntegration(
            venue="test", initial_capital=10000.0,
            store=store, session_id="int-test-2",
        )

        # Place and submit
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        run(ctx.submit_pending_orders())

        # Simulate fill arriving via tracker
        fill = Fill(
            market="SOL-PERP", side=Side.LONG, price=150.0,
            size=10.0, fee=0.15, ts=int(time.time()),
            order_id=oid, tx_sig="tx_1", venue="test",
        )
        ctx._tracker.mark_filled(oid, fill)

        # Position should be updated
        pos = ctx.position("SOL-PERP", venue="test")
        assert pos is not None
        assert pos.size == 10.0
        assert pos.entry_price == 150.0

        # Fill should be in store
        fills = store.get_live_fills("int-test-2")
        assert len(fills) == 1
        assert fills[0]["price"] == 150.0

        store.close()

    def test_session_creation_and_equity(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        store.create_live_session(
            session_id="eq-test",
            strategy_name="test_strat",
            market="SOL-PERP",
            network="devnet",
            venue="test",
            initial_capital=10000.0,
            config_snapshot="{}",
        )

        ctx = MockVenueForIntegration(
            venue="test", initial_capital=10000.0,
            store=store, session_id="eq-test",
        )

        # Persist equity
        ctx._persist_equity()

        history = store.get_live_equity_history("eq-test")
        assert len(history) == 1
        assert history[0]["equity"] == 10000.0
        assert history[0]["cash"] == 10000.0

        store.close()

    def test_risk_guard_rejects_order(self, tmp_path):
        from flint.risk.guards import RiskManager, MaxOpenPositions
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        rm = RiskManager(guards=[MaxOpenPositions(max_positions=0)])

        ctx = MockVenueForIntegration(
            venue="test", initial_capital=10000.0,
            store=store, session_id="risk-test",
            risk_manager=rm,
        )

        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        assert oid == ""  # rejected
        assert len(ctx._tracker.active_orders) == 0

        store.close()
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/test_live_integration.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 3: Run full test suite to verify no regressions**

Run: `pytest tests/ -v --timeout=120`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_live_integration.py
git commit -m "test: add end-to-end integration tests for live execution lifecycle"
```

---

## File Map

| Action | File | Task |
|--------|------|------|
| Modify | `flint/models.py` | 1 |
| Modify | `flint/config.py` | 2 |
| Modify | `flint/store.py` | 3 |
| Create | `flint/execution/wallet.py` | 4 |
| Create | `flint/execution/order_tracker.py` | 5 |
| Create | `flint/execution/live_base.py` | 6 |
| Rewrite | `flint/execution/drift_live.py` | 7 |
| Modify | `ROADMAP.md` | 8 |
| Create | `tests/test_models.py` | 1 |
| Create | `tests/test_config.py` | 2 |
| Create | `tests/test_store_live.py` | 3 |
| Create | `tests/test_wallet.py` | 4 |
| Create | `tests/test_order_tracker.py` | 5 |
| Create | `tests/test_live_base.py` | 6 |
| Rewrite | `tests/test_drift_live.py` | 7 |
| Create | `tests/test_live_integration.py` | 9 |

## Dependency Order

```
Task 1 (models)
Task 2 (config)      ──→ all can run in parallel
Task 3 (store)
Task 4 (wallet)      ──→ Task 7 (drift_live depends on wallet)
Task 5 (tracker)     ──→ Task 6 (live_base depends on tracker)
                         Task 6 ──→ Task 7 (drift_live extends live_base)
                                    Task 7 ──→ Task 8 (roadmap update)
                                               Task 9 (integration tests)
```

Tasks 1-5 can be built in parallel. Task 6 depends on Task 5. Task 7 depends on Tasks 4 and 6. Tasks 8-9 come last.
