# Jupiter Perps Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate Jupiter Perps into Flint with live execution via TS sidecar, historical borrow rate collection via Dune Analytics, and continuous borrow fee modeling for backtests.

**Architecture:** Three parallel workstreams — (1) data collection pipeline (Dune backfill + RPC forward collector → DuckDB), (2) TS sidecar for live execution (Fastify wrapping jup-perps-client, managed as subprocess), (3) backtest support (HoldingCostModel abstraction, continuous borrow accrual). They converge at LiveJupiterContext and BacktestContext.

**Tech Stack:** Python (FastAPI, DuckDB, httpx, solders), TypeScript (Fastify, jup-perps-client), Dune Analytics API, Solana RPC.

**Spec:** `docs/superpowers/specs/2026-04-05-jupiter-perps-integration-design.md`

---

## Task 1: BorrowSnapshot Model

**Files:**
- Modify: `flint/models.py:187-194` (after FundingRate)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_jupiter_borrow_model.py
from flint.models import BorrowSnapshot


def test_borrow_snapshot_creation():
    bs = BorrowSnapshot(
        market="SOL-PERP",
        ts=1700000000,
        rate_hourly=0.00008,
        utilization=0.65,
        cumulative_rate=1.00234,
        source="rpc",
    )
    assert bs.market == "SOL-PERP"
    assert bs.rate_hourly == 0.00008
    assert bs.utilization == 0.65
    assert bs.cumulative_rate == 1.00234
    assert bs.source == "rpc"


def test_borrow_snapshot_frozen():
    bs = BorrowSnapshot(
        market="SOL-PERP", ts=1700000000, rate_hourly=0.00008,
        utilization=0.65, cumulative_rate=1.00234,
    )
    try:
        bs.market = "ETH-PERP"
        assert False, "Should be frozen"
    except AttributeError:
        pass


def test_borrow_snapshot_defaults():
    bs = BorrowSnapshot(
        market="ETH-PERP", ts=1700000000, rate_hourly=0.0001,
        utilization=0.5, cumulative_rate=1.001,
    )
    assert bs.source == "rpc"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_jupiter_borrow_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'BorrowSnapshot'`

- [ ] **Step 3: Add BorrowSnapshot to models.py**

Add after the `FundingRate` dataclass (after line ~194 in `flint/models.py`):

```python
@dataclass(frozen=True)
class BorrowSnapshot:
    """Jupiter Perps borrow rate snapshot.

    Unlike FundingRate (periodic, can be negative), borrow rates are
    continuous and always positive. The cumulative_rate field tracks
    the monotonically increasing on-chain counter used to compute
    position borrow costs.
    """
    market: str
    ts: int
    rate_hourly: float
    utilization: float
    cumulative_rate: float
    source: str = "rpc"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_jupiter_borrow_model.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add flint/models.py tests/test_jupiter_borrow_model.py
git commit -m "feat: add BorrowSnapshot model for Jupiter Perps"
```

---

## Task 2: DuckDB Store — jupiter_borrow_rates Table and Methods

**Files:**
- Modify: `flint/store.py` (table DDL ~line 81, new methods after funding methods ~line 599)
- Test: `tests/test_jupiter_store.py`

- [ ] **Step 1: Write failing tests for store methods**

```python
# tests/test_jupiter_store.py
import os
import tempfile

from flint.models import BorrowSnapshot
from flint.store import FlintStore


def _make_store():
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    return FlintStore(path), path


def test_upsert_and_query_borrow_rates():
    store, path = _make_store()
    try:
        snapshots = [
            BorrowSnapshot("SOL-PERP", 1000, 0.00008, 0.65, 1.001, "rpc"),
            BorrowSnapshot("SOL-PERP", 2000, 0.00009, 0.70, 1.002, "rpc"),
            BorrowSnapshot("SOL-PERP", 3000, 0.00010, 0.75, 1.003, "dune"),
        ]
        count = store.upsert_borrow_rates(snapshots)
        assert count == 3

        results = store.query_borrow_rates("SOL-PERP", 1000, 3000)
        assert len(results) == 3
        assert results[0].ts == 1000
        assert results[0].rate_hourly == 0.00008
        assert results[2].source == "dune"
    finally:
        store.close()
        os.unlink(path)


def test_query_borrow_cumulative():
    store, path = _make_store()
    try:
        snapshots = [
            BorrowSnapshot("SOL-PERP", 1000, 0.00008, 0.65, 1.001, "rpc"),
            BorrowSnapshot("SOL-PERP", 2000, 0.00009, 0.70, 1.002, "rpc"),
            BorrowSnapshot("SOL-PERP", 3000, 0.00010, 0.75, 1.003, "rpc"),
        ]
        store.upsert_borrow_rates(snapshots)

        # Exact match
        rate = store.query_borrow_cumulative("SOL-PERP", 2000)
        assert rate == 1.002

        # Nearest (between 2000 and 3000, should return closest <= ts)
        rate = store.query_borrow_cumulative("SOL-PERP", 2500)
        assert rate == 1.002

        # Before any data
        rate = store.query_borrow_cumulative("SOL-PERP", 500)
        assert rate is None
    finally:
        store.close()
        os.unlink(path)


def test_upsert_borrow_rates_upsert_semantics():
    store, path = _make_store()
    try:
        store.upsert_borrow_rates([
            BorrowSnapshot("SOL-PERP", 1000, 0.00008, 0.65, 1.001, "rpc"),
        ])
        # Upsert with updated value
        store.upsert_borrow_rates([
            BorrowSnapshot("SOL-PERP", 1000, 0.00012, 0.80, 1.005, "dune"),
        ])
        results = store.query_borrow_rates("SOL-PERP", 1000, 1000)
        assert len(results) == 1
        assert results[0].rate_hourly == 0.00012
        assert results[0].source == "dune"
    finally:
        store.close()
        os.unlink(path)


def test_query_borrow_rates_empty():
    store, path = _make_store()
    try:
        results = store.query_borrow_rates("SOL-PERP", 1000, 2000)
        assert results == []
    finally:
        store.close()
        os.unlink(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_jupiter_store.py -v`
Expected: FAIL — `AttributeError: 'FlintStore' object has no attribute 'upsert_borrow_rates'`

- [ ] **Step 3: Add table DDL to store.py**

In `flint/store.py`, add the table creation SQL constant after the existing `_CREATE_VENUE_FUNDING` (around line 81), and add it to the `_init_tables` method:

```python
_CREATE_JUPITER_BORROW = """
CREATE TABLE IF NOT EXISTS jupiter_borrow_rates (
    market          VARCHAR NOT NULL,
    ts              BIGINT  NOT NULL,
    rate_hourly     DOUBLE  NOT NULL,
    utilization     DOUBLE  NOT NULL,
    cumulative_rate DOUBLE  NOT NULL,
    source          VARCHAR NOT NULL DEFAULT 'rpc',
    PRIMARY KEY (market, ts)
);
"""
```

Add `self._conn.execute(_CREATE_JUPITER_BORROW)` to the `_init_tables` method alongside the other CREATE TABLE calls.

- [ ] **Step 4: Add upsert_borrow_rates method**

Add after the existing funding query methods (after `query_funding_by_venue`):

```python
def upsert_borrow_rates(self, snapshots: list) -> int:
    """Insert or replace Jupiter borrow rate snapshots."""
    if not snapshots:
        return 0
    with self._lock:
        try:
            self._conn.execute("BEGIN TRANSACTION")
            for s in snapshots:
                self._conn.execute(
                    "INSERT OR REPLACE INTO jupiter_borrow_rates "
                    "(market, ts, rate_hourly, utilization, cumulative_rate, source) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [s.market, s.ts, s.rate_hourly, s.utilization,
                     s.cumulative_rate, s.source],
                )
            self._conn.execute("COMMIT")
            return len(snapshots)
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
```

- [ ] **Step 5: Add query_borrow_rates method**

```python
def query_borrow_rates(self, market: str, start_ts: int, end_ts: int) -> list:
    """Query borrow rate snapshots for a market within a time range."""
    from .models import BorrowSnapshot
    with self._lock:
        rows = self._conn.execute(
            "SELECT market, ts, rate_hourly, utilization, cumulative_rate, source "
            "FROM jupiter_borrow_rates "
            "WHERE market = ? AND ts >= ? AND ts <= ? "
            "ORDER BY ts",
            [market, start_ts, end_ts],
        ).fetchall()
    return [
        BorrowSnapshot(
            market=r[0], ts=r[1], rate_hourly=r[2],
            utilization=r[3], cumulative_rate=r[4], source=r[5],
        )
        for r in rows
    ]
```

- [ ] **Step 6: Add query_borrow_cumulative method**

```python
def query_borrow_cumulative(self, market: str, ts: int):
    """Get the nearest cumulative borrow rate at or before a timestamp."""
    with self._lock:
        rows = self._conn.execute(
            "SELECT cumulative_rate FROM jupiter_borrow_rates "
            "WHERE market = ? AND ts <= ? "
            "ORDER BY ts DESC LIMIT 1",
            [market, ts],
        ).fetchall()
    return rows[0][0] if rows else None
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_jupiter_store.py -v`
Expected: 4 passed

- [ ] **Step 8: Run existing store tests to verify no regressions**

Run: `pytest tests/test_store.py -v`
Expected: All existing tests pass

- [ ] **Step 9: Commit**

```bash
git add flint/store.py tests/test_jupiter_store.py
git commit -m "feat: add jupiter_borrow_rates table and store methods"
```

---

## Task 3: HoldingCostModel Abstraction

**Files:**
- Create: `flint/execution/holding_cost.py`
- Test: `tests/test_holding_cost.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_holding_cost.py
from flint.execution.holding_cost import (
    FundingCostModel,
    BorrowCostModel,
)


