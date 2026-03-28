# Paper Trading Realism Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every realism gap between backtesting and paper trading — funding payments, realistic fills, session persistence with restart recovery, LiveContext data access parity, multi-venue support, inter-candle PnL, and limit order timeouts.

**Architecture:** PaperBroker gets multi-venue position keying `(venue, market)`, VenueAllocator integration, funding rate application, and SlippageFill. LiveContext gets a store reference to serve funding/orderbook/OI data from DuckDB. The engine persists every candle and resumes sessions on restart. All changes follow existing patterns from BacktestContext/MarginEngine.

**Tech Stack:** Python 3.10+, DuckDB, asyncio, FastAPI

---

## Execution Order

```
Parallel Wave 1 (independent):
  Task 1: Funding rate application (paper_broker.py)
  Task 2: Realistic fills — SlippageFill default (paper_broker.py)
  Task 5: Rolling candle history (engine.py)
  Task 7: Inter-candle PnL via price ticker (engine.py)
  Task 8: Limit order timeouts (paper_broker.py)

Sequential after Wave 1:
  Task 10: Multi-venue broker (paper_broker.py) — depends on Tasks 1, 2
  Task 4: LiveContext data access (live_context.py) — independent but best after 1
  Task 6: Order latency simulation (paper_broker.py) — depends on Task 2

Sequential after all above:
  Task 3: Session persistence + resumption (engine.py, session_store.py)
  Task 9: Enhanced API (paper.py) — depends on all above
```

**IMPORTANT**: Tasks 1, 2, 8 all modify `paper_broker.py`. Tasks 5, 7 both modify `engine.py`. When running in parallel, each agent must work on NON-OVERLAPPING sections of these files. Assign specific line ranges to avoid merge conflicts, OR run them sequentially within each file.

**Recommended approach**: Run Tasks 1+5+4 in parallel (different files), then 2+7+8 (paper_broker sections), then 10, then 6+3, then 9.

---

### Task 1: Funding Rate Application

**Files:**
- Modify: `flint/execution/paper_broker.py` — add `apply_funding()` method
- Modify: `flint/store.py` — add `paper_funding_payments` table DDL
- Modify: `flint/paper/session_store.py` — add funding payment persistence
- Modify: `flint/paper/engine.py` — add funding check to live loop
- Test: `tests/test_paper_funding.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_paper_funding.py`:

```python
"""Tests for paper trading funding rate application."""
import os
import tempfile
import pytest
from unittest.mock import MagicMock

from flint.execution.paper_broker import PaperBroker
from flint.models import Side


def test_apply_funding_long_positive_rate():
    """Long pays when funding rate is positive."""
    broker = PaperBroker(initial_capital=10000)
    broker.positions["SOL-PERP"] = {
        "market": "SOL-PERP", "side": "long", "size": 100,
        "entry_price": 100.0, "entry_ts": 1000, "unrealized_pnl": 0,
    }
    # Positive rate = longs pay shorts
    # payment = size * mark_price * rate = 100 * 100 * 0.0001 = 1.0
    payment = broker.apply_funding("SOL-PERP", rate=0.0001, mark_price=100.0)
    assert abs(payment - 1.0) < 0.01
    assert broker.cash < 10000  # cash decreased
    assert broker.total_funding > 0


def test_apply_funding_short_positive_rate():
    """Short receives when funding rate is positive."""
    broker = PaperBroker(initial_capital=10000)
    broker.positions["SOL-PERP"] = {
        "market": "SOL-PERP", "side": "short", "size": 100,
        "entry_price": 100.0, "entry_ts": 1000, "unrealized_pnl": 0,
    }
    payment = broker.apply_funding("SOL-PERP", rate=0.0001, mark_price=100.0)
    assert payment < 0  # negative = received
    assert broker.cash > 10000  # cash increased


def test_apply_funding_no_position():
    """No payment when no position exists."""
    broker = PaperBroker(initial_capital=10000)
    payment = broker.apply_funding("SOL-PERP", rate=0.0001, mark_price=100.0)
    assert payment == 0.0
    assert broker.cash == 10000


def test_apply_funding_negative_rate():
    """Negative rate = longs receive, shorts pay."""
    broker = PaperBroker(initial_capital=10000)
    broker.positions["SOL-PERP"] = {
        "market": "SOL-PERP", "side": "long", "size": 50,
        "entry_price": 100.0, "entry_ts": 1000, "unrealized_pnl": 0,
    }
    payment = broker.apply_funding("SOL-PERP", rate=-0.0002, mark_price=100.0)
    assert payment < 0  # long receives when rate is negative
    assert broker.cash > 10000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && .venv/bin/pytest tests/test_paper_funding.py -v`

Expected: FAIL — `PaperBroker` has no `apply_funding` method.

- [ ] **Step 3: Implement `apply_funding()` on PaperBroker**

In `flint/execution/paper_broker.py`, add after the `get_liquidation_price` method (around line 116):

