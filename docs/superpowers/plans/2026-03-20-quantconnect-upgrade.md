# QuantConnect-Style Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a docs page, upgrade Backtest Lab into a Strategy Lab with Monaco editor, and add a background data collection service — making Flint a QuantConnect-like platform for quants entering Solana.

**Architecture:** The Docs page is a new React route with sidebar navigation and structured content. The Strategy Lab replaces the current BacktestLab with a split-view (Monaco editor left, config+results right). User strategies are saved to disk and loaded dynamically via `exec()` with AST validation. The data collector runs as an asyncio background task inside the FastAPI lifespan, writing to DuckDB via a shared singleton store with WAL mode.

**Tech Stack:** Python (FastAPI, DuckDB, ast module), React 19, TypeScript, Monaco Editor (`@monaco-editor/react`), Tailwind CSS, Recharts

**Spec:** `docs/superpowers/specs/2026-03-20-quantconnect-upgrade-design.md`

---

## File Map

### New Files

| File | Responsibility |
|------|---------------|
| `flint/models.py` (modify) | Add `OraclePrice`, `CollectorStatus` dataclasses |
| `flint/store.py` (modify) | Add 3 new tables, WAL mode, new upsert/query methods |
| `flint/strategy/loader.py` | AST-based strategy validation + `exec()` loading |
| `flint/collector/__init__.py` | Package init |
| `flint/collector/service.py` | Async collector loop, scheduling, backfill |
| `flint/collector/tasks.py` | Individual collection tasks (candles, funding, orderbook, oracle) |
| `flint/api/main.py` (modify) | Lifespan, singleton store, new route registration |
| `flint/api/routes/backtest.py` (modify) | Add `code` field, user strategy loading |
| `flint/api/routes/user_strategies.py` | CRUD endpoints for user strategies |
| `flint/api/routes/collector.py` | Collector status/trigger endpoints |
| `strategies/user/.gitkeep` | User strategy directory |
| `ui/src/pages/Docs.tsx` | Documentation page with sidebar |
| `ui/src/data/docs-content.ts` | Structured documentation content |
| `ui/src/components/DocsSidebar.tsx` | Collapsible sidebar nav |
| `ui/src/components/DocsContent.tsx` | Content renderer |
| `ui/src/components/CodeEditor.tsx` | Monaco editor wrapper |
| `ui/src/components/CollectorStatus.tsx` | Data collection status panel |
| `ui/src/hooks/useStrategies.ts` | Strategy CRUD hook |
| `ui/src/App.tsx` (modify) | New routes, nav items |
| `ui/src/pages/BacktestLab.tsx` (modify) | Full rewrite → Strategy Lab |
| `ui/src/pages/Dashboard.tsx` (modify) | Add collector status section |
| `tests/test_loader.py` | Strategy loader tests |
| `tests/test_user_strategies.py` | User strategy API tests |
| `tests/test_collector.py` | Collector service tests |

---

## Task 1: Add New Models + Extend DuckDB Store

**Files:**
- Modify: `flint/models.py:210` (append after line 210)
- Modify: `flint/store.py:1-143` (add WAL mode, new tables, new methods)
- Test: `tests/test_store.py`

- [ ] **Step 1: Write failing tests for new store tables**

Create `tests/test_store_extended.py`:

```python
"""Tests for extended DuckDB store — oracle prices, orderbook snapshots, pool snapshots."""
import pytest
from flint.store import FlintStore
from flint.models import OraclePrice


@pytest.fixture
def store():
    s = FlintStore(":memory:")
    yield s
    s.close()


def test_upsert_and_query_oracle_prices(store):
    prices = [
        OraclePrice(market="SOL-PERP", ts=1000, price=100.5),
        OraclePrice(market="SOL-PERP", ts=2000, price=101.0),
    ]
    count = store.upsert_oracle_prices(prices)
    assert count == 2
    result = store.query_oracle_prices("SOL-PERP")
    assert len(result) == 2
    assert result[0].price == 100.5


def test_query_oracle_prices_with_range(store):
    prices = [
        OraclePrice(market="SOL-PERP", ts=1000, price=100.0),
        OraclePrice(market="SOL-PERP", ts=2000, price=101.0),
        OraclePrice(market="SOL-PERP", ts=3000, price=102.0),
    ]
    store.upsert_oracle_prices(prices)
    result = store.query_oracle_prices("SOL-PERP", start_ts=1500, end_ts=2500)
    assert len(result) == 1
    assert result[0].ts == 2000


def test_upsert_orderbook_snapshots(store):
    count = store.upsert_orderbook_snapshots([{
        "market": "SOL-PERP",
        "ts": 1000,
        "bid_prices": [100.0, 99.5],
        "bid_sizes": [10.0, 20.0],
        "ask_prices": [101.0, 101.5],
        "ask_sizes": [15.0, 25.0],
    }])
    assert count == 1


def test_upsert_pool_snapshots(store):
    count = store.upsert_pool_snapshots([{
        "pool_address": "pool1",
        "dex": "raydium",
        "token_a_mint": "SOL",
        "token_b_mint": "USDC",
        "reserve_a": 1000.0,
        "reserve_b": 100000.0,
        "fee_rate": 0.003,
        "ts": 1000,
    }])
    assert count == 1


def test_collector_status_query(store):
    """Store should report table row counts for collector status."""
    prices = [OraclePrice(market="SOL-PERP", ts=1000, price=100.0)]
    store.upsert_oracle_prices(prices)
    count = store.count_oracle_prices("SOL-PERP")
    assert count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && python -m pytest tests/test_store_extended.py -v`
Expected: FAIL — `OraclePrice` not found, methods don't exist

- [ ] **Step 3: Add OraclePrice and CollectorStatus to models.py**

Append to `flint/models.py` after line 210:

```python
# ---------------------------------------------------------------------------
# Phase 7 models — collector + extended storage
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OraclePrice:
    market: str
    ts: int
    price: float
    slot: int | None = None


@dataclass
class CollectorStatus:
    market: str
    data_type: str  # "candles", "funding", "orderbook", "pools", "oracle"
    state: str  # "idle", "collecting", "backfilling", "error"
    last_updated: int | None = None
    row_count: int = 0
    date_range_start: int | None = None
    date_range_end: int | None = None
    error_message: str | None = None
    progress_pct: float | None = None
```

- [ ] **Step 4: Extend FlintStore with WAL mode and new tables**

In `flint/store.py`:

1. Add DDL strings for new tables after `_CREATE_FUNDING_RATES`:

```python
_CREATE_ORACLE_PRICES = """
CREATE TABLE IF NOT EXISTS oracle_prices (
    market  VARCHAR NOT NULL,
    ts      BIGINT  NOT NULL,
    price   DOUBLE  NOT NULL,
    slot    BIGINT,
    PRIMARY KEY (market, ts)
);
"""

_CREATE_ORDERBOOK_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    market     VARCHAR  NOT NULL,
    ts         BIGINT   NOT NULL,
    bid_prices DOUBLE[],
    bid_sizes  DOUBLE[],
    ask_prices DOUBLE[],
    ask_sizes  DOUBLE[],
    PRIMARY KEY (market, ts)
);
"""

_CREATE_POOL_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS pool_snapshots (
    pool_address VARCHAR NOT NULL,
    dex          VARCHAR NOT NULL,
    token_a_mint VARCHAR NOT NULL,
    token_b_mint VARCHAR NOT NULL,
    reserve_a    DOUBLE  NOT NULL,
    reserve_b    DOUBLE  NOT NULL,
    fee_rate     DOUBLE  NOT NULL,
    ts           BIGINT  NOT NULL,
    PRIMARY KEY (pool_address, ts)
);
"""
```

2. Update `_create_tables()` to run all DDL + enable WAL:

```python
def _create_tables(self) -> None:
    # Enable WAL mode for better concurrent read/write (collector + API)
    try:
        self._conn.execute("PRAGMA wal_autocheckpoint='1000'")
    except Exception:
        pass  # WAL not supported on all DuckDB builds; non-fatal
    self._conn.execute(_CREATE_CANDLES)
    self._conn.execute(_CREATE_FUNDING_RATES)
    self._conn.execute(_CREATE_ORACLE_PRICES)
    self._conn.execute(_CREATE_ORDERBOOK_SNAPSHOTS)
    self._conn.execute(_CREATE_POOL_SNAPSHOTS)
```

3. Add import for `OraclePrice` at top and new methods:

```python
def upsert_oracle_prices(self, prices: List[OraclePrice]) -> int:
    if not prices:
        return 0
    rows = [(p.market, p.ts, p.price, p.slot) for p in prices]
    self._conn.executemany(
        "INSERT OR REPLACE INTO oracle_prices (market, ts, price, slot) VALUES (?, ?, ?, ?)",
        rows,
    )
    return len(rows)

def query_oracle_prices(self, market: str, start_ts: int | None = None, end_ts: int | None = None) -> List[OraclePrice]:
    sql = "SELECT market, ts, price, slot FROM oracle_prices WHERE market = ?"
    params: list = [market]
    if start_ts is not None:
        sql += " AND ts >= ?"
        params.append(start_ts)
    if end_ts is not None:
        sql += " AND ts <= ?"
        params.append(end_ts)
    sql += " ORDER BY ts ASC"
    rows = self._conn.execute(sql, params).fetchall()
    return [OraclePrice(market=r[0], ts=r[1], price=r[2], slot=r[3]) for r in rows]

def count_oracle_prices(self, market: str) -> int:
    row = self._conn.execute("SELECT COUNT(*) FROM oracle_prices WHERE market = ?", [market]).fetchone()
    return row[0] if row else 0

def upsert_orderbook_snapshots(self, snapshots: list[dict]) -> int:
    if not snapshots:
        return 0
    rows = [
        (s["market"], s["ts"], s["bid_prices"], s["bid_sizes"], s["ask_prices"], s["ask_sizes"])
        for s in snapshots
    ]
    self._conn.executemany(
        "INSERT OR REPLACE INTO orderbook_snapshots (market, ts, bid_prices, bid_sizes, ask_prices, ask_sizes) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)

def upsert_pool_snapshots(self, snapshots: list[dict]) -> int:
    if not snapshots:
        return 0
    rows = [
        (s["pool_address"], s["dex"], s["token_a_mint"], s["token_b_mint"], s["reserve_a"], s["reserve_b"], s["fee_rate"], s["ts"])
        for s in snapshots
    ]
    self._conn.executemany(
        "INSERT OR REPLACE INTO pool_snapshots (pool_address, dex, token_a_mint, token_b_mint, reserve_a, reserve_b, fee_rate, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && python -m pytest tests/test_store_extended.py -v`
Expected: All 5 tests PASS

- [ ] **Step 6: Run full test suite to verify no regressions**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && python -m pytest tests/ -v`
Expected: All 94 existing + 5 new = 99 tests PASS

- [ ] **Step 7: Commit**

```bash
git add flint/models.py flint/store.py tests/test_store_extended.py
git commit -m "feat: add oracle_prices, orderbook_snapshots, pool_snapshots tables + OraclePrice model"
```

---

## Task 2: Strategy Loader

**Files:**
- Create: `flint/strategy/loader.py`
- Test: `tests/test_loader.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_loader.py`:

```python
"""Tests for dynamic strategy loader with AST validation."""
import pytest
from flint.strategy.loader import load_user_strategy, validate_strategy_code, StrategyLoadError


VALID_STRATEGY = '''
from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List

class MyStrategy(Strategy):
    @property
    def name(self) -> str:
        return "my_strategy"

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        if len(history) < 2:
            return Signal.HOLD
        if candle.close > history[-2].close:
            return Signal.BUY
        return Signal.SELL

    def reset(self) -> None:
        pass
'''

MISSING_ON_CANDLE = '''
from flint.strategy.base import Strategy
from flint.models import Signal

class BadStrategy(Strategy):
    @property
    def name(self) -> str:
        return "bad"

    def reset(self) -> None:
        pass
'''

SYNTAX_ERROR_CODE = '''
def this is broken(
'''

NO_STRATEGY_CLASS = '''
x = 42
def foo():
    return x
'''

SUSPICIOUS_IMPORT = '''
import os
from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List

class OsStrategy(Strategy):
    @property
    def name(self) -> str:
        return "os_strategy"

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        return Signal.HOLD

    def reset(self) -> None:
        pass
'''


def test_load_valid_strategy():
    strategy = load_user_strategy(VALID_STRATEGY)
    assert strategy.name == "my_strategy"


def test_validate_valid_code():
    result = validate_strategy_code(VALID_STRATEGY)
    assert result["valid"] is True
    assert result["warnings"] == []


def test_validate_missing_method():
    result = validate_strategy_code(MISSING_ON_CANDLE)
    assert result["valid"] is False
    assert "on_candle" in result["error"]


def test_validate_syntax_error():
    result = validate_strategy_code(SYNTAX_ERROR_CODE)
    assert result["valid"] is False
    assert "line" in result["error"].lower() or "syntax" in result["error"].lower()


def test_validate_no_strategy_class():
    result = validate_strategy_code(NO_STRATEGY_CLASS)
    assert result["valid"] is False
    assert "Strategy" in result["error"]


def test_validate_suspicious_import_warns():
    result = validate_strategy_code(SUSPICIOUS_IMPORT)
    assert result["valid"] is True
    assert len(result["warnings"]) > 0
    assert "os" in result["warnings"][0]


def test_load_strategy_with_params():
    code = '''
from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List

class ParamStrategy(Strategy):
    def __init__(self, threshold=5.0):
        self.threshold = threshold

    @property
    def name(self) -> str:
        return "param_strategy"

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        return Signal.HOLD

    def reset(self) -> None:
        pass
'''
    strategy = load_user_strategy(code, params={"threshold": 10.0})
    assert strategy.threshold == 10.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && python -m pytest tests/test_loader.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement strategy loader**

Create `flint/strategy/loader.py`:

```python
"""Dynamic strategy loader with AST validation.

This is intentionally unsandboxed. Flint is a local-first, single-user tool —
the user runs their own code on their own machine with full process privileges.
Same model as Jupyter notebooks and Freqtrade.
"""
from __future__ import annotations

import ast
from typing import Any

from .base import Strategy

APPROVED_MODULES = frozenset({
    "flint", "numpy", "math", "statistics", "collections", "dataclasses",
    "typing", "enum", "abc", "functools", "itertools", "operator",
})


class StrategyLoadError(Exception):
    pass


def validate_strategy_code(code: str) -> dict:
    """Validate strategy code via AST analysis.

    Returns {"valid": bool, "error": str | None, "warnings": list[str]}
    """
    warnings: list[str] = []

    # 1. Parse — catch syntax errors
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"valid": False, "error": f"Syntax error on line {e.lineno}: {e.msg}", "warnings": []}

    # 2. Find class that references Strategy
    strategy_classes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = ""
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr
                if base_name == "Strategy":
                    strategy_classes.append(node)

    if not strategy_classes:
        return {"valid": False, "error": "No class subclassing Strategy found", "warnings": []}

    # 3. Check required methods on first Strategy subclass
    cls = strategy_classes[0]
    method_names = set()
    for item in cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            method_names.add(item.name)

    missing = []
    if "on_candle" not in method_names:
        missing.append("on_candle")
    if "reset" not in method_names:
        missing.append("reset")
    if "name" not in method_names:
        missing.append("name")

    if missing:
        return {"valid": False, "error": f"Strategy class missing required methods: {', '.join(missing)}", "warnings": []}

    # 4. Check imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in APPROVED_MODULES:
                    warnings.append(f"Non-standard import: '{alias.name}' — proceed with caution")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top not in APPROVED_MODULES:
                    warnings.append(f"Non-standard import: '{node.module}' — proceed with caution")

    return {"valid": True, "error": None, "warnings": warnings}


def load_user_strategy(code: str, params: dict[str, Any] | None = None) -> Strategy:
    """Load a user strategy from source code.

    1. Validate via AST
    2. exec() in isolated namespace
    3. Find and instantiate the Strategy subclass
    """
    result = validate_strategy_code(code)
    if not result["valid"]:
        raise StrategyLoadError(result["error"])

    # Execute code in a namespace with flint imports available
    namespace: dict[str, Any] = {}
    try:
        exec(code, namespace)
    except Exception as e:
        raise StrategyLoadError(f"Error executing strategy code: {e}") from e

    # Find Strategy subclass in namespace
    strategy_cls = None
    for obj in namespace.values():
        if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
            strategy_cls = obj
            break

    if strategy_cls is None:
        raise StrategyLoadError("No Strategy subclass found after execution")

    # Instantiate with params if the constructor accepts them
    try:
        if params:
            return strategy_cls(**params)
        return strategy_cls()
    except TypeError as e:
        raise StrategyLoadError(f"Error instantiating strategy: {e}") from e
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && python -m pytest tests/test_loader.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add flint/strategy/loader.py tests/test_loader.py
git commit -m "feat: add dynamic strategy loader with AST validation"
```

---

## Task 3: User Strategy API Endpoints

**Files:**
- Create: `flint/api/routes/user_strategies.py`
- Create: `strategies/user/.gitkeep`
- Test: `tests/test_user_strategies.py`

- [ ] **Step 1: Create user strategy directory**

```bash
mkdir -p /Users/sohan/Documents/solana_stuff/flint/strategies/user
touch /Users/sohan/Documents/solana_stuff/flint/strategies/user/.gitkeep
```

- [ ] **Step 2: Write failing tests**

Create `tests/test_user_strategies.py`:

```python
"""Tests for user strategy CRUD API endpoints."""
import pytest
from fastapi.testclient import TestClient
from flint.api.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Use a temp directory for user strategies."""
    monkeypatch.setattr("flint.api.routes.user_strategies.STRATEGIES_DIR", tmp_path)
    return TestClient(app)


VALID_CODE = '''
from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List

class TestStrat(Strategy):
    @property
    def name(self) -> str:
        return "test_strat"

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        return Signal.HOLD

    def reset(self) -> None:
        pass
'''


def test_save_strategy(client):
    resp = client.post("/api/v1/user-strategies", json={"name": "my_strat", "code": VALID_CODE})
    assert resp.status_code == 200
    assert resp.json()["name"] == "my_strat"


def test_list_strategies(client):
    client.post("/api/v1/user-strategies", json={"name": "strat_a", "code": VALID_CODE})
    client.post("/api/v1/user-strategies", json={"name": "strat_b", "code": VALID_CODE})
    resp = client.get("/api/v1/user-strategies")
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()["strategies"]]
    assert "strat_a" in names
    assert "strat_b" in names


def test_load_strategy(client):
    client.post("/api/v1/user-strategies", json={"name": "my_strat", "code": VALID_CODE})
    resp = client.get("/api/v1/user-strategies/my_strat")
    assert resp.status_code == 200
    assert "TestStrat" in resp.json()["code"]


def test_delete_strategy(client):
    client.post("/api/v1/user-strategies", json={"name": "to_delete", "code": VALID_CODE})
    resp = client.delete("/api/v1/user-strategies/to_delete")
    assert resp.status_code == 200
    resp = client.get("/api/v1/user-strategies/to_delete")
    assert resp.status_code == 404


def test_validate_endpoint(client):
    resp = client.post("/api/v1/user-strategies/validate", json={"code": VALID_CODE})
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


def test_validate_bad_code(client):
    resp = client.post("/api/v1/user-strategies/validate", json={"code": "class Foo: pass"})
    assert resp.status_code == 200
    assert resp.json()["valid"] is False


def test_load_nonexistent_returns_404(client):
    resp = client.get("/api/v1/user-strategies/nonexistent")
    assert resp.status_code == 404
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && python -m pytest tests/test_user_strategies.py -v`
Expected: FAIL — module not found

- [ ] **Step 4: Implement user_strategies route**

Create `flint/api/routes/user_strategies.py`:

```python
"""User strategy CRUD API routes."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...strategy.loader import validate_strategy_code

router = APIRouter()

# Resolved at import time; tests monkeypatch this
STRATEGIES_DIR = Path(__file__).resolve().parents[3] / "strategies" / "user"


class SaveStrategyRequest(BaseModel):
    name: str
    code: str


class ValidateRequest(BaseModel):
    code: str


@router.post("")
def save_strategy(req: SaveStrategyRequest):
    """Save a user strategy to disk."""
    STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
    path = STRATEGIES_DIR / f"{req.name}.py"
    path.write_text(req.code, encoding="utf-8")
    return {"name": req.name, "saved": True}


@router.get("")
def list_strategies():
    """List all user strategies."""
    STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
    strategies = []
    for f in sorted(STRATEGIES_DIR.glob("*.py")):
        strategies.append({"name": f.stem, "file": f.name})
    return {"strategies": strategies}


@router.get("/{name}")
def load_strategy(name: str):
    """Load a user strategy's source code."""
    path = STRATEGIES_DIR / f"{name}.py"
    if not path.exists():
        raise HTTPException(404, f"Strategy '{name}' not found")
    code = path.read_text(encoding="utf-8")
    return {"name": name, "code": code}


@router.delete("/{name}")
def delete_strategy(name: str):
    """Delete a user strategy."""
    path = STRATEGIES_DIR / f"{name}.py"
    if not path.exists():
        raise HTTPException(404, f"Strategy '{name}' not found")
    path.unlink()
    return {"name": name, "deleted": True}


@router.post("/validate")
def validate_strategy(req: ValidateRequest):
    """Validate strategy code without saving or running."""
    return validate_strategy_code(req.code)
```

- [ ] **Step 5: Register route in main.py**

In `flint/api/main.py`, add import and include_router:

```python
from .routes import backtest, strategies, data, mev, user_strategies

# ... after existing router registrations:
app.include_router(user_strategies.router, prefix="/api/v1/user-strategies", tags=["user-strategies"])
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && python -m pytest tests/test_user_strategies.py -v`
Expected: All 7 tests PASS

- [ ] **Step 7: Run full suite for regressions**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add flint/api/routes/user_strategies.py flint/api/main.py strategies/user/.gitkeep tests/test_user_strategies.py
git commit -m "feat: add user strategy CRUD API at /api/v1/user-strategies"
```

---

## Task 4: Extend Backtest Route for User Strategies

**Files:**
- Modify: `flint/api/routes/backtest.py:30-70`
- Test: `tests/test_api.py` (extend)

- [ ] **Step 1: Write failing test**

Add to `tests/test_api.py`:

```python
def test_backtest_with_inline_code():
    """Backtest should accept inline strategy code."""
    code = '''
from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List

class AlwaysHold(Strategy):
    @property
    def name(self) -> str:
        return "always_hold"

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        return Signal.HOLD

    def reset(self) -> None:
        pass
'''
    resp = client.post("/api/v1/backtest/run", json={
        "strategy": "custom",
        "code": code,
        "market": "SOL-PERP",
        "start_ts": 1709251200,
        "end_ts": 1711929600,
    })
    assert resp.status_code == 200
    assert "id" in resp.json()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && python -m pytest tests/test_api.py::test_backtest_with_inline_code -v`
Expected: FAIL — `code` field not accepted or strategy not loadable

- [ ] **Step 3: Update BacktestRequest and _build_strategy**

In `flint/api/routes/backtest.py`:

1. Add `code` field to `BacktestRequest` (after line 38):
```python
class BacktestRequest(BaseModel):
    strategy: str = "ma_crossover"
    code: str | None = None  # Inline strategy code from editor
    market: str = "SOL-PERP"
    resolution_s: int = 3600
    start_ts: int
    end_ts: int
    initial_capital: float = 10_000.0
    fee_rate: float = 0.0005
    params: Optional[Dict] = None
```

2. Add import at top:
```python
from ...strategy.loader import load_user_strategy, StrategyLoadError
```

3. Update `_build_strategy` to handle code and user: prefix:

```python
def _build_strategy(name: str, params: Dict, code: str | None = None):
    """Instantiate a strategy by name, user file, or inline code."""
    # Inline code from editor
    if code:
        return load_user_strategy(code, params or None)

    # User strategy from disk
    if name.startswith("user:"):
        from pathlib import Path
        strat_name = name[5:]
        path = Path(__file__).resolve().parents[3] / "strategies" / "user" / f"{strat_name}.py"
        if not path.exists():
            return None
        return load_user_strategy(path.read_text(encoding="utf-8"), params or None)

    # Built-in strategies (existing code)
    if name == "ma_crossover":
        # ... keep existing code unchanged
```

4. Update the `_run` function inside `run_backtest` to pass `code`:
```python
strategy = _build_strategy(req.strategy, params, req.code)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && python -m pytest tests/test_api.py -v`
Expected: All tests PASS (including new one)

- [ ] **Step 5: Commit**

```bash
git add flint/api/routes/backtest.py tests/test_api.py
git commit -m "feat: support inline strategy code and user: prefix in backtest route"
```

---

## Task 5: Data Collector Service

**Files:**
- Create: `flint/collector/__init__.py`
- Create: `flint/collector/tasks.py`
- Create: `flint/collector/service.py`
- Test: `tests/test_collector.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_collector.py`:

```python
"""Tests for the data collector service."""
import pytest
from unittest.mock import patch, MagicMock

from flint.collector.service import CollectorService
from flint.collector.tasks import CollectorConfig, collect_oracle_prices
from flint.store import FlintStore


@pytest.fixture
def store():
    s = FlintStore(":memory:")
    yield s
    s.close()


def test_collector_config_defaults():
    config = CollectorConfig()
    assert "SOL-PERP" in config.markets
    assert config.candle_backfill_days == 90


def test_collector_service_init(store):
    service = CollectorService(store)
    assert service.store is store
    assert len(service.status) == 0


def test_collector_status_tracking(store):
    service = CollectorService(store)
    service.update_status("SOL-PERP", "candles", "collecting")
    s = service.get_status()
    assert any(st["market"] == "SOL-PERP" and st["data_type"] == "candles" for st in s)


def test_collector_status_error(store):
    service = CollectorService(store)
    service.update_status("SOL-PERP", "candles", "error", error_message="API timeout")
    s = service.get_status()
    entry = next(st for st in s if st["market"] == "SOL-PERP" and st["data_type"] == "candles")
    assert entry["state"] == "error"
    assert entry["error_message"] == "API timeout"


def test_collect_oracle_prices_success(store):
    """Oracle price collection should write to store on success (sync function)."""
    mock_provider = MagicMock()
    mock_provider.fetch_mid_price = MagicMock(return_value=150.0)
    mock_provider.close = MagicMock()

    with patch("flint.collector.tasks.DriftDataProvider", return_value=mock_provider):
        count = collect_oracle_prices(store, "SOL-PERP")
        assert count >= 1
        prices = store.query_oracle_prices("SOL-PERP")
        assert len(prices) >= 1
        assert prices[0].price == 150.0