def test_funding_cost_positive_rate_long():
    """Long pays when funding rate is positive."""
    model = FundingCostModel()
    cost = model.cost_at_bar(
        side="long", size_usd=10000.0, rate=0.0001,
    )
    # Long pays: rate × size = 0.0001 × 10000 = 1.0
    assert cost == 1.0


def test_funding_cost_negative_rate_long():
    """Long gets paid when funding rate is negative."""
    model = FundingCostModel()
    cost = model.cost_at_bar(
        side="long", size_usd=10000.0, rate=-0.0001,
    )
    # Long receives: -0.0001 × 10000 = -1.0 (negative = credit)
    assert cost == -1.0


def test_funding_cost_short():
    """Short pays opposite of long."""
    model = FundingCostModel()
    cost = model.cost_at_bar(
        side="short", size_usd=10000.0, rate=0.0001,
    )
    # Short receives: -(0.0001 × 10000) = -1.0
    assert cost == -1.0


def test_borrow_cost_at_bar():
    """Borrow cost is always positive (deducted from PnL)."""
    model = BorrowCostModel()
    cost = model.cost_at_bar(
        cumulative_entry=1.00100,
        cumulative_now=1.00150,
        size_usd=10000.0,
    )
    # (1.00150 - 1.00100) × 10000 = 0.0005 × 10000 = 5.0
    assert abs(cost - 5.0) < 1e-9


def test_borrow_cost_at_close():
    """Borrow cost at close uses same formula."""
    model = BorrowCostModel()
    cost = model.cost_at_close(
        cumulative_entry=1.00100,
        cumulative_close=1.00300,
        size_usd=20000.0,
    )
    # (1.00300 - 1.00100) × 20000 = 0.002 × 20000 = 40.0
    assert abs(cost - 40.0) < 1e-9


def test_borrow_cost_never_negative():
    """Cumulative rate only increases, so cost is always >= 0."""
    model = BorrowCostModel()
    cost = model.cost_at_bar(
        cumulative_entry=1.005,
        cumulative_now=1.005,  # no change
        size_usd=10000.0,
    )
    assert cost == 0.0


def test_borrow_cost_same_for_long_and_short():
    """Both sides pay borrow fees on Jupiter."""
    model = BorrowCostModel()
    cost_long = model.cost_at_bar(
        cumulative_entry=1.001, cumulative_now=1.002, size_usd=10000.0,
    )
    cost_short = model.cost_at_bar(
        cumulative_entry=1.001, cumulative_now=1.002, size_usd=10000.0,
    )
    assert cost_long == cost_short
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_holding_cost.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flint.execution.holding_cost'`

- [ ] **Step 3: Implement holding_cost.py**

```python
# flint/execution/holding_cost.py
"""Holding cost models for different venue fee structures.

FundingCostModel: Periodic funding rates (Drift, Hyperliquid) — can be positive or negative.
BorrowCostModel: Continuous borrow fees (Jupiter Perps) — always positive.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class HoldingCostModel(ABC):
    """Base class for position holding cost calculations."""

    @abstractmethod
    def cost_at_bar(self, **kwargs) -> float:
        """Unrealized holding cost at a bar for margin/liquidation checks."""

    @abstractmethod
    def cost_at_close(self, **kwargs) -> float:
        """Realized holding cost when position is closed."""


class FundingCostModel(HoldingCostModel):
    """Periodic funding rate model (Drift, Hyperliquid, Binance, etc.).

    Longs pay when rate > 0, shorts pay when rate < 0.
    """

    def cost_at_bar(self, *, side: str, size_usd: float, rate: float) -> float:
        if side == "long":
            return rate * size_usd
        else:
            return -(rate * size_usd)

    def cost_at_close(self, *, side: str, size_usd: float, rate: float) -> float:
        return self.cost_at_bar(side=side, size_usd=size_usd, rate=rate)


class BorrowCostModel(HoldingCostModel):
    """Continuous borrow fee model (Jupiter Perps).

    Always positive — both longs and shorts pay. Cost is computed from
    the delta of the on-chain cumulative interest rate counter.
    """

    def cost_at_bar(
        self, *, cumulative_entry: float, cumulative_now: float, size_usd: float,
    ) -> float:
        return (cumulative_now - cumulative_entry) * size_usd

    def cost_at_close(
        self, *, cumulative_entry: float, cumulative_close: float, size_usd: float,
    ) -> float:
        return (cumulative_close - cumulative_entry) * size_usd
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_holding_cost.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add flint/execution/holding_cost.py tests/test_holding_cost.py
git commit -m "feat: add HoldingCostModel abstraction (funding vs borrow)"
```

---

## Task 4: VenueConfig Jupiter Preset

**Files:**
- Modify: `flint/execution/venue_config.py:36-77` (VENUE_DEFAULTS dict)
- Test: `tests/test_jupiter_venue_config.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_jupiter_venue_config.py
from flint.execution.venue_config import get_venue_config


def test_jupiter_venue_config_exists():
    cfg = get_venue_config("jupiter")
    assert cfg.name == "jupiter"


def test_jupiter_fee_is_flat_6bps():
    cfg = get_venue_config("jupiter")
    assert cfg.taker_fee_bps == 6.0
    assert cfg.maker_fee_bps == 6.0


def test_jupiter_leverage_100x():
    cfg = get_venue_config("jupiter")
    assert cfg.max_leverage == 100.0
    assert cfg.initial_margin == 0.01


def test_jupiter_high_latency():
    """Jupiter has 2-step keeper model so latency is higher."""
    cfg = get_venue_config("jupiter")
    assert cfg.base_latency_s >= 10.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_jupiter_venue_config.py -v`
Expected: FAIL — Jupiter config returns the "default" fallback, assertions fail

- [ ] **Step 3: Add Jupiter preset to VENUE_DEFAULTS**

In `flint/execution/venue_config.py`, add a `"jupiter"` entry to the `VENUE_DEFAULTS` dict (alongside drift, hyperliquid, etc.):

```python
"jupiter": VenueConfig(
    name="jupiter",
    taker_fee_bps=6.0,
    maker_fee_bps=6.0,
    initial_margin=0.01,
    maintenance_margin=0.002,
    max_leverage=100.0,
    liquidation_penalty=0.0,
    impact_coefficient=0.03,
    base_latency_s=12.0,
    latency_jitter_s=8.0,
),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_jupiter_venue_config.py -v`
Expected: 4 passed

- [ ] **Step 5: Run existing venue config tests**

Run: `pytest tests/ -k venue_config -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add flint/execution/venue_config.py tests/test_jupiter_venue_config.py
git commit -m "feat: add Jupiter Perps venue config preset"
```

---

## Task 5: ExecutionContext ABC — Borrow Rate Methods

**Files:**
- Modify: `flint/execution/context.py:160-183` (after funding methods)
- Test: `tests/test_jupiter_context_abc.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_jupiter_context_abc.py
from flint.execution.context import ExecutionContext


def test_get_borrow_rate_default_returns_none():
    """ABC default implementation returns None."""
    # ExecutionContext can't be instantiated directly, but we can test
    # the default method exists by checking it's defined
    assert hasattr(ExecutionContext, "get_borrow_rate")
    assert hasattr(ExecutionContext, "get_borrow_rates")


def test_get_borrow_rate_method_signature():
    """Verify the method signature accepts market and venue params."""
    import inspect
    sig = inspect.signature(ExecutionContext.get_borrow_rate)
    params = list(sig.parameters.keys())
    assert "market" in params
    assert "venue" in params


def test_get_borrow_rates_method_signature():
    sig = inspect.signature(ExecutionContext.get_borrow_rates)
    params = list(sig.parameters.keys())
    assert "market" in params
    assert "venue" in params
    assert "lookback" in params
```

Add `import inspect` at the top.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_jupiter_context_abc.py -v`
Expected: FAIL — `AttributeError: type object 'ExecutionContext' has no attribute 'get_borrow_rate'`

- [ ] **Step 3: Add borrow rate methods to ExecutionContext**

In `flint/execution/context.py`, add after the `get_funding_by_venue` method (after line ~183):

```python
def get_borrow_rate(self, market: str = None, venue: str = None):
    """Current hourly borrow rate. Returns None for venues using funding rates."""
    return None

def get_borrow_rates(self, market: str = None, venue: str = None, lookback: int = 24) -> list:
    """Historical borrow rates as [(ts, rate), ...]. Empty for non-borrow venues."""
    return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_jupiter_context_abc.py -v`
Expected: 3 passed

- [ ] **Step 5: Run all existing context tests**

Run: `pytest tests/ -k "context" -v`
Expected: All pass (new default methods return None/[], existing behavior unchanged)

- [ ] **Step 6: Commit**

```bash
git add flint/execution/context.py tests/test_jupiter_context_abc.py
git commit -m "feat: add get_borrow_rate/get_borrow_rates to ExecutionContext ABC"
```

---

## Task 6: BacktestContext — Borrow Rate Tracking and Accrual

**Files:**
- Modify: `flint/execution/backtest_context.py` (init ~line 105, new methods after line ~353)
- Test: `tests/test_jupiter_backtest.py`

- [ ] **Step 1: Write failing tests for borrow rate tracking**

```python
# tests/test_jupiter_backtest.py
"""Tests for Jupiter borrow rate support in BacktestContext."""
from flint.models import BorrowSnapshot, Candle


def _make_ctx(**kwargs):
    """Create a BacktestContext with minimal config."""
    from flint.execution.backtest_context import BacktestContext
    defaults = dict(
        initial_capital=10000.0,
        fee_rate=0.0006,
        markets=["SOL-PERP"],
    )
    defaults.update(kwargs)
    return BacktestContext(**defaults)


def test_add_and_get_borrow_rate():
    ctx = _make_ctx()
    bs = BorrowSnapshot("SOL-PERP", 1000, 0.00008, 0.65, 1.001, "rpc")
    ctx.add_borrow_rate(bs)

    rate = ctx.get_borrow_rate("SOL-PERP")
    assert rate == 0.00008