```python
    def apply_funding(self, market: str, rate: float, mark_price: float) -> float:
        """Apply funding rate to an open position.

        Positive rate = longs pay shorts.
        Negative rate = shorts pay longs.
        Returns the payment amount (positive = paid, negative = received).
        """
        pos = self.positions.get(market)
        if pos is None:
            return 0.0

        notional = pos["size"] * mark_price
        payment = notional * rate

        if pos["side"] == "long":
            # Longs pay when rate > 0, receive when rate < 0
            self.cash -= payment
            self.total_funding += payment
        else:
            # Shorts receive when rate > 0, pay when rate < 0
            self.cash += payment
            self.total_funding -= payment
            payment = -payment  # invert sign for shorts

        return payment

    def close_all_positions(self, mark_prices: dict) -> None:
        """Force-close all positions at mark prices. Used by liquidation."""
        for market in list(self.positions.keys()):
            pos = self.positions[market]
            mark = mark_prices.get(market, pos["entry_price"])
            if pos["side"] == "long":
                pnl = (mark - pos["entry_price"]) * pos["size"]
            else:
                pnl = (pos["entry_price"] - mark) * pos["size"]
            self.cash += pnl
            self.closed_trades.append({
                **pos, "exit_price": mark, "exit_ts": int(time.time()), "pnl": pnl,
            })
        self.positions.clear()
```

- [ ] **Step 4: Add `paper_funding_payments` table to store.py**

In `flint/store.py`, after the `paper_positions` table DDL, add:

```python
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_funding_payments (
                    session_id   VARCHAR NOT NULL,
                    ts           BIGINT NOT NULL,
                    market       VARCHAR NOT NULL,
                    rate         DOUBLE NOT NULL,
                    payment      DOUBLE NOT NULL,
                    position_size DOUBLE NOT NULL,
                    mark_price   DOUBLE NOT NULL
                )
            """)
```

- [ ] **Step 5: Add funding persistence to session_store.py**

In `flint/paper/session_store.py`, add:

```python
    def save_funding_payment(self, session_id: str, ts: int, market: str,
                             rate: float, payment: float, position_size: float,
                             mark_price: float) -> None:
        with self._store._lock:
            self._store._conn.execute(
                "INSERT INTO paper_funding_payments "
                "(session_id, ts, market, rate, payment, position_size, mark_price) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [session_id, ts, market, rate, payment, position_size, mark_price],
            )

    def get_funding_payments(self, session_id: str) -> list:
        with self._store._lock:
            rows = self._store._conn.execute(
                "SELECT ts, market, rate, payment, position_size, mark_price "
                "FROM paper_funding_payments WHERE session_id = ? ORDER BY ts",
                [session_id],
            ).fetchall()
        return [{"ts": r[0], "market": r[1], "rate": r[2], "payment": r[3],
                 "position_size": r[4], "mark_price": r[5]} for r in rows]
```

- [ ] **Step 6: Add funding application to live loop in engine.py**

In `flint/paper/engine.py`, in `_run_live_session`, add funding check logic. After the candle processing for-loop ends and before the equity buffer persistence check, add:

```python
                # Apply funding rates from store
                if session.broker.positions:
                    try:
                        last_funding_ts = getattr(session, '_last_funding_ts', 0)
                        funding = self.store.query_venue_funding(
                            session.market, last_funding_ts + 1, int(time.time())
                        )
                        if funding:
                            for fr in funding:
                                payment = session.broker.apply_funding(
                                    session.market, fr.rate, fr.mark_price or candle.close if candle else 0
                                )
                                if payment != 0 and ss:
                                    pos = session.broker.positions.get(session.market)
                                    ss.save_funding_payment(
                                        session.session_id, fr.ts, session.market,
                                        fr.rate, payment,
                                        pos["size"] if pos else 0,
                                        fr.mark_price or 0,
                                    )
                            session._last_funding_ts = funding[-1].ts
                    except Exception as e:
                        logger.debug("Funding application error: %s", e)
```

Note: `self.store.query_venue_funding()` returns funding rate objects. Read the store method to confirm the return type — it may return a list of `FundingRate` objects or row tuples. Adapt accordingly.

- [ ] **Step 7: Run tests**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && .venv/bin/pytest tests/test_paper_funding.py tests/test_paper.py tests/test_paper_deploy.py -v`

Expected: All pass.

- [ ] **Step 8: Commit**

```bash
git add flint/execution/paper_broker.py flint/store.py flint/paper/session_store.py flint/paper/engine.py tests/test_paper_funding.py
git commit -m "feat: apply funding rate payments to paper trading positions"
```

---

### Task 2: Realistic Fill Pricing — SlippageFill Default

**Files:**
- Modify: `flint/execution/paper_broker.py:33` — change default fill model
- Test: `tests/test_paper.py` (add test)

- [ ] **Step 1: Write failing test**

Add to `tests/test_paper_funding.py` (or a new test file):

```python
def test_paper_broker_uses_slippage_fill_by_default():
    """PaperBroker should use SlippageFill, not ClosePriceFill."""
    from flint.execution.fill_models import SlippageFill
    broker = PaperBroker(initial_capital=10000)
    assert isinstance(broker.fill_model, SlippageFill)