def test_needs_backfill_empty_db(store):
    """Service should detect empty database needs backfill."""
    service = CollectorService(store)
    assert service._needs_backfill() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && python -m pytest tests/test_collector.py -v`
Expected: FAIL — modules not found

- [ ] **Step 3: Create collector package**

Create `flint/collector/__init__.py`:
```python
"""Data collection service for automated market data ingestion."""
```

Create `flint/collector/tasks.py`:

**IMPORTANT**: All `DriftDataProvider` methods are synchronous (uses `httpx.Client`).
Collection tasks are plain sync functions, run via `asyncio.to_thread()` from the service.

```python
"""Individual data collection tasks.

All tasks are synchronous — DriftDataProvider uses httpx.Client (sync).
The CollectorService runs them via asyncio.to_thread().
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..models import OraclePrice
from ..providers.drift_api import DriftDataProvider
from ..providers.drift_s3 import DriftS3Provider
from ..store import FlintStore


# Market index mapping for Drift API
MARKET_INDEX = {"SOL-PERP": 0, "BTC-PERP": 1, "ETH-PERP": 2}


@dataclass
class CollectorConfig:
    markets: list[str] = field(default_factory=lambda: ["SOL-PERP", "BTC-PERP", "ETH-PERP"])
    candle_backfill_days: int = 90
    candle_interval_s: int = 3600       # 1 hour
    funding_interval_s: int = 3600      # 1 hour
    orderbook_interval_s: int = 300     # 5 minutes
    oracle_interval_s: int = 60         # 1 minute


def collect_oracle_prices(store: FlintStore, market: str) -> int:
    """Fetch current oracle/mid price and store it. Synchronous."""
    provider = DriftDataProvider()
    try:
        price = provider.fetch_mid_price(market)
        ts = int(time.time())
        oracle = OraclePrice(market=market, ts=ts, price=price)
        return store.upsert_oracle_prices([oracle])
    finally:
        provider.close()


def collect_funding_rates(store: FlintStore, market: str, market_index: int) -> int:
    """Fetch recent funding rates and store them. Synchronous."""
    provider = DriftDataProvider()
    try:
        rates = provider.fetch_funding_rates(market_index=market_index, market_name=market)
        if rates:
            return store.upsert_funding_rates(rates)
        return 0
    finally:
        provider.close()


def collect_orderbook(store: FlintStore, market: str) -> int:
    """Fetch L2 orderbook and store snapshot. Synchronous."""
    provider = DriftDataProvider()
    try:
        ob = provider.fetch_orderbook(market_name=market, depth=10)
        if ob:
            snapshot = {
                "market": market,
                "ts": int(time.time()),
                "bid_prices": [level.price for level in ob.bids[:10]],
                "bid_sizes": [level.size for level in ob.bids[:10]],
                "ask_prices": [level.price for level in ob.asks[:10]],
                "ask_sizes": [level.size for level in ob.asks[:10]],
            }
            return store.upsert_orderbook_snapshots([snapshot])
        return 0
    finally:
        provider.close()


def collect_candles_backfill(store: FlintStore, market: str, days: int = 90) -> int:
    """Backfill candle data from Drift S3. Synchronous."""
    end_ts = int(time.time())
    start_ts = end_ts - (days * 86400)
    provider = DriftS3Provider()
    try:
        candles = provider.fetch_candles(market, 3600, start_ts, end_ts)
        if candles:
            return store.upsert_candles(candles)
        return 0
    finally:
        provider.close()
```

Create `flint/collector/service.py`:

**KEY DESIGN**: All collection tasks are synchronous (DriftDataProvider uses httpx.Client).
The service runs them via `asyncio.to_thread()`. The `_run_task` method takes a **callable
factory** (not a coroutine) so retries create fresh calls each time. An `asyncio.Lock`
protects DuckDB writes.

```python
"""Main collector service — async loop with scheduling and status tracking."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from ..store import FlintStore
from .tasks import (
    CollectorConfig,
    MARKET_INDEX,
    collect_oracle_prices,
    collect_funding_rates,
    collect_orderbook,
    collect_candles_backfill,
)

logger = logging.getLogger("flint.collector")

# Shared write lock for DuckDB — collector and API routes must not write simultaneously
write_lock = asyncio.Lock()


class CollectorService:
    def __init__(self, store: FlintStore, config: CollectorConfig | None = None):
        self.store = store
        self.config = config or CollectorConfig()
        self.status: dict[tuple[str, str], dict[str, Any]] = {}
        self._running = False

    def update_status(
        self,
        market: str,
        data_type: str,
        state: str,
        error_message: str | None = None,
        progress_pct: float | None = None,
    ) -> None:
        key = (market, data_type)
        if key not in self.status:
            self.status[key] = {
                "market": market,
                "data_type": data_type,
                "state": "idle",
                "last_updated": None,
                "row_count": 0,
                "error_message": None,
                "progress_pct": None,
            }
        self.status[key]["state"] = state
        if error_message is not None:
            self.status[key]["error_message"] = error_message
        if progress_pct is not None:
            self.status[key]["progress_pct"] = progress_pct
        if state == "idle":
            self.status[key]["last_updated"] = int(time.time())
            self.status[key]["error_message"] = None

    def get_status(self) -> list[dict]:
        return list(self.status.values())

    async def _run_task(self, market: str, data_type: str, task_fn: Callable[[], int]) -> None:
        """Run a sync collection task in a thread with retry and status tracking.

        task_fn is a zero-arg callable that returns row count. It is called fresh
        on each retry (no coroutine reuse issue).
        """
        self.update_status(market, data_type, "collecting")
        retries = 0
        max_retries = 5
        while retries < max_retries:
            try:
                async with write_lock:
                    count = await asyncio.to_thread(task_fn)
                self.status[(market, data_type)]["row_count"] += count
                self.update_status(market, data_type, "idle")
                return
            except Exception as e:
                retries += 1
                if retries >= max_retries:
                    logger.error(f"Collection failed for {market}/{data_type}: {e}")
                    self.update_status(market, data_type, "error", error_message=str(e))
                    return
                wait = min(5 * (2 ** (retries - 1)), 300)
                logger.warning(f"Retry {retries}/{max_retries} for {market}/{data_type} in {wait}s: {e}")
                await asyncio.sleep(wait)

    def _needs_backfill(self) -> bool:
        """Check if backfill is needed: no data or data older than 2 hours."""
        count = self.store.count_candles("SOL-PERP", 3600)
        if count == 0:
            return True
        # Check recency of most recent candle
        candles = self.store.query_candles("SOL-PERP", 3600)
        if not candles:
            return True
        newest_ts = max(c.ts for c in candles)
        age_hours = (time.time() - newest_ts) / 3600
        return age_hours > 2

    async def backfill(self) -> None:
        """Run initial backfill for all markets."""
        total = len(self.config.markets)
        for i, market in enumerate(self.config.markets):
            pct = (i / total) * 100
            self.update_status(market, "candles", "backfilling", progress_pct=pct)
            try:
                async with write_lock:
                    count = await asyncio.to_thread(
                        collect_candles_backfill, self.store, market, self.config.candle_backfill_days
                    )
                self.status[(market, "candles")]["row_count"] = count
                self.update_status(market, "candles", "idle", progress_pct=100.0)
            except Exception as e:
                logger.error(f"Backfill failed for {market}: {e}")
                self.update_status(market, "candles", "error", error_message=str(e))

    async def run(self) -> None:
        """Main collector loop. Runs until cancelled."""
        self._running = True
        logger.info("Collector service starting")

        if self._needs_backfill():
            logger.info("Database empty or stale — starting backfill")
            await self.backfill()

        # Scheduled collection loop
        last_oracle = 0
        last_funding = 0
        last_orderbook = 0

        while self._running:
            now = time.time()

            for market in self.config.markets:
                idx = MARKET_INDEX.get(market, 0)

                # Oracle prices — every minute
                if now - last_oracle >= self.config.oracle_interval_s:
                    await self._run_task(
                        market, "oracle",
                        lambda m=market: collect_oracle_prices(self.store, m),
                    )

                # Orderbook — every 5 minutes
                if now - last_orderbook >= self.config.orderbook_interval_s:
                    await self._run_task(
                        market, "orderbook",
                        lambda m=market: collect_orderbook(self.store, m),
                    )

                # Funding rates — every hour
                if now - last_funding >= self.config.funding_interval_s:
                    await self._run_task(
                        market, "funding",
                        lambda m=market, i=idx: collect_funding_rates(self.store, m, i),
                    )

            if now - last_oracle >= self.config.oracle_interval_s:
                last_oracle = now
            if now - last_orderbook >= self.config.orderbook_interval_s:
                last_orderbook = now
            if now - last_funding >= self.config.funding_interval_s:
                last_funding = now

            await asyncio.sleep(10)

    def stop(self) -> None:
        self._running = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && python -m pytest tests/test_collector.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add flint/collector/ tests/test_collector.py
git commit -m "feat: add data collector service with backfill and scheduled collection"
```

---

## Task 6: Collector API Routes + Lifespan Integration

**Files:**
- Create: `flint/api/routes/collector.py`
- Modify: `flint/api/main.py`
- Test: extend `tests/test_api.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_api.py`:

```python
def test_collector_status():
    resp = client.get("/api/v1/collector/status")
    assert resp.status_code == 200
    assert "status" in resp.json()


def test_collector_config():
    resp = client.get("/api/v1/collector/config")
    assert resp.status_code == 200
    assert "markets" in resp.json()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && python -m pytest tests/test_api.py::test_collector_status -v`
Expected: FAIL — 404

- [ ] **Step 3: Create collector routes**

Create `flint/api/routes/collector.py`:

```python
"""Collector status and trigger API routes."""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ...collector.tasks import CollectorConfig

router = APIRouter()


class TriggerRequest(BaseModel):
    market: str = "SOL-PERP"
    data_type: str = "candles"


@router.get("/status")
def get_collector_status(request: Request):
    """Get collection status for all markets and data types."""
    collector = getattr(request.app.state, "collector", None)
    if collector is None:
        return {"status": [], "running": False}
    return {"status": collector.get_status(), "running": collector._running}