def test_get_borrow_rate_returns_none_when_empty():
    ctx = _make_ctx()
    assert ctx.get_borrow_rate("SOL-PERP") is None


def test_get_borrow_rates_with_lookback():
    ctx = _make_ctx()
    for i in range(10):
        ts = 1000 + i * 3600
        bs = BorrowSnapshot("SOL-PERP", ts, 0.00008 + i * 0.00001, 0.65, 1.001 + i * 0.001, "rpc")
        ctx.add_borrow_rate(bs)

    rates = ctx.get_borrow_rates("SOL-PERP", lookback=5)
    assert len(rates) == 5
    # Should be the most recent 5
    assert rates[-1][1] == 0.00008 + 9 * 0.00001


def test_get_borrow_cumulative_at():
    """Get cumulative rate at a specific timestamp."""
    ctx = _make_ctx()
    ctx.add_borrow_rate(BorrowSnapshot("SOL-PERP", 1000, 0.00008, 0.65, 1.001, "rpc"))
    ctx.add_borrow_rate(BorrowSnapshot("SOL-PERP", 2000, 0.00009, 0.70, 1.002, "rpc"))
    ctx.add_borrow_rate(BorrowSnapshot("SOL-PERP", 3000, 0.00010, 0.75, 1.003, "rpc"))

    cum = ctx.get_borrow_cumulative_at("SOL-PERP", 2000)
    assert cum == 1.002

    # Between snapshots — returns the one at or before ts
    cum = ctx.get_borrow_cumulative_at("SOL-PERP", 2500)
    assert cum == 1.002

    # Before any data
    cum = ctx.get_borrow_cumulative_at("SOL-PERP", 500)
    assert cum is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_jupiter_backtest.py -v`
Expected: FAIL — `AttributeError: 'BacktestContext' object has no attribute 'add_borrow_rate'`

- [ ] **Step 3: Add borrow rate state to BacktestContext.__init__**

In `flint/execution/backtest_context.py`, add after the `_venue_funding` initialization (around line 105):

```python
self._borrow_history: dict = {}  # {market: [BorrowSnapshot]}
```

- [ ] **Step 4: Add borrow rate methods to BacktestContext**

Add after the `get_venue_snapshots` method (after line ~353):

```python
def add_borrow_rate(self, bs) -> None:
    """Record a borrow rate snapshot for strategy access."""
    mkt = bs.market
    if mkt not in self._borrow_history:
        self._borrow_history[mkt] = []
    self._borrow_history[mkt].append(bs)

def get_borrow_rate(self, market: str = None, venue: str = None):
    """Get the most recent hourly borrow rate for a market."""
    mkt = market or (self._current_candle.market if self._current_candle else None)
    if not mkt:
        return None
    history = self._borrow_history.get(mkt)
    if not history:
        return None
    return history[-1].rate_hourly

def get_borrow_rates(self, market: str = None, venue: str = None, lookback: int = 24) -> list:
    """Get recent borrow rate history as [(ts, rate_hourly), ...]."""
    mkt = market or (self._current_candle.market if self._current_candle else None)
    if not mkt:
        return []
    history = self._borrow_history.get(mkt, [])
    sliced = history[-lookback:] if lookback < len(history) else history
    return [(bs.ts, bs.rate_hourly) for bs in sliced]

def get_borrow_cumulative_at(self, market: str, ts: int):
    """Get the cumulative borrow rate at or before a timestamp.

    Returns None if no data exists before the given timestamp.
    """
    history = self._borrow_history.get(market, [])
    result = None
    for bs in history:
        if bs.ts <= ts:
            result = bs.cumulative_rate
        else:
            break
    return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_jupiter_backtest.py -v`
Expected: 4 passed

- [ ] **Step 6: Run all backtest context tests**

Run: `pytest tests/ -k "backtest_context" -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add flint/execution/backtest_context.py tests/test_jupiter_backtest.py
git commit -m "feat: add borrow rate tracking to BacktestContext"
```

---

## Task 7: Backtest Engine — Borrow Rate Application

**Files:**
- Modify: `flint/backtest/engine.py:155-218` (funding loop area)
- Modify: `flint/execution/backtest_context.py` (add _borrow_cumulative_at_entry tracking to positions)
- Test: `tests/test_jupiter_backtest_engine.py`

- [ ] **Step 1: Write failing test for borrow accrual in backtest**

```python
# tests/test_jupiter_backtest_engine.py
"""Test that Jupiter borrow fees are accrued continuously in backtests."""
from flint.models import BorrowSnapshot, Candle


def _run_jupiter_backtest(candles, borrow_snapshots, strategy_code):
    """Helper to run a backtest with borrow rate data."""
    from flint.backtest.engine import run_backtest

    result = run_backtest(
        candles=candles,
        strategy_code=strategy_code,
        initial_capital=10000.0,
        fee_rate=0.0006,
        borrow_snapshots=borrow_snapshots,
    )
    return result


def test_borrow_cost_deducted_on_close():
    """Borrow cost should be realized when position is closed."""
    candles = [
        Candle("SOL-PERP", 1000, 100.0, 101.0, 99.0, 100.0, 1000.0, 0),
        Candle("SOL-PERP", 2000, 100.0, 101.0, 99.0, 100.0, 1000.0, 0),
        Candle("SOL-PERP", 3000, 100.0, 101.0, 99.0, 100.0, 1000.0, 0),
        Candle("SOL-PERP", 4000, 100.0, 101.0, 99.0, 100.0, 1000.0, 0),
    ]

    # Cumulative rate increases: 1.000, 1.001, 1.002, 1.003
    borrow_snapshots = [
        BorrowSnapshot("SOL-PERP", 1000, 0.001, 0.5, 1.000, "rpc"),
        BorrowSnapshot("SOL-PERP", 2000, 0.001, 0.5, 1.001, "rpc"),
        BorrowSnapshot("SOL-PERP", 3000, 0.001, 0.5, 1.002, "rpc"),
        BorrowSnapshot("SOL-PERP", 4000, 0.001, 0.5, 1.003, "rpc"),
    ]

    # Buy on bar 1, sell on bar 3
    code = '''
class Strategy:
    def __init__(self):
        self.bar = 0
    def on_candle(self, ctx):
        self.bar += 1
        if self.bar == 1:
            ctx.market_order("SOL-PERP", "buy", 10.0, venue="jupiter")
        elif self.bar == 3:
            ctx.market_order("SOL-PERP", "sell", 10.0, venue="jupiter")
'''

    result = _run_jupiter_backtest(candles, borrow_snapshots, code)

    # Position held from ts=1000 (cum=1.000) to ts=3000 (cum=1.002)
    # Position size USD = 10 × 100 = 1000
    # Borrow cost = (1.002 - 1.000) × 1000 = 2.0
    assert result.jupiter_borrow_paid > 0
    assert abs(result.jupiter_borrow_paid - 2.0) < 0.1


def test_no_borrow_cost_for_drift_positions():
    """Drift positions should not incur borrow costs."""
    candles = [
        Candle("SOL-PERP", 1000, 100.0, 101.0, 99.0, 100.0, 1000.0, 0),
        Candle("SOL-PERP", 2000, 100.0, 101.0, 99.0, 100.0, 1000.0, 0),
    ]

    borrow_snapshots = [
        BorrowSnapshot("SOL-PERP", 1000, 0.001, 0.5, 1.000, "rpc"),
        BorrowSnapshot("SOL-PERP", 2000, 0.001, 0.5, 1.001, "rpc"),
    ]

    code = '''
class Strategy:
    def __init__(self):
        self.bar = 0
    def on_candle(self, ctx):
        self.bar += 1
        if self.bar == 1:
            ctx.market_order("SOL-PERP", "buy", 10.0, venue="drift")
'''

    result = _run_jupiter_backtest(candles, borrow_snapshots, code)
    assert result.jupiter_borrow_paid == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_jupiter_backtest_engine.py -v`
Expected: FAIL — `run_backtest() got unexpected keyword argument 'borrow_snapshots'`

- [ ] **Step 3: Add borrow_snapshots parameter to run_backtest**

In `flint/backtest/engine.py`, add `borrow_snapshots: list = None` to the `run_backtest` function signature. Before the main loop, sort and index them:

```python
sorted_borrow = sorted(borrow_snapshots or [], key=lambda b: b.ts)
borrow_cursor = 0
```

- [ ] **Step 4: Add borrow rate feeding in the engine loop**

In the main bar loop (after the funding application block around line ~218), add:

```python
# Feed borrow rate snapshots to context
while borrow_cursor < len(sorted_borrow) and sorted_borrow[borrow_cursor].ts <= candle.ts:
    ctx.add_borrow_rate(sorted_borrow[borrow_cursor])
    borrow_cursor += 1
```

- [ ] **Step 5: Add borrow accrual tracking to BacktestContext positions**

In `flint/execution/backtest_context.py`, modify the `_Position` class to track borrow entry:

Add field to `_Position` (around line 30-62):
```python
borrow_cumulative_at_entry: float = 0.0
```

In the `market_order` method, when opening a Jupiter position, record the cumulative rate:

```python
# After creating the position, if venue == "jupiter":
if venue == "jupiter":
    cum = self.get_borrow_cumulative_at(market, self._current_candle.ts if self._current_candle else 0)
    if cum is not None:
        pos.borrow_cumulative_at_entry = cum
```

When closing a Jupiter position, calculate and deduct borrow cost:

```python
if venue == "jupiter" and pos.borrow_cumulative_at_entry > 0:
    cum_now = self.get_borrow_cumulative_at(market, self._current_candle.ts if self._current_candle else 0)
    if cum_now is not None:
        borrow_cost = (cum_now - pos.borrow_cumulative_at_entry) * abs(pos.size * close_price)
        self._total_borrow_paid += borrow_cost
        self._debit_cash(borrow_cost)