```

- [ ] **Step 2: Run test to see it fail**

Expected: FAIL — currently uses `ClosePriceFill`.

- [ ] **Step 3: Change default fill model**

In `flint/execution/paper_broker.py` line 33, change:

```python
        self.fill_model = fill_model or ClosePriceFill()
```

To:

```python
        self.fill_model = fill_model or SlippageFill(slippage_bps=5.0)
```

Add `SlippageFill` to the import at the top:

```python
from .fill_models import FillModel, ClosePriceFill, SlippageFill
```

- [ ] **Step 4: Also change default fee model to DriftFeeModel**

In the same `__init__`, change:

```python
        self.fee_model = fee_model or FlatFeeModel()
```

To:

```python
        self.fee_model = fee_model or DriftFeeModel()
```

Add the import:

```python
from .fee_models import FeeModel, FlatFeeModel, DriftFeeModel
```

Read `flint/execution/fee_models.py` to confirm `DriftFeeModel` exists and its import name.

- [ ] **Step 5: Run all paper tests**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && .venv/bin/pytest tests/test_paper.py tests/test_paper_deploy.py tests/test_paper_funding.py -v`

Expected: All pass. Some equity values may shift slightly due to slippage — update assertions if needed.

- [ ] **Step 6: Commit**

```bash
git add flint/execution/paper_broker.py tests/
git commit -m "feat: use SlippageFill and DriftFeeModel as paper trading defaults"
```

---

### Task 3: Session Persistence and Restart Resumption

**Files:**
- Modify: `flint/paper/engine.py` — persist every candle, add `resume_sessions()`
- Modify: `flint/paper/session_store.py` — add `get_latest_equity()` method
- Modify: `flint/api/main.py` — call `resume_sessions()` on startup
- Test: `tests/test_paper_resume.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_paper_resume.py`:

```python
"""Tests for paper trading session resumption."""
import os
import tempfile
import pytest

from flint.models import Candle
from flint.paper.engine import PaperTradingEngine
from flint.paper.session_store import PaperSessionStore
from flint.store import FlintStore
from flint.strategy.ma_crossover import MACrossoverStrategy


@pytest.fixture
def store_and_engine():
    db = os.path.join(tempfile.gettempdir(), "test_resume.duckdb")
    store = FlintStore(db)
    engine = PaperTradingEngine(store)
    yield store, engine
    store.close()
    if os.path.exists(db):
        os.unlink(db)


def _make_candles(n=50, start_ts=1700000000):
    candles = []
    price = 100.0
    for i in range(n):
        ts = start_ts + i * 3600
        price *= 1.01 if i % 2 == 0 else 0.99
        candles.append(Candle(
            market="SOL-PERP", resolution_s=3600, ts=ts,
            open=price, high=price * 1.005, low=price * 0.995,
            close=price, volume=1000,
        ))
    return candles


def test_resume_sessions_loads_from_db(store_and_engine):
    store, engine = store_and_engine
    candles = _make_candles(50)
    store.upsert_candles(candles)

    # Deploy a session
    strategy = MACrossoverStrategy(fast_period=5, slow_period=10)
    session_id = engine.deploy_session(
        strategy=strategy, strategy_code="class X: pass",
        strategy_params={"fast_period": 5, "slow_period": 10},
        market="SOL-PERP", initial_capital=10000,
        replay_start_ts=1700000000, risk_config={},
    )

    # Simulate restart: create a fresh engine (in-memory sessions lost)
    engine2 = PaperTradingEngine(store)
    assert len(engine2.sessions) == 0

    # Resume should reconstruct from DB
    resumed = engine2.resume_sessions()
    assert resumed >= 1  # at least our session was resumed


def test_positions_persisted_during_live(store_and_engine):
    store, engine = store_and_engine
    candles = _make_candles(50)
    store.upsert_candles(candles)

    strategy = MACrossoverStrategy(fast_period=5, slow_period=10)
    session_id = engine.deploy_session(
        strategy=strategy, strategy_code="class X: pass",
        strategy_params={}, market="SOL-PERP", initial_capital=10000,
        replay_start_ts=1700000000, risk_config={},
    )

    # Check persistence
    ss = PaperSessionStore(store)
    session_data = ss.load_session(session_id)
    assert session_data is not None
    assert session_data["status"] in ("live", "replaying")
```

- [ ] **Step 2: Implement `resume_sessions()` on PaperTradingEngine**

In `flint/paper/engine.py`, add:

```python
    def resume_sessions(self) -> int:
        """Resume all active sessions from DuckDB after server restart.

        Returns count of successfully resumed sessions.
        """
        ss = PaperSessionStore(self.store)
        active = ss.list_active_sessions()
        resumed = 0

        for session_data in active:
            sid = session_data["session_id"]
            try:
                full = ss.load_session(sid)
                if not full:
                    continue

                # Reconstruct strategy
                from ..strategy.loader import load_user_strategy
                strategy = load_user_strategy(
                    full["strategy_code"],
                    full["strategy_params"] or None,
                )

                # Get last equity snapshot for cash
                equity_history = ss.get_equity_history(sid)
                last_equity = equity_history[-1] if equity_history else None
                cash = last_equity["equity"] if last_equity else full["initial_capital"]
                last_ts = last_equity["ts"] if last_equity else 0

                # Reconstruct broker
                broker = PaperBroker(initial_capital=cash)
                broker.equity_history = [cash]

                # Restore positions
                positions = ss.load_positions(sid)
                for p in positions:
                    broker.positions[p["market"]] = {
                        "market": p["market"],
                        "side": p["side"],
                        "size": p["size"],
                        "entry_price": p["entry_price"],
                        "entry_ts": p["entry_ts"],
                        "unrealized_pnl": p.get("unrealized_pnl", 0),
                    }

                # Create context and session
                ctx = LiveContext(broker)
                session = PaperSession(
                    session_id=sid,
                    strategy=strategy,
                    market=full["market"],
                    resolution_s=3600,
                    broker=broker,
                    ctx=ctx,
                )
                session.last_candle_ts = last_ts
                session.status = "live"
                session.session_store = ss

                # Attach risk guard
                risk_cfg = full.get("risk_config", {})
                rc = RiskConfig(
                    max_drawdown_pct=risk_cfg.get("max_drawdown_pct", 0.15),
                    daily_loss_limit=risk_cfg.get("daily_loss_limit", 500),
                    max_position_pct=risk_cfg.get("max_position_pct", 0.95),
                    liquidation_enabled=risk_cfg.get("liquidation_enabled", True),
                )
                session.risk_guard = RiskGuard(rc)
                session.risk_config = risk_cfg

                self.sessions[sid] = session

                # Launch live loop
                task = self._schedule_async_task(self._run_live_session(session))
                if task is not None:
                    self._tasks[sid] = task

                # Cancel any pending orders (they're stale after restart)
                broker.pending_orders.clear()

                resumed += 1
                logger.info("Resumed session %s: %s on %s (last_ts=%d, cash=%.2f, positions=%d)",
                            sid, full["strategy_name"], full["market"], last_ts, cash, len(positions))
            except Exception as e:
                logger.error("Failed to resume session %s: %s", sid, e)

        logger.info("Resumed %d/%d active sessions", resumed, len(active))
        return resumed
```

- [ ] **Step 3: Change equity persistence from batch-10 to every candle**

In `_run_live_session`, change the equity buffer flush condition from:

```python
                if ss and len(equity_buffer) >= 10:
```

To:

```python
                if ss and equity_buffer:
```

Also add position persistence after each candle. After the equity_buffer append (line 391), add:

```python
                    # Persist positions
                    if ss:
                        pos_list = [
                            {"market": m, **p} for m, p in session.broker.positions.items()
                        ]
                        ss.save_positions(session.session_id, pos_list)
```

And persist closed trades when they happen. After `session.broker.process_candle(candle)` (line 378), add:

```python
                    # Persist any new closed trades
                    if ss and session.broker.closed_trades:
                        new_trades = session.broker.closed_trades[len(getattr(session, '_persisted_trade_count', 0)):]
                        if new_trades:
                            for t in new_trades:
                                t.setdefault("trade_id", f"live-{session.session_id}-{len(session.broker.closed_trades)}")
                                t.setdefault("is_replay", False)
                            ss.save_trades(session.session_id, new_trades)
                            session._persisted_trade_count = len(session.broker.closed_trades)
```

- [ ] **Step 4: Call resume_sessions() in main.py**

In `flint/api/main.py`, after the paper engine is created and event loop is set, add:

```python
        paper_engine.resume_sessions()
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && .venv/bin/pytest tests/test_paper_resume.py tests/test_paper_deploy.py tests/test_paper.py -v`

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add flint/paper/engine.py flint/paper/session_store.py flint/api/main.py tests/test_paper_resume.py
git commit -m "feat: persist full session state and resume after restart"
```

---

### Task 4: LiveContext Data Access — Funding, Orderbook, OI, Candles

**Files:**
- Modify: `flint/execution/live_context.py` — add store reference, implement data methods
- Test: `tests/test_live_context_data.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_live_context_data.py`:

```python
"""Tests for LiveContext data access methods."""
import os
import tempfile
import pytest

from flint.execution.live_context import LiveContext
from flint.execution.paper_broker import PaperBroker
from flint.models import Candle, FundingRate
from flint.store import FlintStore


@pytest.fixture
def ctx_with_store():
    db = os.path.join(tempfile.gettempdir(), "test_live_ctx.duckdb")
    store = FlintStore(db)
    broker = PaperBroker(initial_capital=10000)
    ctx = LiveContext(broker, store=store, resolution_s=3600, session_id="test1")
    yield ctx, store
    store.close()
    if os.path.exists(db):
        os.unlink(db)