@router.post("/trigger")
async def trigger_collection(req: TriggerRequest, request: Request):
    """Manually trigger a data collection run."""
    collector = getattr(request.app.state, "collector", None)
    if collector is None:
        return {"error": "Collector not running"}
    # Trigger is informational for now — the collector loop handles scheduling
    return {"triggered": True, "market": req.market, "data_type": req.data_type}


@router.get("/config")
def get_collector_config():
    """Get current collector configuration."""
    config = CollectorConfig()
    return {
        "markets": config.markets,
        "candle_backfill_days": config.candle_backfill_days,
        "intervals": {
            "candles": config.candle_interval_s,
            "funding": config.funding_interval_s,
            "orderbook": config.orderbook_interval_s,
            "oracle": config.oracle_interval_s,
        },
    }
```

- [ ] **Step 4: Rewrite main.py with lifespan and all routes**

Replace `flint/api/main.py` entirely:

```python
"""Flint FastAPI application."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import backtest, strategies, data, mev, user_strategies, collector
from ..store import FlintStore
from ..collector.service import CollectorService

logger = logging.getLogger("flint.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init shared store and collector. Shutdown: clean up."""
    store = FlintStore("./data/flint.duckdb")
    app.state.store = store

    collector_svc = CollectorService(store)
    app.state.collector = collector_svc
    task = asyncio.create_task(collector_svc.run())
    logger.info("Flint API started with collector")

    yield

    collector_svc.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    store.close()
    logger.info("Flint API shutdown complete")


app = FastAPI(
    title="Flint",
    description="Algorithmic trading, backtesting, and MEV research platform for Solana",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(backtest.router, prefix="/api/v1/backtest", tags=["backtest"])
app.include_router(strategies.router, prefix="/api/v1/strategies", tags=["strategies"])
app.include_router(user_strategies.router, prefix="/api/v1/user-strategies", tags=["user-strategies"])
app.include_router(data.router, prefix="/api/v1/data", tags=["data"])
app.include_router(mev.router, prefix="/api/v1/mev", tags=["mev"])
app.include_router(collector.router, prefix="/api/v1/collector", tags=["collector"])


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "service": "flint"}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && python -m pytest tests/test_api.py -v`
Expected: All tests PASS

- [ ] **Step 6: Run full suite**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add flint/api/routes/collector.py flint/api/main.py tests/test_api.py
git commit -m "feat: add collector API routes and lifespan-managed collector service"
```

---

## Task 7: Docs Page — Content + Components

**Files:**
- Create: `ui/src/data/docs-content.ts`
- Create: `ui/src/components/DocsSidebar.tsx`
- Create: `ui/src/components/DocsContent.tsx`
- Create: `ui/src/pages/Docs.tsx`
- Modify: `ui/src/App.tsx`

- [ ] **Step 1: Install no new deps needed — Monaco comes in Task 8**

No action needed.

- [ ] **Step 2: Create docs content data structure**

Create `ui/src/data/docs-content.ts` with the full `DocsContent` type and all sections. This is the largest single file — it contains all documentation text. Structure:

```typescript
export interface DocCodeBlock {
  language: string
  code: string
}

export interface DocTopic {
  id: string
  title: string
  content: string
  codeBlocks?: DocCodeBlock[]
}

export interface DocSection {
  id: string
  title: string
  topics: DocTopic[]
}

export const docsContent: DocSection[] = [
  {
    id: "getting-started",
    title: "GETTING STARTED",
    topics: [
      {
        id: "installation",
        title: "Installation",
        content: `<p>Flint requires Python 3.9+ and Node.js 18+. No API keys are needed for backtesting — all data sources are free.</p>`,
        codeBlocks: [
          { language: "bash", code: "# Install Python backend\ncd flint\npip install -e \".[dev]\"\n\n# Install UI dependencies\ncd ui\nnpm install" },
          { language: "bash", code: "# Verify everything works\npytest tests/ -v" },
        ],
      },
      {
        id: "quick-start",
        title: "Quick Start",
        content: `<p>Run your first backtest in under 5 minutes. This downloads real SOL-PERP trade data from Drift's public S3 bucket, aggregates it into 1-hour candles, and runs an MA crossover strategy.</p>`,
        codeBlocks: [
          { language: "bash", code: "# Run the demo backtest\npython scripts/run_backtest.py\n\n# Start the web UI\nuvicorn flint.api.main:app --port 8000 &\ncd ui && npm run dev" },
        ],
      },
      {
        id: "project-structure",
        title: "Project Structure",
        content: `<p>Flint is organized into clear modules:</p>
<ul>
<li><strong>flint/strategy/</strong> — Strategy base class and built-in strategies</li>
<li><strong>flint/backtest/</strong> — Event-driven backtesting engine</li>
<li><strong>flint/providers/</strong> — Data providers (Drift S3, Drift API, GeckoTerminal, Jupiter)</li>
<li><strong>flint/connectors/</strong> — Protocol connectors (Drift, Jupiter)</li>
<li><strong>flint/mev/</strong> — MEV framework (arb, liquidation, JIT, bundles)</li>
<li><strong>flint/analytics/</strong> — Metrics and tearsheet generation</li>
<li><strong>flint/api/</strong> — FastAPI backend</li>
<li><strong>flint/collector/</strong> — Automated data collection service</li>
<li><strong>ui/</strong> — React frontend</li>
</ul>`,
      },
    ],
  },
  {
    id: "solana-for-quants",
    title: "SOLANA FOR QUANTS",
    topics: [
      {
        id: "slots-vs-time",
        title: "Slots vs Time",
        content: `<p>In TradFi, everything keys off wall-clock time — 1-minute candles, daily closes, quarterly earnings. On Solana, the fundamental unit is a <strong>slot</strong> (~400ms). Think of slots as Solana's heartbeat.</p>
<p>Each slot can contain one block of transactions. Validators rotate every ~4 slots (1.6 seconds). This matters for execution: your transaction lands in a specific slot, not at a specific time.</p>
<p>Flint abstracts this for backtesting — candles use unix timestamps and configurable resolutions (1h default). But when you move to live trading, slot awareness becomes critical for MEV and execution timing.</p>`,
        codeBlocks: [
          { language: "python", code: "# Solana: ~2.5 slots per second\n# 1 hour ≈ 9,000 slots\n# Flint candles use unix timestamps\ncandle.ts           # unix seconds (bucket start)\ncandle.resolution_s # 3600 for 1h candles" },
        ],
      },
      {
        id: "drift-protocol",
        title: "Drift Protocol",
        content: `<p>Drift is Solana's largest perpetual futures exchange — think of it as an on-chain Binance Futures. Key differences from CEX perps:</p>
<ul>
<li><strong>Decentralized Limit Order Book (DLOB)</strong> — Not a traditional CLOB. Orders are stored on-chain and matched by keeper bots, not a central engine.</li>
<li><strong>JIT Auctions</strong> — Before your market order fills against the DLOB, makers get a ~5-second window to offer better prices. This is unique to Drift.</li>
<li><strong>Hourly Funding</strong> — CEX perps typically settle funding every 8 hours. Drift settles every hour, creating more frequent arbitrage opportunities.</li>
<li><strong>Oracle-based pricing</strong> — Drift uses Pyth oracles for mark price, not its own orderbook mid.</li>
</ul>
<p>Flint uses Drift as its primary data source (free S3 historical data) and primary perp exchange.</p>`,
      },
      {
        id: "amm-pools",
        title: "AMM Pools",
        content: `<p>If you're used to orderbooks, AMM pools are a paradigm shift. Instead of buyers and sellers posting orders, liquidity providers deposit token pairs into a pool. Price is determined by the ratio of reserves.</p>
<p><strong>Constant Product formula:</strong> x * y = k</p>
<p>When you buy token A with token B, you add B to the pool and remove A. The price moves along the curve. Key implications for quants:</p>
<ul>
<li><strong>No bid/ask spread</strong> — Instead, you pay "price impact" proportional to trade size relative to pool reserves</li>
<li><strong>Impermanent Loss</strong> — LPs lose value when prices diverge from their entry ratio</li>
<li><strong>Arbitrage</strong> — Price differences between pools (or pool vs CEX) create arb opportunities</li>
</ul>`,
        codeBlocks: [
          { language: "python", code: "# Constant product AMM math\n# output = (reserve_out * input) / (reserve_in + input)\ndef swap(reserve_in, reserve_out, amount_in, fee=0.003):\n    effective = amount_in * (1 - fee)\n    return (reserve_out * effective) / (reserve_in + effective)" },
        ],
      },
      {
        id: "funding-rates",
        title: "Funding Rates",
        content: `<p>If you've traded perps on Binance or FTX, you know funding rates. They keep the perp price anchored to the spot (oracle) price. When the perp trades above oracle, longs pay shorts. Below oracle, shorts pay longs.</p>
<p><strong>Key difference on Drift:</strong> Funding settles every hour (not 8 hours). This means:</p>
<ul>
<li>More frequent payments — smaller per-period but more granular</li>
<li>Faster convergence — perp price tracks oracle more tightly</li>
<li>More arb opportunities — funding can flip sign multiple times per day</li>
</ul>
<p>Funding rate harvesting is a classic Drift strategy: go long when funding is deeply negative (you get paid), go short when deeply positive.</p>`,
        codeBlocks: [
          { language: "python", code: "# Drift funding rate example\n# rate > 0: longs pay shorts\n# rate < 0: shorts pay longs\nfunding_payment = position_size * funding_rate * oracle_price" },
        ],
      },
      {
        id: "mev-on-solana",
        title: "MEV on Solana",
        content: `<p>MEV (Maximal Extractable Value) on Solana is different from Ethereum. There's no public mempool — instead, Solana MEV is driven by:</p>
<ul>
<li><strong>Jito Bundles</strong> — Pay a tip to the validator to include your transactions in a specific order. This is how arbitrage and liquidations are executed atomically.</li>
<li><strong>Priority Fees</strong> — Higher fees = higher chance of inclusion in the next block.</li>
<li><strong>Backrunning</strong> — You can't see pending transactions, but you can react to confirmed ones within the same slot.</li>
</ul>
<p>Flint's MEV module detects arb routes across AMM pools and scans for liquidation opportunities on Drift. The bundle builder constructs Jito-compatible bundles for atomic execution.</p>`,
      },
      {
        id: "onchain-orderbooks",
        title: "On-chain Order Books",
        content: `<p>Drift's DLOB (Decentralized Limit Order Book) is unlike any CEX orderbook:</p>
<ul>
<li><strong>Keeper-matched</strong> — Orders don't auto-match. Keeper bots scan for crossing orders and submit match transactions.</li>
<li><strong>JIT priority</strong> — Market makers can fill incoming orders before they hit the DLOB via JIT auctions.</li>
<li><strong>L2 and L3 data</strong> — Drift's free DLOB API provides both aggregate (L2) and per-order (L3) orderbook data.</li>
</ul>
<p>For backtesting, Flint uses candle data (aggregated from historical trades). For live strategies, the orderbook becomes critical for execution quality.</p>`,
      },
    ],
  },
  {
    id: "strategy-api",
    title: "STRATEGY API",
    topics: [
      {
        id: "base-class",
        title: "Strategy Base Class",
        content: `<p>Every Flint strategy inherits from the <code>Strategy</code> base class. You implement three things:</p>
<ul>
<li><code>name</code> — A property returning your strategy's identifier</li>
<li><code>on_candle(candle, history)</code> — Called on every new candle. Return a Signal.</li>
<li><code>reset()</code> — Reset internal state for a fresh backtest run.</li>
</ul>`,
        codeBlocks: [
          { language: "python", code: "from flint.strategy.base import Strategy\nfrom flint.models import Candle, Signal\nfrom typing import List\n\nclass MyStrategy(Strategy):\n    @property\n    def name(self) -> str:\n        return \"my_strategy\"\n\n    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:\n        # Your logic here\n        return Signal.HOLD\n\n    def reset(self) -> None:\n        # Reset any internal state\n        pass" },
        ],
      },
      {
        id: "signals",
        title: "Signals",
        content: `<p>Your <code>on_candle</code> method returns one of three signals:</p>
<ul>
<li><code>Signal.BUY</code> — Open a long position (if none open). The engine buys at the candle's close price.</li>
<li><code>Signal.SELL</code> — Close the current position. The engine sells at the candle's close price.</li>
<li><code>Signal.HOLD</code> — Do nothing. Keep the current position (or stay flat).</li>
</ul>
<p>The engine handles position sizing, fee deduction, and equity tracking automatically.</p>`,
      },
      {
        id: "candle-data",
        title: "Candle Data",
        content: `<p>The <code>Candle</code> dataclass contains:</p>
<table>
<tr><td><code>ts</code></td><td>Unix timestamp (bucket start)</td></tr>
<tr><td><code>open</code></td><td>Opening price</td></tr>
<tr><td><code>high</code></td><td>Highest price in period</td></tr>
<tr><td><code>low</code></td><td>Lowest price in period</td></tr>
<tr><td><code>close</code></td><td>Closing price</td></tr>
<tr><td><code>volume</code></td><td>Base asset volume</td></tr>
<tr><td><code>market</code></td><td>Market identifier (e.g., "SOL-PERP")</td></tr>
<tr><td><code>resolution_s</code></td><td>Candle width in seconds (3600 for 1h)</td></tr>
</table>
<p>The <code>history</code> parameter gives you all previous candles in chronological order, so <code>history[-1]</code> is the most recent previous candle.</p>`,
      },
      {
        id: "backtest-engine",
        title: "Backtest Engine",
        content: `<p>The backtest engine processes candles chronologically and executes your strategy:</p>
<ol>
<li>For each candle, calls <code>strategy.on_candle(candle, history)</code></li>
<li>On <code>BUY</code>: opens a position at close price, deducts fees</li>
<li>On <code>SELL</code>: closes position at close price, records P&L, deducts fees</li>
<li>Tracks equity curve (including unrealized P&L)</li>
<li>Force-closes any open position at the final candle</li>
</ol>
<p>Configurable parameters: <code>initial_capital</code> (default $10,000), <code>fee_rate</code> (default 5bps), <code>position_size</code> (fraction of capital, default 1.0).</p>`,
      },
      {
        id: "analytics",
        title: "Analytics",
        content: `<p>After a backtest completes, Flint computes a full analytics suite:</p>
<ul>
<li><strong>Returns:</strong> Total return %, annualized return %</li>
<li><strong>Risk:</strong> Sharpe ratio, Sortino ratio, max drawdown %, max drawdown duration</li>
<li><strong>Trades:</strong> Win rate, profit factor, average win/loss, average holding period</li>
<li><strong>Benchmark:</strong> Buy-and-hold comparison with equity curve overlay</li>
<li><strong>Visualization:</strong> Equity curve, drawdown chart, monthly return heatmap, trade log</li>
</ul>`,
      },
    ],
  },
  {
    id: "examples",
    title: "EXAMPLES",
    topics: [
      {
        id: "ma-crossover",
        title: "MA Crossover (Beginner)",
        content: `<p>The classic moving average crossover strategy. When the fast SMA crosses above the slow SMA (golden cross), go long. When it crosses below (death cross), exit.</p>`,
        codeBlocks: [
          { language: "python", code: "from flint.strategy.base import Strategy\nfrom flint.models import Candle, Signal\nfrom typing import List\nimport numpy as np\n\nclass MACrossover(Strategy):\n    def __init__(self, fast_period=10, slow_period=30):\n        self.fast = fast_period\n        self.slow = slow_period\n        self._prev_fast = None\n        self._prev_slow = None\n\n    @property\n    def name(self) -> str:\n        return f\"MA-Crossover({self.fast}/{self.slow})\"\n\n    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:\n        if len(history) < self.slow:\n            return Signal.HOLD\n\n        closes = [c.close for c in history[-self.slow:]]\n        fast_ma = np.mean(closes[-self.fast:])\n        slow_ma = np.mean(closes)\n\n        signal = Signal.HOLD\n        if self._prev_fast is not None:\n            if self._prev_fast <= self._prev_slow and fast_ma > slow_ma:\n                signal = Signal.BUY\n            elif self._prev_fast >= self._prev_slow and fast_ma < slow_ma:\n                signal = Signal.SELL\n\n        self._prev_fast = fast_ma\n        self._prev_slow = slow_ma\n        return signal\n\n    def reset(self) -> None:\n        self._prev_fast = None\n        self._prev_slow = None" },
        ],
      },
      {
        id: "rsi-reversion",
        title: "RSI Mean Reversion (Beginner)",
        content: `<p>Uses the Relative Strength Index to detect oversold (buy) and overbought (sell) conditions. Works best in ranging markets.</p>`,
        codeBlocks: [
          { language: "python", code: "from flint.strategy.base import Strategy\nfrom flint.models import Candle, Signal\nfrom typing import List\nimport numpy as np\n\nclass RSIMeanReversion(Strategy):\n    def __init__(self, period=14, oversold=30, overbought=70):\n        self.period = period\n        self.oversold = oversold\n        self.overbought = overbought\n\n    @property\n    def name(self) -> str:\n        return f\"RSI({self.period})\"\n\n    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:\n        if len(history) < self.period + 1:\n            return Signal.HOLD\n\n        closes = [c.close for c in history[-(self.period + 1):]]\n        deltas = np.diff(closes)\n        gains = np.where(deltas > 0, deltas, 0)\n        losses = np.where(deltas < 0, -deltas, 0)\n\n        avg_gain = np.mean(gains) if len(gains) > 0 else 0\n        avg_loss = np.mean(losses) if len(losses) > 0 else 0\n\n        if avg_loss == 0:\n            rsi = 100.0\n        else:\n            rs = avg_gain / avg_loss\n            rsi = 100 - (100 / (1 + rs))\n\n        if rsi < self.oversold:\n            return Signal.BUY\n        elif rsi > self.overbought:\n            return Signal.SELL\n        return Signal.HOLD\n\n    def reset(self) -> None:\n        pass" },
        ],
      },
      {
        id: "funding-harvest",
        title: "Funding Rate Harvest (Intermediate)",
        content: `<p>A Solana-native strategy that exploits Drift's hourly funding rate. When funding is deeply negative (shorts paying longs), go long to collect funding payments. When deeply positive, go short.</p>
<p><strong>Note:</strong> This is a simplified template for learning. Production funding harvesting needs delta hedging and more sophisticated entry/exit logic.</p>`,
        codeBlocks: [
          { language: "python", code: "from flint.strategy.base import Strategy\nfrom flint.models import Candle, Signal\nfrom typing import List\n\nclass FundingHarvest(Strategy):\n    \"\"\"Collect funding payments by trading against the crowd.\n    \n    When funding rate is deeply negative, longs get paid.\n    When deeply positive, shorts get paid.\n    Uses price momentum as a proxy for funding direction.\n    \"\"\"\n    def __init__(self, lookback=24, threshold_pct=2.0):\n        self.lookback = lookback\n        self.threshold = threshold_pct / 100\n\n    @property\n    def name(self) -> str:\n        return f\"FundingHarvest({self.lookback})\"\n\n    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:\n        if len(history) < self.lookback:\n            return Signal.HOLD\n\n        # Use price deviation from mean as funding proxy\n        closes = [c.close for c in history[-self.lookback:]]\n        mean_price = sum(closes) / len(closes)\n        deviation = (candle.close - mean_price) / mean_price\n\n        # Price above mean → funding likely positive → go short\n        # Price below mean → funding likely negative → go long\n        if deviation < -self.threshold:\n            return Signal.BUY   # Collect negative funding\n        elif deviation > self.threshold:\n            return Signal.SELL  # Exit before funding flips\n        return Signal.HOLD\n\n    def reset(self) -> None:\n        pass" },
        ],
      },
      {
        id: "amm-arb",
        title: "AMM Arbitrage (Advanced)",
        content: `<p>Detects price discrepancies across AMM pools using Flint's built-in arb detection engine. This is a simplified demonstration — real arbitrage requires atomic execution via Jito bundles.</p>`,
        codeBlocks: [
          { language: "python", code: "from flint.strategy.base import Strategy\nfrom flint.models import Candle, Signal\nfrom typing import List\n\nclass AMMArbitrage(Strategy):\n    \"\"\"Detect arb opportunities via price divergence.\n    \n    Monitors price movement for divergence patterns\n    that indicate cross-venue mispricing.\n    In production, this would use the ArbDetector\n    with real pool state data.\n    \"\"\"\n    def __init__(self, window=12, divergence_bps=50):\n        self.window = window\n        self.divergence_bps = divergence_bps\n        self._prices: list[float] = []\n\n    @property\n    def name(self) -> str:\n        return f\"AMMArb({self.window}/{self.divergence_bps}bps)\"\n\n    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:\n        self._prices.append(candle.close)\n        if len(self._prices) < self.window:\n            return Signal.HOLD\n\n        recent = self._prices[-self.window:]\n        mean = sum(recent) / len(recent)\n        deviation_bps = abs(candle.close - mean) / mean * 10000\n\n        if deviation_bps > self.divergence_bps:\n            if candle.close < mean:\n                return Signal.BUY\n            else:\n                return Signal.SELL\n        return Signal.HOLD\n\n    def reset(self) -> None:\n        self._prices = []" },
        ],
      },
    ],
  },
]
```

- [ ] **Step 3: Create DocsSidebar component**

Create `ui/src/components/DocsSidebar.tsx`:

```tsx
import { useState } from 'react'
import type { DocSection } from '../data/docs-content'