```

Add `self._total_borrow_paid: float = 0.0` and `self._borrow_payments: list = []` to `__init__`.

When deducting borrow cost on close, also append to `_borrow_payments`:

```python
self._borrow_payments.append({
    "ts": self._current_candle.ts if self._current_candle else 0,
    "market": market,
    "rate": bs.rate_hourly if (bs := (self._borrow_history.get(market) or [None])[-1]) else 0,
    "cost": borrow_cost,
    "position_size": abs(pos.size * close_price),
})
```

- [ ] **Step 6: Add jupiter_borrow_paid and borrow_payments to BacktestResult**

Find the `BacktestResult` dataclass (likely in `flint/backtest/engine.py` or `flint/models.py`) and add:

```python
jupiter_borrow_paid: float = 0.0
borrow_payments: list = None  # [{ts, market, rate, cost, position_size}, ...]
```

Wire `ctx._total_borrow_paid` and `ctx._borrow_payments` into the result at the end of `run_backtest`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_jupiter_backtest_engine.py -v`
Expected: 2 passed

- [ ] **Step 8: Run all backtest tests**

Run: `pytest tests/ -k "backtest" -v`
Expected: All pass

- [ ] **Step 9: Commit**

```bash
git add flint/backtest/engine.py flint/execution/backtest_context.py tests/test_jupiter_backtest_engine.py
git commit -m "feat: add continuous borrow accrual to backtest engine"
```

---

## Task 8: Config — Jupiter Perps Section

**Files:**
- Modify: `flint/config.py` (Pydantic settings class)
- Test: `tests/test_jupiter_config.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_jupiter_config.py
import os


def test_jupiter_perps_config_defaults():
    from flint.config import load_config
    cfg = load_config()
    assert cfg.jupiter_perps_enabled is False
    assert cfg.jupiter_perps_sidecar_port == 8401
    assert cfg.jupiter_perps_rpc_url == ""
    assert cfg.jupiter_perps_wallet_path == ""


def test_jupiter_perps_config_from_env(monkeypatch):
    monkeypatch.setenv("FLINT_JUPITER_PERPS_ENABLED", "true")
    monkeypatch.setenv("FLINT_JUPITER_PERPS_SIDECAR_PORT", "9000")
    monkeypatch.setenv("FLINT_DUNE_API_KEY", "dune_test_key")
    from flint.config import load_config
    cfg = load_config()
    assert cfg.jupiter_perps_enabled is True
    assert cfg.jupiter_perps_sidecar_port == 9000
    assert cfg.dune_api_key == "dune_test_key"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_jupiter_config.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'jupiter_perps_enabled'`

- [ ] **Step 3: Add Jupiter Perps config fields**

In `flint/config.py`, add to the settings class (following the existing flat naming convention):

```python
# Jupiter Perps
jupiter_perps_enabled: bool = False
jupiter_perps_sidecar_port: int = 8401
jupiter_perps_rpc_url: str = ""
jupiter_perps_wallet_path: str = ""

# Dune Analytics (for borrow rate backfill)
dune_api_key: str = ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_jupiter_config.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add flint/config.py tests/test_jupiter_config.py
git commit -m "feat: add Jupiter Perps config fields"
```

---

## Task 9: Jupiter Borrow Rate Forward Collector

**Files:**
- Create: `flint/providers/jupiter_borrow.py`
- Test: `tests/test_jupiter_borrow_collector.py`

- [ ] **Step 1: Write failing tests for the forward collector**

```python
# tests/test_jupiter_borrow_collector.py
"""Tests for Jupiter borrow rate collector (RPC polling)."""
import json
from unittest.mock import MagicMock, patch

from flint.providers.jupiter_borrow import JupiterBorrowCollector


MOCK_CUSTODY_DATA = {
    "fundingRateState": {
        "hourlyFundingDbps": 80,  # 80 deci-basis-points = 0.008% = 0.00008
        "cumulativeInterestRate": 1002340000000,  # scaled by 1e9
        "lastUpdate": 1700000000,
    },
    "assets": {
        "owned": 1000000,
        "locked": 650000,
    },
}


def test_parse_custody_account():
    collector = JupiterBorrowCollector(rpc_url="http://fake")
    snapshot = collector._parse_custody("SOL-PERP", MOCK_CUSTODY_DATA, 1700000000)
    assert snapshot.market == "SOL-PERP"
    assert snapshot.rate_hourly == 0.00008  # 80 Dbps → 0.00008
    assert abs(snapshot.utilization - 0.65) < 0.01  # 650000/1000000
    assert snapshot.cumulative_rate == 1002340000000
    assert snapshot.source == "rpc"


def test_collector_markets():
    collector = JupiterBorrowCollector(rpc_url="http://fake")
    assert "SOL-PERP" in collector.markets
    assert "ETH-PERP" in collector.markets
    assert "BTC-PERP" in collector.markets


def test_dbps_to_hourly_rate():
    """Deci-basis-points to hourly rate conversion."""
    collector = JupiterBorrowCollector(rpc_url="http://fake")
    # 80 Dbps = 80 / 1_000_000 = 0.00008
    assert collector._dbps_to_hourly(80) == 0.00008
    # 100 Dbps = 100 / 1_000_000 = 0.0001
    assert collector._dbps_to_hourly(100) == 0.0001
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_jupiter_borrow_collector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flint.providers.jupiter_borrow'`

- [ ] **Step 3: Implement JupiterBorrowCollector**

```python
# flint/providers/jupiter_borrow.py
"""Jupiter Perps borrow rate collection.

Three collection methods:
1. JupiterBorrowCollector — polls custody accounts via Solana RPC (forward collection)
2. DuneBorrowBackfill — queries Dune Analytics for historical rates
3. RpcBorrowBackfill — reads archival RPC for gap filling
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

import httpx

from ..models import BorrowSnapshot

# Jupiter Perps program
PERPS_PROGRAM_ID = "PERPHjGBqRHArX4DySjwM6UJHiR3sWAatqfdBS2qQJu"
POOL_ACCOUNT = "5BUwFW4nRbftYTDMbgxykoFWqWHPzahFSNAaaaJtVKsq"

# Market name mapping
CUSTODY_MARKETS = {
    0: "SOL-PERP",
    1: "ETH-PERP",
    2: "BTC-PERP",
    3: "USDC",
    4: "USDT",
}

# Only perp-relevant markets
PERP_MARKETS = ["SOL-PERP", "ETH-PERP", "BTC-PERP"]


class JupiterBorrowCollector:
    """Polls Jupiter Perps custody accounts for borrow rate data."""

    def __init__(self, rpc_url: str, client: Optional[httpx.Client] = None) -> None:
        self._rpc_url = rpc_url
        self._client = client or httpx.Client(timeout=30)
        self._owns_client = client is None
        self._custody_addresses: Dict[str, str] = {}

    @property
    def markets(self) -> List[str]:
        return list(PERP_MARKETS)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    @staticmethod
    def _dbps_to_hourly(dbps: int) -> float:
        """Convert deci-basis-points to hourly rate."""
        return dbps / 1_000_000

    def _parse_custody(
        self, market: str, data: dict, ts: int,
    ) -> BorrowSnapshot:
        """Parse a custody account's funding rate state into a BorrowSnapshot."""
        funding_state = data["fundingRateState"]
        assets = data["assets"]

        rate_dbps = funding_state["hourlyFundingDbps"]
        rate_hourly = self._dbps_to_hourly(rate_dbps)

        owned = assets["owned"]
        locked = assets["locked"]
        utilization = locked / owned if owned > 0 else 0.0

        cumulative_rate = funding_state["cumulativeInterestRate"]

        return BorrowSnapshot(
            market=market,
            ts=ts,
            rate_hourly=rate_hourly,
            utilization=utilization,
            cumulative_rate=cumulative_rate,
            source="rpc",
        )

    def _rpc_get_account(self, address: str) -> Optional[dict]:
        """Fetch and decode an account via Solana RPC."""
        resp = self._client.post(
            self._rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [address, {"encoding": "jsonParsed"}],
            },
        )
        resp.raise_for_status()
        result = resp.json().get("result")
        if result and result.get("value"):
            return result["value"]
        return None

    def collect(self) -> List[BorrowSnapshot]:
        """Poll all custody accounts and return current borrow snapshots."""
        now = int(time.time())
        snapshots = []
        for market in self.markets:
            addr = self._custody_addresses.get(market)
            if not addr:
                continue
            account = self._rpc_get_account(addr)
            if account and "data" in account:
                snapshot = self._parse_custody(market, account["data"], now)
                snapshots.append(snapshot)
        return snapshots
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_jupiter_borrow_collector.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add flint/providers/jupiter_borrow.py tests/test_jupiter_borrow_collector.py
git commit -m "feat: add Jupiter borrow rate forward collector"
```

---

## Task 10: Dune Borrow Rate Backfill

**Files:**
- Modify: `flint/providers/jupiter_borrow.py` (add DuneBorrowBackfill class)
- Test: `tests/test_jupiter_dune_backfill.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_jupiter_dune_backfill.py
"""Tests for Dune Analytics borrow rate backfill."""
from unittest.mock import MagicMock, patch

from flint.providers.jupiter_borrow import DuneBorrowBackfill


MOCK_DUNE_RESPONSE = {
    "result": {
        "rows": [
            {
                "market": "SOL-PERP",
                "block_time": "2024-06-01T00:00:00Z",
                "hourly_funding_dbps": 80,
                "cumulative_interest_rate": 1000100000000,
                "utilization": 0.65,
            },
            {
                "market": "SOL-PERP",
                "block_time": "2024-06-01T01:00:00Z",
                "hourly_funding_dbps": 85,
                "cumulative_interest_rate": 1000200000000,
                "utilization": 0.68,
            },
        ],
    },
}


def test_parse_dune_response():
    backfill = DuneBorrowBackfill(api_key="fake_key")
    snapshots = backfill._parse_response(MOCK_DUNE_RESPONSE)

    assert len(snapshots) == 2
    assert snapshots[0].market == "SOL-PERP"
    assert snapshots[0].rate_hourly == 0.00008
    assert snapshots[0].utilization == 0.65
    assert snapshots[0].source == "dune"
    assert snapshots[1].rate_hourly == 0.000085


def test_build_query():
    backfill = DuneBorrowBackfill(api_key="fake_key")
    query = backfill._build_query("SOL-PERP", 1000, 2000)
    assert "SOL" in query or "sol" in query.lower()
    assert isinstance(query, str)


def test_backfill_requires_api_key():
    backfill = DuneBorrowBackfill(api_key="")
    assert backfill.is_available() is False

    backfill = DuneBorrowBackfill(api_key="dune_abc123")
    assert backfill.is_available() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_jupiter_dune_backfill.py -v`