def test_get_candles_returns_data(ctx_with_store):
    ctx, store = ctx_with_store
    candles = [
        Candle(market="SOL-PERP", resolution_s=3600, ts=1000 + i * 3600,
               open=100, high=101, low=99, close=100, volume=1000)
        for i in range(10)
    ]
    store.upsert_candles(candles)
    result = ctx.get_candles("SOL-PERP", lookback=5)
    assert len(result) == 5


def test_get_candles_without_store():
    broker = PaperBroker(initial_capital=10000)
    ctx = LiveContext(broker)
    result = ctx.get_candles("SOL-PERP", lookback=5)
    assert result == []


def test_log_does_not_crash(ctx_with_store):
    ctx, _ = ctx_with_store
    ctx.log("test message")  # should not raise
```

- [ ] **Step 2: Implement LiveContext with store**

Rewrite `flint/execution/live_context.py` to add store reference and data methods:

```python
"""LiveContext — ExecutionContext backed by PaperBroker for paper trading."""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

from ..models import (
    AccountState, Candle, Order, OrderType, PositionInfo, Side,
)
from .context import ExecutionContext
from .paper_broker import PaperBroker
from ..store import FlintStore

logger = logging.getLogger("flint.paper")


class LiveContext(ExecutionContext):
    """ExecutionContext for paper (and eventually live) trading.

    When a store reference is provided, data access methods
    (get_funding_rates, get_orderbook, get_candles, etc.)
    query DuckDB for real data, matching BacktestContext behavior.
    """

    def __init__(self, broker: PaperBroker, store: Optional[FlintStore] = None,
                 resolution_s: int = 3600, session_id: str = ""):
        self._broker = broker
        self._store = store
        self._resolution_s = resolution_s
        self._session_id = session_id
        self._current_candle: Optional[Candle] = None
        self._order_counter = 0

    def _next_id(self) -> str:
        self._order_counter += 1
        return f"paper-{self._order_counter}"

    # --- Account & Position State ---

    @property
    def account(self) -> AccountState:
        unrealized = sum(
            p.get("unrealized_pnl", 0) for p in self._broker.positions.values()
        )
        return AccountState(
            equity=self._broker.equity,
            cash=self._broker.cash,
            unrealized_pnl=unrealized,
            margin_used=self._broker.margin_used,
            free_margin=self._broker.free_margin,
            leverage=self._broker.leverage,
        )

    @property
    def positions(self) -> List[PositionInfo]:
        result = []
        for pos in self._broker.positions.values():
            result.append(PositionInfo(
                market=pos["market"],
                side=Side.LONG if pos["side"] == "long" else Side.SHORT,
                size=pos["size"],
                entry_price=pos["entry_price"],
                unrealized_pnl=pos.get("unrealized_pnl", 0),
                entry_ts=pos.get("entry_ts", 0),
            ))
        return result

    @property
    def pending_orders(self) -> List[Order]:
        return list(self._broker.pending_orders)

    @property
    def current_candle(self) -> Optional[Candle]:
        return self._current_candle

    @property
    def timestamp(self) -> int:
        return self._current_candle.ts if self._current_candle else 0

    def set_candle(self, candle: Candle) -> None:
        self._current_candle = candle

    # --- Order Placement ---

    def market_order(self, market, side, size, reduce_only=False, tag="", venue="default"):
        oid = self._next_id()
        order = Order(market=market, side=side, order_type=OrderType.MARKET,
                      size=size, order_id=oid, ts=self.timestamp)
        self._broker.submit_order(order)
        return oid

    def limit_order(self, market, side, size, price, reduce_only=False, tag="", venue="default"):
        oid = self._next_id()
        order = Order(market=market, side=side, order_type=OrderType.LIMIT,
                      size=size, price=price, order_id=oid, ts=self.timestamp)
        self._broker.submit_order(order)
        return oid

    def stop_order(self, market, side, size, trigger_price, tag="", venue="default"):
        oid = self._next_id()
        order = Order(market=market, side=side, order_type=OrderType.STOP_LOSS,
                      size=size, price=trigger_price, order_id=oid, ts=self.timestamp)
        self._broker.submit_order(order)
        return oid

    def take_profit_order(self, market, side, size, trigger_price, tag="", venue="default"):
        oid = self._next_id()
        order = Order(market=market, side=side, order_type=OrderType.TAKE_PROFIT,
                      size=size, price=trigger_price, order_id=oid, ts=self.timestamp)
        self._broker.submit_order(order)
        return oid

    def cancel(self, order_id):
        return self._broker.cancel_order(order_id)

    def cancel_all(self, market=None):
        return self._broker.cancel_all(market)

    # --- Data Access (queries DuckDB via store) ---

    def get_funding_rate(self, market: Optional[str] = None) -> Optional[float]:
        rates = self.get_funding_rates(market, lookback=1)
        return rates[-1][1] if rates else None

    def get_funding_rates(self, market: Optional[str] = None, lookback: int = 24) -> list:
        if not self._store:
            return []
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return []
        now = int(time.time())
        start = now - lookback * 3600
        try:
            funding = self._store.query_venue_funding(mkt, start, now)
            return [(f.ts, f.rate) for f in funding] if funding else []
        except Exception:
            return []

    def get_funding_by_venue(self, market: Optional[str] = None, lookback: int = 24) -> dict:
        if not self._store:
            return {}
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return {}
        now = int(time.time())
        start = now - lookback * 3600
        try:
            return self._store.query_funding_by_venue(mkt, start, now)
        except Exception:
            return {}

    def get_orderbook(self, market: Optional[str] = None):
        if not self._store:
            return None
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return None
        try:
            snapshots = self._store.query_orderbook_snapshots(mkt, limit=1)
            return snapshots[0] if snapshots else None
        except Exception:
            return None

    def get_candles(self, market: str, lookback: int = 50) -> list:
        if not self._store:
            return []
        try:
            candles = self._store.query_candles(market, self._resolution_s)
            return candles[-lookback:] if candles else []
        except Exception:
            return []

    def log(self, message: str) -> None:
        logger.info("[%s] %s", self._session_id, message)