interface Props {
  sections: DocSection[]
  activeTopic: string
  onSelect: (topicId: string) => void
}

export default function DocsSidebar({ sections, activeTopic, onSelect }: Props) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>(
    Object.fromEntries(sections.map(s => [s.id, true]))
  )

  const toggle = (id: string) => setExpanded(prev => ({ ...prev, [id]: !prev[id] }))

  return (
    <nav className="w-56 shrink-0 border-r border-border overflow-y-auto h-[calc(100vh-8rem)] sticky top-12 py-4 pr-4">
      {sections.map(section => (
        <div key={section.id} className="mb-4">
          <button
            onClick={() => toggle(section.id)}
            className="w-full text-left text-[10px] text-amber tracking-[0.2em] font-semibold py-1.5 flex items-center gap-2 hover:text-amber/80 transition-colors"
          >
            <span className={`text-[8px] transition-transform ${expanded[section.id] ? 'rotate-90' : ''}`}>
              {'>'}
            </span>
            {section.title}
          </button>
          {expanded[section.id] && (
            <div className="ml-3 border-l border-border/50 pl-3 space-y-0.5">
              {section.topics.map(topic => (
                <button
                  key={topic.id}
                  onClick={() => onSelect(topic.id)}
                  className={`block w-full text-left text-[11px] py-1.5 px-2 transition-all ${
                    activeTopic === topic.id
                      ? 'text-white bg-amber-glow border-l-2 border-amber -ml-[13px] pl-[13px]'
                      : 'text-ghost hover:text-terminal'
                  }`}
                >
                  {topic.title}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
    </nav>
  )
}
```

- [ ] **Step 4: Create DocsContent component**

Create `ui/src/components/DocsContent.tsx`:

```tsx
import type { DocTopic } from '../data/docs-content'

interface Props {
  topic: DocTopic | null
}

export default function DocsContent({ topic }: Props) {
  if (!topic) {
    return (
      <div className="flex-1 flex items-center justify-center text-ghost/40 text-sm">
        Select a topic from the sidebar
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto h-[calc(100vh-8rem)] py-6 px-8">
      <h2 className="font-[var(--font-display)] text-2xl text-white/90 italic mb-6">
        {topic.title}
      </h2>

      <div
        className="docs-prose text-[13px] text-ghost/80 leading-relaxed space-y-4"
        dangerouslySetInnerHTML={{ __html: topic.content }}
      />

      {topic.codeBlocks?.map((block, i) => (
        <div key={i} className="mt-4 border border-border bg-void rounded overflow-hidden">
          <div className="px-3 py-1.5 border-b border-border bg-surface/50 text-[9px] text-ghost/40 tracking-[0.2em]">
            {block.language.toUpperCase()}
          </div>
          <pre className="p-4 overflow-x-auto text-[12px] leading-relaxed">
            <code className="text-phosphor">{block.code}</code>
          </pre>
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 5: Create Docs page**

Create `ui/src/pages/Docs.tsx`:

```tsx
import { useState } from 'react'
import { docsContent } from '../data/docs-content'
import DocsSidebar from '../components/DocsSidebar'
import DocsContent from '../components/DocsContent'
import type { DocTopic } from '../data/docs-content'

export default function Docs() {
  const [activeTopic, setActiveTopic] = useState(docsContent[0]?.topics[0]?.id || '')

  const findTopic = (id: string): DocTopic | null => {
    for (const section of docsContent) {
      const topic = section.topics.find(t => t.id === id)
      if (topic) return topic
    }
    return null
  }

  return (
    <div className="flex -mx-6 -mt-8" style={{ minHeight: 'calc(100vh - 8rem)' }}>
      <DocsSidebar
        sections={docsContent}
        activeTopic={activeTopic}
        onSelect={setActiveTopic}
      />
      <DocsContent topic={findTopic(activeTopic)} />
    </div>
  )
}
```

- [ ] **Step 6: Add Docs route to App.tsx**

In `ui/src/App.tsx`:

1. Add import:
```tsx
import Docs from './pages/Docs'
```

2. Add nav item (insert between DATA and MEV):
```tsx
const navItems = [
  { to: '/', label: 'HOME', key: '1' },
  { to: '/backtest', label: 'LAB', key: '2' },
  { to: '/data', label: 'DATA', key: '3' },
  { to: '/docs', label: 'DOCS', key: '4' },
  { to: '/mev', label: 'MEV', key: '5' },
]
```

3. Add route:
```tsx
<Route path="/docs" element={<Docs />} />
```

- [ ] **Step 7: Verify UI builds**

Run: `cd /Users/sohan/Documents/solana_stuff/flint/ui && npm run build`
Expected: Build succeeds

- [ ] **Step 8: Commit**

```bash
git add ui/src/data/docs-content.ts ui/src/components/DocsSidebar.tsx ui/src/components/DocsContent.tsx ui/src/pages/Docs.tsx ui/src/App.tsx
git commit -m "feat: add docs page with sidebar nav, learning path content, code examples"
```

---

## Task 8: Strategy Lab — Monaco Editor Integration

**Files:**
- Create: `ui/src/components/CodeEditor.tsx`
- Create: `ui/src/hooks/useStrategies.ts`
- Modify: `ui/src/pages/BacktestLab.tsx` (full rewrite)
- Modify: `ui/package.json`

- [ ] **Step 1: Install Monaco editor**

Run: `cd /Users/sohan/Documents/solana_stuff/flint/ui && npm install @monaco-editor/react`

- [ ] **Step 2: Create CodeEditor wrapper**

Create `ui/src/components/CodeEditor.tsx`:

```tsx
import Editor from '@monaco-editor/react'

interface Props {
  value: string
  onChange: (value: string) => void
  height?: string
}

export default function CodeEditor({ value, onChange, height = '100%' }: Props) {
  return (
    <Editor
      height={height}
      defaultLanguage="python"
      theme="vs-dark"
      value={value}
      onChange={(v) => onChange(v || '')}
      options={{
        fontSize: 13,
        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        lineNumbers: 'on',
        renderLineHighlight: 'line',
        tabSize: 4,
        insertSpaces: true,
        wordWrap: 'on',
        padding: { top: 12 },
      }}
    />
  )
}
```

- [ ] **Step 3: Create useStrategies hook**

Create `ui/src/hooks/useStrategies.ts`:

```typescript
import { useState, useEffect, useCallback } from 'react'

interface UserStrategy {
  name: string
  file: string
}

export function useStrategies() {
  const [strategies, setStrategies] = useState<UserStrategy[]>([])
  const [loading, setLoading] = useState(false)

  const fetchStrategies = useCallback(async () => {
    try {
      const res = await fetch('/api/v1/user-strategies')
      const data = await res.json()
      setStrategies(data.strategies || [])
    } catch {
      setStrategies([])
    }
  }, [])

  useEffect(() => { fetchStrategies() }, [fetchStrategies])

  const save = async (name: string, code: string) => {
    setLoading(true)
    try {
      await fetch('/api/v1/user-strategies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, code }),
      })
      await fetchStrategies()
    } finally {
      setLoading(false)
    }
  }

  const load = async (name: string): Promise<string> => {
    const res = await fetch(`/api/v1/user-strategies/${name}`)
    const data = await res.json()
    return data.code || ''
  }

  const remove = async (name: string) => {
    await fetch(`/api/v1/user-strategies/${name}`, { method: 'DELETE' })
    await fetchStrategies()
  }

  const validate = async (code: string) => {
    const res = await fetch('/api/v1/user-strategies/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    })
    return res.json()
  }

  return { strategies, save, load, remove, validate, loading, refresh: fetchStrategies }
}
```

- [ ] **Step 4: Rewrite BacktestLab as Strategy Lab**

Fully rewrite `ui/src/pages/BacktestLab.tsx` with a split-view layout: Monaco editor on the left, config + results on the right. Key features:

- Template dropdown above editor (Blank, MA Crossover, RSI, Funding Harvest, AMM Arb)
- Strategy file tabs with save/open controls
- Monaco code editor taking ~55% width
- Right panel with market/dates/capital config and Run button
- Results display below config using existing components
- Ctrl+S saves, Ctrl+Enter runs backtest
- Strategy code sent as `code` field in backtest request

The template code strings should come from `docs-content.ts` example code blocks to stay DRY.

- [ ] **Step 5: Verify UI builds**

Run: `cd /Users/sohan/Documents/solana_stuff/flint/ui && npm run build`
Expected: Build succeeds

- [ ] **Step 6: Commit**

```bash
git add ui/src/components/CodeEditor.tsx ui/src/hooks/useStrategies.ts ui/src/pages/BacktestLab.tsx ui/package.json ui/package-lock.json
git commit -m "feat: upgrade BacktestLab to Strategy Lab with Monaco editor and user strategy management"
```

---

## Task 9: Collector Status Dashboard Component

**Files:**
- Create: `ui/src/components/CollectorStatus.tsx`
- Modify: `ui/src/pages/Dashboard.tsx`

- [ ] **Step 1: Create CollectorStatus component**

Create `ui/src/components/CollectorStatus.tsx`:

```tsx
import { useEffect, useState } from 'react'

interface StatusEntry {
  market: string
  data_type: string
  state: string
  last_updated: number | null
  row_count: number
  error_message: string | null
  progress_pct: number | null
}

export default function CollectorStatus() {
  const [entries, setEntries] = useState<StatusEntry[]>([])
  const [running, setRunning] = useState(false)

  useEffect(() => {
    const poll = () => {
      fetch('/api/v1/collector/status')
        .then(r => r.json())
        .then(d => {
          setEntries(d.status || [])
          setRunning(d.running || false)
        })
        .catch(() => {})
    }
    poll()
    const id = setInterval(poll, 5000)
    return () => clearInterval(id)
  }, [])

  const fmtTime = (ts: number | null) => {
    if (!ts) return '—'
    const ago = Math.floor((Date.now() / 1000) - ts)
    if (ago < 60) return `${ago}s ago`
    if (ago < 3600) return `${Math.floor(ago / 60)}m ago`
    return `${Math.floor(ago / 3600)}h ago`
  }

  const stateColor = (state: string) => {
    if (state === 'idle') return 'text-phosphor'
    if (state === 'collecting' || state === 'backfilling') return 'text-amber'
    if (state === 'error') return 'text-loss'
    return 'text-ghost'
  }

  if (entries.length === 0) {
    return (
      <div className="border border-border bg-surface/60 backdrop-blur p-4">
        <div className="flex items-center gap-2 mb-3">
          <span className="w-2 h-2 bg-amber/60" />
          <span className="text-[10px] text-ghost tracking-[0.2em]">DATA.COLLECTOR</span>
          <span className={`ml-auto text-[10px] ${running ? 'text-phosphor' : 'text-ghost/40'}`}>
            {running ? 'ACTIVE' : 'INACTIVE'}
          </span>
        </div>
        <div className="text-[11px] text-ghost/40">No collection data yet. Start the API server to begin automatic data collection.</div>
      </div>
    )
  }

  return (
    <div className="border border-border bg-surface/60 backdrop-blur">
      <div className="px-4 py-3 border-b border-border flex items-center gap-2">
        <span className="w-2 h-2 bg-amber/60" />
        <span className="text-[10px] text-ghost tracking-[0.2em]">DATA.COLLECTOR</span>
        <span className={`ml-auto text-[10px] flex items-center gap-1.5 ${running ? 'text-phosphor' : 'text-ghost/40'}`}>
          {running && <span className="w-1.5 h-1.5 rounded-full bg-phosphor animate-pulse" />}
          {running ? 'ACTIVE' : 'INACTIVE'}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-ghost/40 border-b border-border text-[10px] tracking-[0.15em]">
              <th className="py-2 px-4">MARKET</th>
              <th className="py-2 px-4">TYPE</th>
              <th className="py-2 px-4">STATUS</th>
              <th className="py-2 px-4 text-right">RECORDS</th>
              <th className="py-2 px-4">UPDATED</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e, i) => (
              <tr key={i} className="border-b border-border/30 hover:bg-amber-glow transition-colors">
                <td className="py-2 px-4 text-amber font-medium">{e.market}</td>
                <td className="py-2 px-4 text-ghost">{e.data_type}</td>
                <td className={`py-2 px-4 ${stateColor(e.state)}`}>
                  {e.state.toUpperCase()}
                  {e.progress_pct != null && e.state === 'backfilling' && (
                    <span className="ml-2 text-ghost/40">{e.progress_pct.toFixed(0)}%</span>
                  )}
                </td>
                <td className="py-2 px-4 text-right text-white/70 tabular-nums">{e.row_count.toLocaleString()}</td>
                <td className="py-2 px-4 text-ghost/40">{fmtTime(e.last_updated)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Add CollectorStatus to Dashboard**

In `ui/src/pages/Dashboard.tsx`, import and add the component after the DATA.COVERAGE section (before the spacer div):

```tsx
import CollectorStatus from '../components/CollectorStatus'

// ... inside the component, after the data table section:
<div className="mt-10" style={{ animation: 'fadeUp 0.6s ease 2.2s both' }}>
  <CollectorStatus />
</div>
```

- [ ] **Step 3: Verify UI builds**

Run: `cd /Users/sohan/Documents/solana_stuff/flint/ui && npm run build`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
git add ui/src/components/CollectorStatus.tsx ui/src/pages/Dashboard.tsx
git commit -m "feat: add data collector status panel to dashboard"
```

---

## Task 10: Final Integration Test + CSS for Docs

**Files:**
- Modify: `ui/src/index.css` (if needed for docs prose styles)
- Run full test suite

- [ ] **Step 1: Add docs prose CSS**

Add to the global CSS (check `ui/src/index.css` or wherever Tailwind base styles are) — styles for the docs content HTML:

```css
.docs-prose p { margin-bottom: 0.75rem; }
.docs-prose ul { list-style: disc; padding-left: 1.5rem; margin-bottom: 0.75rem; }
.docs-prose li { margin-bottom: 0.25rem; }
.docs-prose code { background: rgba(245, 158, 11, 0.1); padding: 0.15em 0.4em; border-radius: 2px; font-size: 0.9em; color: #f59e0b; }
.docs-prose strong { color: rgba(255, 255, 255, 0.9); }
.docs-prose table { width: 100%; border-collapse: collapse; margin-bottom: 0.75rem; }
.docs-prose td { padding: 0.4rem 0.75rem; border-bottom: 1px solid rgba(255,255,255,0.05); }
.docs-prose td:first-child { color: #f59e0b; font-family: monospace; white-space: nowrap; }
.docs-prose ol { list-style: decimal; padding-left: 1.5rem; margin-bottom: 0.75rem; }
```

- [ ] **Step 2: Run Python test suite**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && python -m pytest tests/ -v`
Expected: All tests PASS (94 original + ~19 new ≈ 113 tests)

- [ ] **Step 3: Run UI build**

Run: `cd /Users/sohan/Documents/solana_stuff/flint/ui && npm run build`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: finalize QuantConnect-style upgrade — docs, strategy lab, data collector"
```

- [ ] **Step 5: Run full verification**

Run: `cd /Users/sohan/Documents/solana_stuff/flint && python -m pytest tests/ -v && cd ui && npm run build`
Expected: All tests PASS, UI builds clean