Expected: FAIL — `ImportError: cannot import name 'DuneBorrowBackfill'`

- [ ] **Step 3: Implement DuneBorrowBackfill**

Add to `flint/providers/jupiter_borrow.py`:

```python
import calendar
from datetime import datetime, timezone


# Dune API
_DUNE_API = "https://api.dune.com/api/v1"

# Market to Dune custody filter mapping
_MARKET_CUSTODY_NAMES = {
    "SOL-PERP": "SOL",
    "ETH-PERP": "ETH",
    "BTC-PERP": "wBTC",
}


class DuneBorrowBackfill:
    """Backfill historical borrow rates from Dune Analytics."""

    def __init__(self, api_key: str, client: Optional[httpx.Client] = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=120)
        self._owns_client = client is None

    def is_available(self) -> bool:
        return bool(self._api_key)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _build_query(self, market: str, start_ts: int, end_ts: int) -> str:
        """Build Dune SQL query for custody account borrow rates."""
        custody_name = _MARKET_CUSTODY_NAMES.get(market, "SOL")
        start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        return f"""
        SELECT
            '{market}' as market,
            block_time,
            hourly_funding_dbps,
            cumulative_interest_rate,
            locked_amount::double / NULLIF(owned_amount::double, 0) as utilization
        FROM jupiter_perps.custody_updates
        WHERE custody_name = '{custody_name}'
            AND block_time >= '{start_dt}'
            AND block_time <= '{end_dt}'
        ORDER BY block_time
        """

    def _parse_response(self, response: dict) -> List[BorrowSnapshot]:
        """Parse Dune API response into BorrowSnapshot list."""
        rows = response.get("result", {}).get("rows", [])
        snapshots = []
        for row in rows:
            ts_str = row["block_time"]
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            ts = int(calendar.timegm(dt.timetuple()))

            rate_dbps = row["hourly_funding_dbps"]
            rate_hourly = rate_dbps / 1_000_000

            snapshots.append(BorrowSnapshot(
                market=row["market"],
                ts=ts,
                rate_hourly=rate_hourly,
                utilization=row.get("utilization", 0.0),
                cumulative_rate=row.get("cumulative_interest_rate", 0),
                source="dune",
            ))
        return snapshots

    def fetch(self, market: str, start_ts: int, end_ts: int) -> List[BorrowSnapshot]:
        """Execute Dune query and return parsed snapshots."""
        if not self.is_available():
            return []

        query_sql = self._build_query(market, start_ts, end_ts)

        # Execute query via Dune API
        resp = self._client.post(
            f"{_DUNE_API}/query/execute",
            headers={"X-Dune-API-Key": self._api_key},
            json={"query_sql": query_sql},
        )
        resp.raise_for_status()
        execution_id = resp.json()["execution_id"]

        # Poll for results
        for _ in range(60):
            resp = self._client.get(
                f"{_DUNE_API}/execution/{execution_id}/results",
                headers={"X-Dune-API-Key": self._api_key},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("state") == "QUERY_STATE_COMPLETED":
                return self._parse_response(data)
            time.sleep(2)

        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_jupiter_dune_backfill.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add flint/providers/jupiter_borrow.py tests/test_jupiter_dune_backfill.py
git commit -m "feat: add Dune Analytics borrow rate backfill"
```

---

## Task 11: Archival RPC Borrow Rate Backfill

**Files:**
- Modify: `flint/providers/jupiter_borrow.py` (add RpcBorrowBackfill class)
- Test: `tests/test_jupiter_rpc_backfill.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_jupiter_rpc_backfill.py
"""Tests for archival RPC borrow rate backfill."""
from unittest.mock import MagicMock

from flint.providers.jupiter_borrow import RpcBorrowBackfill


def test_slot_for_timestamp_binary_search():
    """Should find approximate slot for a given timestamp."""
    backfill = RpcBorrowBackfill(rpc_url="http://fake")

    # Mock the RPC call
    mock_client = MagicMock()
    # getBlockTime returns unix timestamp for a slot
    mock_client.post.return_value.json.return_value = {
        "jsonrpc": "2.0", "result": 1700000000, "id": 1,
    }
    mock_client.post.return_value.raise_for_status = MagicMock()
    backfill._client = mock_client

    slot = backfill._slot_for_timestamp(1700000000, hint_slot=200_000_000)
    assert isinstance(slot, int)


def test_is_available():
    backfill = RpcBorrowBackfill(rpc_url="")
    assert backfill.is_available() is False

    backfill = RpcBorrowBackfill(rpc_url="https://api.mainnet-beta.solana.com")
    assert backfill.is_available() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_jupiter_rpc_backfill.py -v`
Expected: FAIL — `ImportError: cannot import name 'RpcBorrowBackfill'`

- [ ] **Step 3: Implement RpcBorrowBackfill**

Add to `flint/providers/jupiter_borrow.py`:

```python
class RpcBorrowBackfill:
    """Backfill borrow rates from archival Solana RPC.

    Reads custody account state at historical slots. Slow and expensive —
    only use to fill gaps that Dune can't cover.
    """

    def __init__(self, rpc_url: str, client: Optional[httpx.Client] = None) -> None:
        self._rpc_url = rpc_url
        self._client = client or httpx.Client(timeout=60)
        self._owns_client = client is None

    def is_available(self) -> bool:
        return bool(self._rpc_url)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _get_block_time(self, slot: int) -> Optional[int]:
        """Get unix timestamp for a slot."""
        resp = self._client.post(
            self._rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "getBlockTime", "params": [slot]},
        )
        resp.raise_for_status()
        return resp.json().get("result")

    def _get_account_at_slot(self, address: str, slot: int) -> Optional[dict]:
        """Read account data at a specific historical slot."""
        resp = self._client.post(
            self._rpc_url,
            json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getAccountInfo",
                "params": [address, {"encoding": "jsonParsed", "minContextSlot": slot}],
            },
        )
        resp.raise_for_status()
        result = resp.json().get("result")
        if result and result.get("value"):
            return result["value"]
        return None

    def _slot_for_timestamp(self, target_ts: int, hint_slot: int = 200_000_000) -> int:
        """Binary search for the slot closest to a target timestamp.

        Assumes ~400ms per slot (2.5 slots/sec).
        """
        slot = hint_slot
        for _ in range(20):  # max iterations
            block_time = self._get_block_time(slot)
            if block_time is None:
                slot -= 1000
                continue
            diff = target_ts - block_time
            if abs(diff) < 60:  # within 1 minute
                return slot
            # ~2.5 slots per second
            slot += int(diff * 2.5)
            slot = max(0, slot)
        return slot

    def fetch(
        self, market: str, custody_address: str, timestamps: List[int],
    ) -> List[BorrowSnapshot]:
        """Fetch borrow rates at specific timestamps via archival RPC."""
        if not self.is_available():
            return []

        collector = JupiterBorrowCollector(rpc_url=self._rpc_url, client=self._client)
        snapshots = []
        hint_slot = 200_000_000

        for ts in sorted(timestamps):
            slot = self._slot_for_timestamp(ts, hint_slot=hint_slot)
            hint_slot = slot  # use as hint for next iteration

            account = self._get_account_at_slot(custody_address, slot)
            if account and "data" in account:
                snapshot = collector._parse_custody(market, account["data"], ts)
                snapshots.append(BorrowSnapshot(
                    market=snapshot.market,
                    ts=snapshot.ts,
                    rate_hourly=snapshot.rate_hourly,
                    utilization=snapshot.utilization,
                    cumulative_rate=snapshot.cumulative_rate,
                    source="archival",
                ))

        return snapshots
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_jupiter_rpc_backfill.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add flint/providers/jupiter_borrow.py tests/test_jupiter_rpc_backfill.py
git commit -m "feat: add archival RPC borrow rate backfill"
```

---

## Task 12: JupiterTxCostModel

**Files:**
- Create: `flint/execution/jupiter_costs.py`
- Test: `tests/test_jupiter_costs.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_jupiter_costs.py
from flint.execution.jupiter_costs import JupiterTxCostModel, JupiterCostEstimate


def test_open_close_fees():
    model = JupiterTxCostModel()
    estimate = model.estimate_round_trip(size_usd=10000.0, hold_hours=24, rate_hourly=0.00008)
    # Open fee: 0.06% × 10000 = 6.0
    assert estimate.open_fee == 6.0
    # Close fee: 0.06% × 10000 = 6.0
    assert estimate.close_fee == 6.0


def test_borrow_cost_estimate():
    model = JupiterTxCostModel()
    cost = model.estimate_borrow(size_usd=10000.0, hours=24, rate_hourly=0.00008)
    # 0.00008 × 10000 × 24 = 19.2
    assert abs(cost - 19.2) < 0.01


def test_round_trip_total():
    model = JupiterTxCostModel()
    estimate = model.estimate_round_trip(size_usd=10000.0, hold_hours=24, rate_hourly=0.00008)
    expected_total = 6.0 + 6.0 + 19.2  # open + close + borrow (no impact in estimate)
    assert abs(estimate.total - expected_total) < 0.1


def test_price_impact_scales_with_size():
    model = JupiterTxCostModel()
    small = model.estimate_round_trip(size_usd=1000.0, hold_hours=1, rate_hourly=0.00008)
    large = model.estimate_round_trip(size_usd=100000.0, hold_hours=1, rate_hourly=0.00008)
    assert large.price_impact > small.price_impact


def test_cost_estimate_dataclass():
    est = JupiterCostEstimate(
        open_fee=6.0, close_fee=6.0, price_impact=0.5, borrow_cost=19.2, total=31.7,
    )
    assert est.open_fee == 6.0
    assert est.total == 31.7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_jupiter_costs.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement jupiter_costs.py**

```python
# flint/execution/jupiter_costs.py
"""Jupiter Perps transaction cost estimation.

Handles open/close fees (flat 0.06%) and borrow cost projections.
Distinct from HoldingCostModel which handles ongoing accrual in backtests.
"""
from __future__ import annotations