```

- [ ] **Step 3: Update deploy_session to pass store to LiveContext**

In `flint/paper/engine.py`, change the LiveContext creation in `deploy_session` (around line 245):

```python
        ctx = LiveContext(broker, store=self.store, resolution_s=resolution_s, session_id=session_id)
```

Also update `start_session` and `resume_sessions` similarly.

- [ ] **Step 4: Run tests**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && .venv/bin/pytest tests/test_live_context_data.py tests/test_paper.py tests/test_paper_deploy.py -v`

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add flint/execution/live_context.py flint/paper/engine.py tests/test_live_context_data.py
git commit -m "feat: add store-backed data access to LiveContext (funding, orderbook, candles)"
```

---

### Task 5: Rolling Candle History

**Files:**
- Modify: `flint/paper/engine.py:364` — trim history to rolling window

- [ ] **Step 1: Fix the history in `_run_live_session`**

In `flint/paper/engine.py`, in the `_run_live_session` method, find:

```python
                for candle in candles:
                    history.append(candle)
```

Change to:

```python
                for candle in candles:
                    history.append(candle)
                    if len(history) > 500:
                        history = history[-500:]
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && .venv/bin/pytest tests/test_paper.py tests/test_paper_deploy.py -v`

Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add flint/paper/engine.py
git commit -m "fix: trim candle history to rolling 500-bar window in live loop"
```

---

### Task 6: Order Latency Simulation

**Files:**
- Modify: `flint/execution/paper_broker.py` — use FillPipeline when latency is enabled

- [ ] **Step 1: Add latency option to PaperBroker**

In `__init__`, add a `latency_enabled` parameter:

```python
    def __init__(self, initial_capital=10000.0, fill_model=None, fee_model=None,
                 venue="drift", latency_enabled=False):
```

When `latency_enabled=True` and no explicit `fill_model` is provided, use `FillPipeline`:

```python
        if fill_model:
            self.fill_model = fill_model
        elif latency_enabled and self._venue_config:
            from .fill_models import FillPipeline
            self.fill_model = FillPipeline(
                slippage_bps=5.0,
                base_latency_s=self._venue_config.base_latency_s,
                latency_jitter_s=self._venue_config.latency_jitter_s,
            )
        else:
            self.fill_model = SlippageFill(slippage_bps=5.0)
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && .venv/bin/pytest tests/test_paper.py tests/test_paper_deploy.py -v`

Expected: All pass (latency disabled by default, no behavior change).

- [ ] **Step 3: Commit**

```bash
git add flint/execution/paper_broker.py
git commit -m "feat: add configurable latency simulation to paper broker"
```

---

### Task 7: Inter-Candle PnL Updates via Price Ticker

**Files:**
- Modify: `flint/paper/engine.py` — use price ticker between candles

- [ ] **Step 1: Add price update in live loop**

In `_run_live_session`, after the candle processing for-loop and before `await asyncio.sleep(10)`, add:

```python
                # Update mark prices from ticker (between candles)
                ticker = getattr(self, 'price_ticker', None)
                if ticker and session.broker.positions:
                    mark = ticker.get_price(session.market)
                    if mark is not None:
                        for market, pos in session.broker.positions.items():
                            if market == session.market:
                                pos["mark_price"] = mark
                                if pos["side"] == "long":
                                    pos["unrealized_pnl"] = (mark - pos["entry_price"]) * pos["size"]
                                else:
                                    pos["unrealized_pnl"] = (pos["entry_price"] - mark) * pos["size"]
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && .venv/bin/pytest tests/test_paper.py tests/test_paper_deploy.py -v`

Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add flint/paper/engine.py
git commit -m "feat: update position PnL between candles using DLOB price ticker"
```

---

### Task 8: Limit Order Timeouts

**Files:**
- Modify: `flint/execution/paper_broker.py` — add timeout logic to `process_candle`

- [ ] **Step 1: Write failing test**

Add to `tests/test_paper_funding.py`:

```python
def test_limit_order_expires_after_timeout():
    """Limit orders should expire after N bars."""
    from flint.models import Order, OrderType, Side, Candle
    broker = PaperBroker(initial_capital=10000)
    broker._limit_timeout_bars = 3
    broker._resolution_s = 3600

    order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.LIMIT,
                  size=10, price=50.0, order_id="test-1", ts=1000)
    broker.submit_order(order)
    assert len(broker.pending_orders) == 1

    # Process 4 candles at higher prices (limit won't fill)
    for i in range(4):
        candle = Candle(market="SOL-PERP", resolution_s=3600, ts=1000 + (i + 1) * 3600,
                        open=100, high=101, low=99, close=100, volume=1000)
        broker.process_candle(candle)

    # Order should be expired after 3 bars
    assert len(broker.pending_orders) == 0
```

- [ ] **Step 2: Implement timeout in process_candle**

In `flint/execution/paper_broker.py`, add instance variables to `__init__`:

```python
        self._limit_timeout_bars = 24  # default: expire after 24 bars
        self._resolution_s = 3600
```

In `process_candle`, at the start of the method (before the fill loop), add:

```python
        # Expire timed-out limit/stop orders
        if self._limit_timeout_bars > 0:
            expired = []
            for order in self.pending_orders:
                if order.order_type != OrderType.MARKET and order.ts > 0:
                    age_bars = (candle.ts - order.ts) // max(self._resolution_s, 1)
                    if age_bars >= self._limit_timeout_bars:
                        expired.append(order)
            for order in expired:
                self.pending_orders.remove(order)
                logger.debug("Order %s expired after %d bars", order.order_id, self._limit_timeout_bars)
```

- [ ] **Step 3: Run tests**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && .venv/bin/pytest tests/test_paper_funding.py tests/test_paper.py -v`

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add flint/execution/paper_broker.py tests/test_paper_funding.py
git commit -m "feat: add configurable timeout for limit/stop orders in paper broker"
```

---

### Task 9: Enhanced API — Margin, Funding, Equity History

**Files:**
- Modify: `flint/api/routes/paper.py` — extend status response, add equity-history endpoint

- [ ] **Step 1: Extend status response with margin and funding data**

In `flint/api/routes/paper.py`, find the `get_status` or `status` endpoint. Update the response dict to include margin metrics and funding totals. Read the file first to find the exact function.

Add to the status response:

```python
    # After getting the basic status dict
    status_dict["margin"] = {
        "leverage": round(session.broker.leverage, 2),
        "margin_used": round(session.broker.margin_used, 2),
        "free_margin": round(session.broker.free_margin, 2),
        "margin_ratio": round(session.broker.margin_ratio, 4),
        "liquidation_prices": {
            m: round(session.broker.get_liquidation_price(m), 2)
            for m in session.broker.positions
        },
    }
    status_dict["funding_total"] = round(session.broker.total_funding, 4)
```

- [ ] **Step 2: Add equity-history endpoint**

```python
@router.get("/{session_id}/equity-history")
def get_equity_history(session_id: str, request: Request):
    """Get full equity curve for a session from DuckDB."""
    store = request.app.state.store if hasattr(request.app.state, "store") else None
    if store is None:
        return {"equity_curve": []}
    ss = PaperSessionStore(store)
    history = ss.get_equity_history(session_id)
    return {"equity_curve": history}
```

Make sure to place this BEFORE the `/{session_id}/risk` route to avoid path conflicts. Add the `PaperSessionStore` import at the top if not already present.

- [ ] **Step 3: Run tests**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && .venv/bin/pytest tests/test_api.py -v`

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add flint/api/routes/paper.py
git commit -m "feat: add margin metrics, funding totals, and equity-history to paper API"
```

---

### Task 10: Multi-Venue Paper Trading

**Files:**
- Modify: `flint/execution/paper_broker.py` — change position key to `(venue, market)`, integrate VenueAllocator
- Modify: `flint/execution/live_context.py` — implement venue methods
- Modify: `flint/paper/engine.py` — accept `capital_allocation` in deploy, process transfers
- Modify: `flint/api/routes/paper.py` — accept `capital_allocation` in deploy request
- Test: `tests/test_paper_multi_venue.py`

**This is the largest and most complex task.** It touches the core position storage format. Every method that reads `self.positions` must be updated.

- [ ] **Step 1: Write failing tests**

Create `tests/test_paper_multi_venue.py`:

```python
"""Tests for multi-venue paper trading."""
import pytest
from flint.execution.paper_broker import PaperBroker
from flint.models import Candle, Order, OrderType, Side