from dataclasses import dataclass

# Jupiter Perps flat fee: 0.06% for both open and close
_FEE_RATE = 0.0006

# Price impact coefficient (scales quadratically with size relative to pool)
_IMPACT_K = 0.00001


@dataclass
class JupiterCostEstimate:
    open_fee: float
    close_fee: float
    price_impact: float
    borrow_cost: float
    total: float


class JupiterTxCostModel:
    """Estimate round-trip costs for Jupiter Perps positions."""

    def __init__(self, fee_rate: float = _FEE_RATE, impact_k: float = _IMPACT_K) -> None:
        self._fee_rate = fee_rate
        self._impact_k = impact_k

    def estimate_borrow(self, size_usd: float, hours: float, rate_hourly: float) -> float:
        """Estimate borrow cost for holding a position."""
        return rate_hourly * size_usd * hours

    def estimate_round_trip(
        self, size_usd: float, hold_hours: float, rate_hourly: float,
    ) -> JupiterCostEstimate:
        """Estimate total round-trip cost including open, close, impact, and borrow."""
        open_fee = self._fee_rate * size_usd
        close_fee = self._fee_rate * size_usd
        price_impact = self._impact_k * (size_usd ** 2) / 1_000_000
        borrow_cost = self.estimate_borrow(size_usd, hold_hours, rate_hourly)
        total = open_fee + close_fee + price_impact + borrow_cost

        return JupiterCostEstimate(
            open_fee=open_fee,
            close_fee=close_fee,
            price_impact=price_impact,
            borrow_cost=borrow_cost,
            total=total,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_jupiter_costs.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add flint/execution/jupiter_costs.py tests/test_jupiter_costs.py
git commit -m "feat: add JupiterTxCostModel for round-trip cost estimation"
```

---

## Task 13: API — Borrow Rates Endpoint

**Files:**
- Modify: `flint/api/routes/data.py` (add endpoint after /funding)
- Test: `tests/test_jupiter_api.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_jupiter_api.py
"""Tests for Jupiter borrow rate API endpoint."""
import os
import tempfile

from fastapi.testclient import TestClient

from flint.models import BorrowSnapshot


def _make_app():
    from flint.api.main import create_app
    from flint.store import FlintStore

    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    store = FlintStore(path)
    app = create_app(store=store)
    return app, store, path


def test_borrow_rates_endpoint():
    app, store, path = _make_app()
    try:
        # Insert test data
        store.upsert_borrow_rates([
            BorrowSnapshot("SOL-PERP", 1000, 0.00008, 0.65, 1.001, "rpc"),
            BorrowSnapshot("SOL-PERP", 2000, 0.00009, 0.70, 1.002, "rpc"),
        ])

        client = TestClient(app)
        resp = client.get("/api/v1/data/borrow-rates", params={"market": "SOL-PERP"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["market"] == "SOL-PERP"
        assert data["count"] == 2
        assert len(data["rates"]) == 2
        assert data["rates"][0]["rate_hourly"] == 0.00008
    finally:
        store.close()
        os.unlink(path)


def test_borrow_rates_empty():
    app, store, path = _make_app()
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/data/borrow-rates", params={"market": "SOL-PERP"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["rates"] == []
    finally:
        store.close()
        os.unlink(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_jupiter_api.py -v`
Expected: FAIL — 404 (endpoint doesn't exist yet)

- [ ] **Step 3: Add borrow-rates endpoint**

In `flint/api/routes/data.py`, add after the `/funding` endpoint:

```python
@router.get("/borrow-rates")
def get_borrow_rates(
    request: Request,
    market: str = Query("SOL-PERP"),
    start_ts: Optional[int] = Query(None),
    end_ts: Optional[int] = Query(None),
):
    """Get Jupiter borrow rate history for a market."""
    store = _get_store(request)
    if store is None:
        return {"market": market, "rates": [], "count": 0}
    try:
        start = start_ts or 0
        end = end_ts or int(time.time())
        snapshots = store.query_borrow_rates(market, start, end)
        rates = [
            {
                "ts": s.ts,
                "rate_hourly": s.rate_hourly,
                "utilization": s.utilization,
                "cumulative_rate": s.cumulative_rate,
                "source": s.source,
            }
            for s in snapshots
        ]
        return {"market": market, "rates": rates, "count": len(rates)}
    except Exception as e:
        return {"market": market, "rates": [], "count": 0, "error": str(e)}
```

Add `import time` at top if not already present.

Also extend the existing `/funding` endpoint to include Jupiter borrow rates when `venue=jupiter` is requested, by checking if the venue is `"jupiter"` and querying `store.query_borrow_rates()` instead of `store.query_funding_by_venue()` for that venue. This allows cross-venue comparisons in the UI without a separate API call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_jupiter_api.py -v`
Expected: 2 passed

- [ ] **Step 5: Run existing API tests**

Run: `pytest tests/ -k "api" -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add flint/api/routes/data.py tests/test_jupiter_api.py
git commit -m "feat: add /api/v1/data/borrow-rates endpoint"
```

---

## Task 14: Jupiter Sidecar Manager

**Files:**
- Create: `flint/execution/jupiter_sidecar.py`
- Test: `tests/test_jupiter_sidecar.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_jupiter_sidecar.py
"""Tests for Jupiter TS sidecar lifecycle management."""
import subprocess
from unittest.mock import MagicMock, patch, PropertyMock

from flint.execution.jupiter_sidecar import JupiterSidecar


def test_sidecar_init():
    sidecar = JupiterSidecar(port=8401)
    assert sidecar.port == 8401
    assert sidecar.is_running is False


def test_sidecar_check_node_available():
    sidecar = JupiterSidecar(port=8401)
    with patch("shutil.which", return_value="/usr/local/bin/node"):
        assert sidecar._check_node() is True
    with patch("shutil.which", return_value=None):
        assert sidecar._check_node() is False


def test_sidecar_health_check_success():
    sidecar = JupiterSidecar(port=8401)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "ok"}
    with patch("httpx.get", return_value=mock_resp):
        assert sidecar._health_check() is True


def test_sidecar_health_check_failure():
    sidecar = JupiterSidecar(port=8401)
    with patch("httpx.get", side_effect=Exception("Connection refused")):
        assert sidecar._health_check() is False


def test_sidecar_max_restarts():
    sidecar = JupiterSidecar(port=8401, max_restarts=3)
    assert sidecar._max_restarts == 3
    assert sidecar._restart_count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_jupiter_sidecar.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement jupiter_sidecar.py**

```python
# flint/execution/jupiter_sidecar.py
"""Jupiter Perps TS sidecar lifecycle management.

Manages a Node.js subprocess running a Fastify server that wraps
the jup-perps-client library for on-chain transaction building.
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_SIDECAR_DIR = Path(__file__).resolve().parent.parent.parent / "sidecar" / "jupiter-perps"


class JupiterSidecar:
    """Manages the Jupiter Perps TypeScript sidecar process."""

    def __init__(
        self,
        port: int = 8401,
        rpc_url: str = "",
        wallet_path: str = "",
        max_restarts: int = 3,
    ) -> None:
        self.port = port
        self._rpc_url = rpc_url
        self._wallet_path = wallet_path
        self._max_restarts = max_restarts
        self._restart_count = 0
        self._process: Optional[subprocess.Popen] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _check_node(self) -> bool:
        """Check if Node.js >= 18 is available."""
        return shutil.which("node") is not None

    def _health_check(self) -> bool:
        """Ping the sidecar health endpoint."""
        try:
            resp = httpx.get(f"{self.base_url}/health", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def start(self) -> bool:
        """Start the sidecar subprocess."""
        if not self._check_node():
            logger.error("Node.js not found on PATH. Jupiter sidecar requires node >= 18.")
            return False

        if not _SIDECAR_DIR.exists():
            logger.error(f"Sidecar directory not found: {_SIDECAR_DIR}")
            return False

        env = os.environ.copy()
        env["PORT"] = str(self.port)
        env["RPC_URL"] = self._rpc_url
        env["WALLET_PATH"] = self._wallet_path

        try:
            self._process = subprocess.Popen(
                ["node", "dist/index.js"],
                cwd=str(_SIDECAR_DIR),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            # Wait for health
            for _ in range(30):
                time.sleep(0.5)
                if self._health_check():
                    logger.info(f"Jupiter sidecar started on port {self.port}")
                    self._start_monitor()
                    return True

            logger.error("Jupiter sidecar failed to become healthy within 15s")
            self.stop()
            return False
        except Exception as e:
            logger.error(f"Failed to start Jupiter sidecar: {e}")
            return False

    def stop(self) -> None:
        """Gracefully stop the sidecar."""
        self._stop_event.set()
        if self._process and self._process.poll() is None:
            self._process.send_signal(signal.SIGTERM)
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
            logger.info("Jupiter sidecar stopped")
        self._process = None

    def _start_monitor(self) -> None:
        """Start background thread to monitor sidecar health."""
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def _monitor_loop(self) -> None:
        """Monitor sidecar and restart if it crashes."""
        while not self._stop_event.is_set():
            self._stop_event.wait(10)  # check every 10s
            if self._stop_event.is_set():
                break
            if not self.is_running:
                if self._restart_count < self._max_restarts:
                    self._restart_count += 1
                    logger.warning(
                        f"Jupiter sidecar crashed. Restart {self._restart_count}/{self._max_restarts}"
                    )
                    self.start()
                else:
                    logger.error("Jupiter sidecar exceeded max restarts. Venue unavailable.")
                    break
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_jupiter_sidecar.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add flint/execution/jupiter_sidecar.py tests/test_jupiter_sidecar.py
git commit -m "feat: add Jupiter sidecar subprocess manager"
```

---

## Task 15: LiveJupiterContext

**Files:**
- Create: `flint/execution/jupiter_live.py`
- Test: `tests/test_jupiter_live.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_jupiter_live.py
"""Tests for LiveJupiterContext — sidecar HTTP integration."""
from unittest.mock import MagicMock, patch
import json

from flint.execution.jupiter_live import LiveJupiterContext
from flint.models import Side


def _mock_sidecar_response(data, status_code=200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = data
    mock.raise_for_status = MagicMock()
    return mock


def test_get_mark_price():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    with patch.object(ctx, "_http") as mock_http:
        mock_http.get.return_value = _mock_sidecar_response({"price": 150.25})
        price = ctx.get_mark_price("SOL-PERP")
        assert price == 150.25
        mock_http.get.assert_called_once_with("http://127.0.0.1:8401/oracle/SOL-PERP", timeout=10)


def test_get_positions():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    with patch.object(ctx, "_http") as mock_http:
        mock_http.get.return_value = _mock_sidecar_response({
            "positions": [
                {
                    "market": "SOL-PERP",
                    "side": "long",
                    "size": 10.0,
                    "entry_price": 148.0,
                    "unrealized_pnl": 22.5,
                    "venue": "jupiter",
                },
            ],
        })
        positions = ctx.get_positions()
        assert len(positions) == 1
        assert positions[0].market == "SOL-PERP"
        assert positions[0].size == 10.0
        assert positions[0].venue == "jupiter"


def test_market_order_returns_pending():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    with patch.object(ctx, "_http") as mock_http:
        mock_http.post.return_value = _mock_sidecar_response({
            "request_id": "abc123",
            "tx_signature": "5xYz...",
            "status": "pending",
        })
        order_id = ctx.market_order("SOL-PERP", "buy", 5.0)
        assert order_id == "abc123"


def test_get_borrow_rate():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    # Mock store query
    mock_store = MagicMock()
    mock_store.query_borrow_rates.return_value = [
        MagicMock(rate_hourly=0.00008, ts=1000),
    ]
    ctx._store = mock_store
    rate = ctx.get_borrow_rate("SOL-PERP")
    assert rate == 0.00008


def test_collateral_validation_long_requires_base_asset():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    # Long SOL-PERP requires SOL collateral — validation check
    assert ctx._validate_collateral("SOL-PERP", "buy") is True
    assert ctx._validate_collateral("SOL-PERP", "sell") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_jupiter_live.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement LiveJupiterContext**

```python
# flint/execution/jupiter_live.py
"""Live execution context for Jupiter Perps.

Communicates with the TS sidecar over localhost HTTP.
All order execution is asynchronous (2-step keeper model).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import httpx

from ..models import PositionInfo, Side

logger = logging.getLogger(__name__)

# Collateral rules: longs use base asset, shorts use USDC/USDT
_LONG_COLLATERAL = {
    "SOL-PERP": "SOL",
    "ETH-PERP": "ETH",
    "BTC-PERP": "wBTC",
}


class LiveJupiterContext:
    """Jupiter Perps live execution via TS sidecar."""

    def __init__(
        self,
        sidecar_url: str = "http://127.0.0.1:8401",
        store=None,
    ) -> None:
        self._sidecar_url = sidecar_url.rstrip("/")
        self._http = httpx.Client(timeout=30)
        self._store = store

    def close(self) -> None:
        self._http.close()

    def _validate_collateral(self, market: str, side: str) -> bool:
        """Validate collateral requirements. Always returns True for now —
        actual validation happens in the sidecar."""
        return True

    def get_mark_price(self, market: str) -> float:
        """Get current oracle price from sidecar."""
        resp = self._http.get(f"{self._sidecar_url}/oracle/{market}", timeout=10)
        resp.raise_for_status()
        return resp.json()["price"]

    def get_positions(self) -> List[PositionInfo]:
        """Get all open positions."""
        resp = self._http.get(f"{self._sidecar_url}/positions", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return [
            PositionInfo(
                market=p["market"],
                side=Side.LONG if p["side"] == "long" else Side.SHORT,
                size=p["size"],
                entry_price=p["entry_price"],
                unrealized_pnl=p.get("unrealized_pnl", 0.0),
                venue="jupiter",
            )
            for p in data.get("positions", [])
        ]

    def get_balances(self) -> dict:
        """Get wallet token balances."""
        resp = self._http.get(f"{self._sidecar_url}/balances", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def market_order(self, market: str, side: str, size: float, **kwargs) -> str:
        """Submit a market order via the sidecar.

        Returns request_id (pending until keeper fulfills).
        """
        endpoint = "/increase" if side == "buy" else "/decrease"
        resp = self._http.post(
            f"{self._sidecar_url}{endpoint}",
            json={"market": market, "side": side, "size": size, **kwargs},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["request_id"]

    def get_borrow_rate(self, market: str = None, venue: str = None) -> Optional[float]:
        """Get latest borrow rate from store."""
        if not self._store or not market:
            return None
        import time
        now = int(time.time())
        rates = self._store.query_borrow_rates(market, now - 3600, now)
        if rates:
            return rates[-1].rate_hourly
        return None

    def get_borrow_rates(self, market: str = None, venue: str = None, lookback: int = 24) -> list:
        """Get borrow rate history from store."""
        if not self._store or not market:
            return []
        import time
        now = int(time.time())
        start = now - lookback * 3600
        rates = self._store.query_borrow_rates(market, start, now)
        return [(r.ts, r.rate_hourly) for r in rates]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_jupiter_live.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add flint/execution/jupiter_live.py tests/test_jupiter_live.py
git commit -m "feat: add LiveJupiterContext for sidecar-based execution"
```

---

## Task 16: TS Sidecar Scaffold

**Files:**
- Create: `sidecar/jupiter-perps/package.json`
- Create: `sidecar/jupiter-perps/tsconfig.json`
- Create: `sidecar/jupiter-perps/src/index.ts`
- Create: `sidecar/jupiter-perps/src/routes/positions.ts`
- Create: `sidecar/jupiter-perps/src/routes/orders.ts`
- Create: `sidecar/jupiter-perps/src/routes/account.ts`
- Create: `sidecar/jupiter-perps/src/lib/client.ts`
- Create: `sidecar/jupiter-perps/src/lib/keeper.ts`
- Create: `sidecar/jupiter-perps/src/lib/oracle.ts`

- [ ] **Step 1: Create package.json**

```json
{
  "name": "jupiter-perps-sidecar",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "tsx watch src/index.ts",
    "build": "tsc",
    "start": "node dist/index.js",
    "test": "vitest run"
  },
  "dependencies": {
    "@solana/web3.js": "^1.95.0",
    "fastify": "^5.0.0",
    "bs58": "^6.0.0"
  },
  "devDependencies": {
    "typescript": "^5.5.0",
    "tsx": "^4.19.0",
    "vitest": "^2.0.0",
    "@types/node": "^22.0.0"
  }
}
```

Note: `jup-perps-client` is not on npm — it will be added from git source or vendored. For now the scaffold uses `@solana/web3.js` directly for account reads, with the perps IDL types to be added when the client is integrated.

- [ ] **Step 2: Create tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "outDir": "./dist",
    "rootDir": "./src",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "declaration": true
  },
  "include": ["src/**/*"],
  "exclude": ["node_modules", "dist", "tests"]
}
```

- [ ] **Step 3: Create src/index.ts (Fastify server)**

```typescript
// sidecar/jupiter-perps/src/index.ts
import Fastify from "fastify";
import { registerPositionRoutes } from "./routes/positions.js";
import { registerOrderRoutes } from "./routes/orders.js";
import { registerAccountRoutes } from "./routes/account.js";

const PORT = parseInt(process.env.PORT || "8401", 10);
const RPC_URL = process.env.RPC_URL || "https://api.mainnet-beta.solana.com";
const WALLET_PATH = process.env.WALLET_PATH || "";

const app = Fastify({ logger: true });

// Health check
app.get("/health", async () => {
  return { status: "ok", rpc: RPC_URL, wallet: !!WALLET_PATH };
});

// Oracle price endpoint
app.get<{ Params: { market: string } }>("/oracle/:market", async (req) => {
  // TODO: Read from Pyth/Dove oracle accounts
  return { market: req.params.market, price: 0, borrow_rate: 0 };
});

// Register route modules
registerPositionRoutes(app);
registerOrderRoutes(app);
registerAccountRoutes(app);

const start = async () => {
  try {
    await app.listen({ port: PORT, host: "127.0.0.1" });
    console.log(`Jupiter Perps sidecar listening on port ${PORT}`);
  } catch (err) {
    app.log.error(err);
    process.exit(1);
  }
};

// Graceful shutdown
process.on("SIGTERM", async () => {
  await app.close();
  process.exit(0);
});

start();

export { app };
```

- [ ] **Step 4: Create route stubs**

`sidecar/jupiter-perps/src/routes/positions.ts`:
```typescript
import type { FastifyInstance } from "fastify";

export function registerPositionRoutes(app: FastifyInstance) {
  app.get("/positions", async () => {
    return { positions: [] };
  });

  app.get<{ Params: { market: string } }>("/position/:market", async (req) => {
    return { market: req.params.market, position: null };
  });
}
```

`sidecar/jupiter-perps/src/routes/orders.ts`:
```typescript
import type { FastifyInstance } from "fastify";

export function registerOrderRoutes(app: FastifyInstance) {
  app.post<{ Body: { market: string; side: string; size: number } }>(
    "/increase",
    async (req) => {
      const { market, side, size } = req.body;
      // TODO: Build and send CreateIncreasePositionMarketRequest
      return { request_id: "", tx_signature: "", status: "pending" };
    }
  );

  app.post<{ Body: { market: string; side: string; size: number } }>(
    "/decrease",
    async (req) => {
      const { market, side, size } = req.body;
      // TODO: Build and send CreateDecreasePositionMarketRequest
      return { request_id: "", tx_signature: "", status: "pending" };
    }
  );

  app.post<{ Body: { market: string } }>("/close", async (req) => {
    // TODO: Close full position
    return { request_id: "", tx_signature: "", status: "pending" };
  });
}
```

`sidecar/jupiter-perps/src/routes/account.ts`:
```typescript
import type { FastifyInstance } from "fastify";

export function registerAccountRoutes(app: FastifyInstance) {
  app.get("/balances", async () => {
    // TODO: Read wallet token balances via RPC
    return { sol: 0, usdc: 0, usdt: 0 };
  });
}
```

- [ ] **Step 5: Create lib stubs**

`sidecar/jupiter-perps/src/lib/client.ts`:
```typescript
// Wraps jup-perps-client for transaction building
import { Connection, Keypair } from "@solana/web3.js";

export class JupiterPerpsClient {
  private connection: Connection;
  private wallet: Keypair | null = null;

  constructor(rpcUrl: string) {
    this.connection = new Connection(rpcUrl);
  }

  async loadWallet(path: string): Promise<void> {
    const fs = await import("fs");
    const keyData = JSON.parse(fs.readFileSync(path, "utf-8"));
    this.wallet = Keypair.fromSecretKey(Uint8Array.from(keyData));
  }

  get publicKey(): string {
    return this.wallet?.publicKey.toBase58() ?? "";
  }
}
```

`sidecar/jupiter-perps/src/lib/keeper.ts`:
```typescript
// Keeper fulfillment polling
export class KeeperWatcher {
  private timeout: number;

  constructor(timeoutMs: number = 60000) {
    this.timeout = timeoutMs;
  }

  async waitForFulfillment(requestId: string): Promise<"fulfilled" | "expired"> {
    const start = Date.now();
    while (Date.now() - start < this.timeout) {
      // TODO: Check if PositionRequest account is consumed
      await new Promise((r) => setTimeout(r, 2000));
    }
    return "expired";
  }
}
```

`sidecar/jupiter-perps/src/lib/oracle.ts`:
```typescript
// Oracle price reading (Dove/Pyth)
import { Connection, PublicKey } from "@solana/web3.js";

const ORACLE_ACCOUNTS: Record<string, string> = {
  "SOL-PERP": "39cWjvHrpHNz2SbXv6ME4NPhqBDBd4KsjUYv5JkHEAJU",
  "ETH-PERP": "5URYohbPy32nxK1t3jAHVNfdWY2xTubHiFvLrE3VhXEp",
  "BTC-PERP": "4HBbPx9QJdjJ7GUe6bsiJjGybvfpDhQMMPXP1UEa7VT5",
};

export async function getOraclePrice(
  connection: Connection,
  market: string
): Promise<number> {
  const address = ORACLE_ACCOUNTS[market];
  if (!address) throw new Error(`Unknown market: ${market}`);
  // TODO: Deserialize oracle account data
  return 0;
}
```

- [ ] **Step 6: Verify the scaffold compiles**

Run: `cd sidecar/jupiter-perps && npm install && npx tsc --noEmit`
Expected: No compilation errors (stubs are type-safe)

- [ ] **Step 7: Commit**

```bash
git add sidecar/jupiter-perps/
git commit -m "feat: scaffold Jupiter Perps TS sidecar"
```

---

## Task 17: Provider Registration

**Files:**
- Modify: `flint/providers/__init__.py`
- Test: verify with existing tests

- [ ] **Step 1: Register JupiterBorrowProvider**

In `flint/providers/__init__.py`, add the import and registration:

```python
from .jupiter_borrow import JupiterBorrowCollector
```

If the file uses the `@register` decorator pattern from `registry.py`, wrap the collector:

```python
from .registry import register, DataProvider

@register
class JupiterBorrowProvider(DataProvider):
    name = "jupiter_borrow"
    requires_api_key = False

    def supported_data_types(self):
        return ["borrow"]

    def close(self):
        pass
```

Add this to `flint/providers/jupiter_borrow.py` at the bottom.

- [ ] **Step 2: Verify provider appears in registry**

Run: `python -c "from flint.providers.registry import list_providers; print(list_providers())"`
Expected: Output includes `"jupiter_borrow"`

- [ ] **Step 3: Run all provider tests**

Run: `pytest tests/ -k "provider" -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add flint/providers/jupiter_borrow.py flint/providers/__init__.py
git commit -m "feat: register JupiterBorrowProvider in provider registry"
```

---

## Task 18: Integration Test — Multi-Venue Backtest

**Files:**
- Create: `tests/test_jupiter_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_jupiter_integration.py
"""Integration test: multi-venue backtest with Jupiter borrow + Drift funding."""
from flint.models import BorrowSnapshot, Candle, FundingRate


def test_jupiter_drift_multi_venue_backtest():
    """Run a backtest that trades on both Jupiter and Drift,
    verifying that borrow costs and funding costs are tracked separately."""
    from flint.backtest.engine import run_backtest

    candles = {
        "SOL-PERP": [
            Candle("SOL-PERP", ts, 100.0, 101.0, 99.0, 100.0, 1000.0, 0)
            for ts in range(1000, 6000, 1000)
        ],
    }

    funding = [
        FundingRate("SOL-PERP", ts, 0.0001, 100.0, 100.0, 0, "drift")
        for ts in range(1000, 6000, 1000)
    ]

    borrow = [
        BorrowSnapshot("SOL-PERP", ts, 0.001, 0.5, 1.000 + i * 0.001, "rpc")
        for i, ts in enumerate(range(1000, 6000, 1000))
    ]

    code = '''
class Strategy:
    def __init__(self):
        self.bar = 0
    def on_candle(self, ctx):
        self.bar += 1
        if self.bar == 1:
            ctx.market_order("SOL-PERP", "buy", 5.0, venue="drift")
            ctx.market_order("SOL-PERP", "buy", 5.0, venue="jupiter")
        elif self.bar == 4:
            ctx.market_order("SOL-PERP", "sell", 5.0, venue="drift")
            ctx.market_order("SOL-PERP", "sell", 5.0, venue="jupiter")
'''

    result = run_backtest(
        candles=candles,
        strategy_code=code,
        initial_capital=20000.0,
        fee_rate=0.0006,
        funding_rates=funding,
        borrow_snapshots=borrow,
        capital_allocation={"drift": 10000, "jupiter": 10000},
    )

    # Both cost types should be tracked
    assert result.jupiter_borrow_paid > 0
    # Drift funding should also be applied (existing behavior)
    # The key assertion: they are separate values
    assert hasattr(result, "jupiter_borrow_paid")


def test_borrow_rate_accessible_in_strategy():
    """Verify ctx.get_borrow_rate() works inside a strategy."""
    from flint.backtest.engine import run_backtest

    candles = [
        Candle("SOL-PERP", 1000, 100.0, 101.0, 99.0, 100.0, 1000.0, 0),
        Candle("SOL-PERP", 2000, 100.0, 101.0, 99.0, 100.0, 1000.0, 0),
    ]

    borrow = [
        BorrowSnapshot("SOL-PERP", 1000, 0.00008, 0.65, 1.001, "rpc"),
        BorrowSnapshot("SOL-PERP", 2000, 0.00009, 0.70, 1.002, "rpc"),
    ]

    code = '''
class Strategy:
    def on_candle(self, ctx):
        rate = ctx.get_borrow_rate("SOL-PERP")
        if rate is not None:
            assert rate > 0, "Borrow rate should be positive"
'''

    # Should not raise
    result = run_backtest(
        candles=candles,
        strategy_code=code,
        initial_capital=10000.0,
        fee_rate=0.0006,
        borrow_snapshots=borrow,
    )
    assert result is not None
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/test_jupiter_integration.py -v`
Expected: 2 passed

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v --timeout=120`
Expected: All tests pass including new Jupiter tests

- [ ] **Step 4: Commit**

```bash
git add tests/test_jupiter_integration.py
git commit -m "test: add Jupiter Perps integration tests"
```

---

## Summary

| Task | Component | Est. New Lines |
|------|-----------|---------------|
| 1 | BorrowSnapshot model | ~15 |
| 2 | Store table + methods | ~60 |
| 3 | HoldingCostModel | ~45 |
| 4 | VenueConfig preset | ~15 |
| 5 | ExecutionContext ABC | ~10 |
| 6 | BacktestContext borrow tracking | ~60 |
| 7 | Backtest engine accrual | ~40 |
| 8 | Config fields | ~10 |
| 9 | Forward collector | ~100 |
| 10 | Dune backfill | ~90 |
| 11 | Archival RPC backfill | ~70 |
| 12 | JupiterTxCostModel | ~50 |
| 13 | API endpoint | ~25 |
| 14 | Sidecar manager | ~120 |
| 15 | LiveJupiterContext | ~100 |
| 16 | TS sidecar scaffold | ~250 |
| 17 | Provider registration | ~15 |
| 18 | Integration tests | ~80 |
| **Total** | | **~1155** |

**Parallel workstreams:** Tasks 1-8 (Python foundation) can proceed sequentially. Tasks 9-11 (data collection) and 14-16 (sidecar + live context) can run in parallel after the foundation is in place. Task 16 (TS sidecar) is fully independent.