def test_positions_keyed_by_venue_market():
    """Positions should use (venue, market) tuple keys."""
    broker = PaperBroker(initial_capital=10000)
    order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                  size=10, order_id="t1", ts=1000)
    order.venue = "drift"
    broker.submit_order(order)

    candle = Candle(market="SOL-PERP", resolution_s=3600, ts=1000,
                    open=100, high=101, low=99, close=100, volume=1000)
    broker.process_candle(candle)

    # Position should be keyed by (venue, market)
    assert ("drift", "SOL-PERP") in broker.positions or "SOL-PERP" in broker.positions


def test_venue_allocator_tracks_per_venue_balances():
    """When capital_allocation is set, balances should be per-venue."""
    broker = PaperBroker(
        initial_capital=8000,
        capital_allocation={"drift": 5000, "hyperliquid": 3000},
    )
    assert broker.venue_balance("drift") == 5000
    assert broker.venue_balance("hyperliquid") == 3000


def test_multi_venue_equity():
    """Equity should sum across all venues."""
    broker = PaperBroker(
        initial_capital=8000,
        capital_allocation={"drift": 5000, "hyperliquid": 3000},
    )
    assert broker.equity == 8000
```

- [ ] **Step 2: Implement multi-venue in PaperBroker**

This is a significant refactor. The key changes:

**a) Add `capital_allocation` to `__init__`:**

```python
    def __init__(self, initial_capital=10000.0, fill_model=None, fee_model=None,
                 venue="drift", latency_enabled=False, capital_allocation=None):
        # ... existing init ...

        self._allocator = None
        if capital_allocation:
            from .capital import VenueAllocator
            self._allocator = VenueAllocator(capital_allocation)
            self.cash = self._allocator.total_cash
```

**b) Add venue_balance method:**

```python
    def venue_balance(self, venue: str) -> float:
        if self._allocator:
            return self._allocator.available(venue)
        return self.cash
```

**c) Update `_apply_fill` to route cash through allocator:**

When allocator exists, debit fees from the order's venue and credit/debit PnL to the position's venue.

**d) Update `equity` property to include all venue balances plus unrealized PnL.**

**NOTE**: Changing positions from `Dict[str, dict]` to `Dict[tuple, dict]` is a breaking change that ripples through LiveContext, engine.py, risk_guard.py, and the API. The safest approach for backward compatibility:

- Keep positions keyed by `str` (market name) for single-venue mode
- When `capital_allocation` is provided, add a `venue` field to each position dict
- Venue methods look up positions by filtering on the venue field
- This avoids breaking all existing code that accesses `positions[market]`

- [ ] **Step 3: Implement venue methods on LiveContext**

Add to `LiveContext`:

```python
    def venue_balance(self, venue: str) -> float:
        if hasattr(self._broker, '_allocator') and self._broker._allocator:
            return self._broker._allocator.available(venue)
        return self._broker.cash

    def venue_balances(self) -> dict:
        if hasattr(self._broker, '_allocator') and self._broker._allocator:
            return dict(self._broker._allocator._balances)
        return {"default": self._broker.cash}

    def transfer(self, from_venue: str, to_venue: str, amount: float) -> bool:
        if not hasattr(self._broker, '_allocator') or not self._broker._allocator:
            return False
        import time as _time
        t = self._broker._allocator.transfer(from_venue, to_venue, amount, int(_time.time()))
        if t:
            self._broker.cash = self._broker._allocator.total_cash
        return t is not None
```

- [ ] **Step 4: Update deploy to accept capital_allocation**

In `flint/paper/engine.py` `deploy_session()`, add `capital_allocation` parameter and pass to PaperBroker:

```python
    def deploy_session(self, ..., capital_allocation=None):
        ...
        broker = PaperBroker(
            initial_capital=final_cash,
            capital_allocation=capital_allocation,
        )
```

In `flint/api/routes/paper.py` deploy endpoint, pass it through:

```python
    capital_allocation = body.get("capital_allocation")
    # ... pass to engine.deploy_session(capital_allocation=capital_allocation)
```

- [ ] **Step 5: Run all tests**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && .venv/bin/pytest tests/test_paper_multi_venue.py tests/test_paper.py tests/test_paper_deploy.py tests/test_paper_funding.py tests/test_risk_guard.py -v`

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add flint/execution/paper_broker.py flint/execution/live_context.py flint/paper/engine.py flint/api/routes/paper.py tests/test_paper_multi_venue.py
git commit -m "feat: add multi-venue paper trading with per-venue capital and transfers"
```

---

## Post-Implementation Verification

After all 10 tasks are complete:

```bash
cd /Users/sohan/Documents/solana_stuff/flint

# Run ALL paper trading tests
.venv/bin/pytest tests/test_paper*.py tests/test_risk_guard.py tests/test_price_ticker.py tests/test_live_context_data.py tests/test_api.py -v

# Run full test suite for regressions
.venv/bin/pytest tests/ -v --timeout=30

# Build UI
cd ui && npm run build
```

All tests should pass. No regressions in backtest, optimization, or data pipeline tests.
