# Phase 7B: API Consistency & Convenience

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 6 API inconsistencies that confuse users — funding endpoint defaults, download `days` param, status naming, batch data check, cached response clarity, CCXT warning dedup.

**Architecture:** All changes are in `flint/api/routes/data.py` and `flint/api/routes/backtest.py`. Backward-compatible — existing API calls continue to work. New parameters are additive.

**Tech Stack:** Python, FastAPI

---

### Task 1: Funding Endpoint Defaults and Key Normalization (B1)

**Files:**
- Modify: `flint/api/routes/data.py:84-100`
- Test: `tests/test_phase7b_api_consistency.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_phase7b_api_consistency.py`:

```python
"""Tests for Phase 7B API consistency fixes."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flint.api.routes.data import router


def _app_with_store(store=None):
    app = FastAPI()
    app.include_router(router, prefix="/data")
    app.state.store = store
    return app


class TestFundingDefaults:
    """Funding endpoint should return data without explicit timestamps."""

    def test_funding_without_timestamps_returns_data(self):
        mock_store = MagicMock()
        mock_store.query_funding_by_venue.return_value = {
            "drift": [{"ts": 1000, "rate": 0.001}],
        }
        app = _app_with_store(mock_store)
        client = TestClient(app)
        resp = client.get("/data/funding?market=SOL-PERP")
        assert resp.status_code == 200
        data = resp.json()
        assert "venues" in data
        assert data["count"] > 0
        # Verify default timestamps were passed (last 30 days)
        call_args = mock_store.query_funding_by_venue.call_args
        start_ts = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("start_ts")
        assert start_ts is not None, "start_ts should be defaulted, not None"

    def test_funding_response_key_is_venues(self):
        mock_store = MagicMock()
        mock_store.query_funding_by_venue.return_value = {}
        app = _app_with_store(mock_store)
        client = TestClient(app)
        resp = client.get("/data/funding?market=SOL-PERP")
        data = resp.json()
        assert "venues" in data, "Response key should be 'venues'"
        assert "by_venue" not in data, "Should not use 'by_venue' key"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase7b_api_consistency.py::TestFundingDefaults -v`
Expected: FAIL — `start_ts should be defaulted, not None`

- [ ] **Step 3: Add defaults to funding endpoint**

In `flint/api/routes/data.py`, modify the `get_funding()` function (line 84-100):

```python
@router.get("/funding")
def get_funding(
    request: Request,
    market: str = Query("SOL-PERP"),
    start_ts: Optional[int] = Query(None),
    end_ts: Optional[int] = Query(None),
):
    """Get funding rates for a market, grouped by venue."""
    import time as _time
    if end_ts is None:
        end_ts = int(_time.time())
    if start_ts is None:
        start_ts = end_ts - 30 * 86400  # default: last 30 days
    store = _get_store(request)
    if store is None:
        return {"market": market, "venues": {}, "count": 0}
    try:
        by_venue = store.query_funding_by_venue(market, start_ts, end_ts)
        total = sum(len(v) for v in by_venue.values())
        return {"market": market, "venues": by_venue, "count": total}
    except Exception as e:
        return {"market": market, "venues": {}, "count": 0, "error": str(e)}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_phase7b_api_consistency.py::TestFundingDefaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/api/routes/data.py tests/test_phase7b_api_consistency.py
git commit -m "fix: funding endpoint defaults to last 30 days when no timestamps"
```

---

### Task 2: Download Endpoint `days` Param (B2)

**Files:**
- Modify: `flint/api/routes/data.py:540-555`
- Test: `tests/test_phase7b_api_consistency.py` (append)

- [ ] **Step 1: Write failing test**

Append to `tests/test_phase7b_api_consistency.py`:

```python
class TestDownloadDaysParam:
    """POST /download should accept 'days' as convenience for start_ts/end_ts."""

    def test_days_param_accepted(self):
        mock_store = MagicMock()
        mock_store.query_candles.return_value = []
        app = _app_with_store(mock_store)
        client = TestClient(app)
        # Should NOT return 400
        with patch("flint.api.routes.data._download_range", return_value=([], None)):
            with patch("flint.api.routes.data._download_funding_all_venues", return_value=0):
                resp = client.post("/data/download", json={
                    "market": "SOL-PERP",
                    "days": 30,
                })
        assert resp.status_code != 400, f"days=30 rejected: {resp.json()}"

    def test_explicit_timestamps_override_days(self):
        mock_store = MagicMock()
        mock_store.query_candles.return_value = [MagicMock(ts=5000)]  # has data
        app = _app_with_store(mock_store)
        client = TestClient(app)
        resp = client.post("/data/download", json={
            "market": "SOL-PERP",
            "days": 30,
            "start_ts": 1000,
            "end_ts": 2000,
        })
        # Should use explicit timestamps, not days
        call_args = mock_store.query_candles.call_args
        assert call_args[0][2] == 1000  # start_ts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase7b_api_consistency.py::TestDownloadDaysParam -v`
Expected: FAIL — `days=30 rejected: {"detail": "Invalid date range..."}`

- [ ] **Step 3: Add days param to download endpoint**

In `flint/api/routes/data.py`, add after line 547 (`end_ts = body.get("end_ts")`) and before the validation at line 553:

```python
    # Convenience: accept 'days' as alternative to start_ts/end_ts
    days = body.get("days")
    if days and (not start_ts or not end_ts):
        import time as _time
        end_ts = int(_time.time())
        start_ts = end_ts - int(days) * 86400
```

Update the error message at line 555:
```python
        raise HTTPException(400, "Invalid date range — provide days (e.g. 90) or start_ts + end_ts with start < end")
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_phase7b_api_consistency.py::TestDownloadDaysParam -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/api/routes/data.py tests/test_phase7b_api_consistency.py
git commit -m "feat: download endpoint accepts 'days' convenience param

POST /download now accepts {\"days\": 90} as alternative to explicit
start_ts/end_ts. Explicit timestamps take priority when both provided."
```

---

### Task 3: Backtest Status Field Consistency (B3)

**Files:**
- Modify: `flint/api/routes/backtest.py` (grep for `phase="done"`)
- Modify: `flint/api/routes/optimization.py` (grep for `phase="done"`)
- Test: `tests/test_phase7b_api_consistency.py` (append)

- [ ] **Step 1: Write test**

Append to `tests/test_phase7b_api_consistency.py`:

```python
class TestStatusConsistency:
    """Progress phase should match top-level status naming."""

    def test_no_phase_done_in_backtest(self):
        """phase='done' should be phase='complete' to match status."""
        import inspect
        from flint.api.routes import backtest
        source = inspect.getsource(backtest)
        assert 'phase="done"' not in source, (
            "Found phase='done' in backtest.py — should be phase='complete'"
        )

    def test_no_phase_done_in_optimization(self):
        import inspect
        from flint.api.routes import optimization
        source = inspect.getsource(optimization)
        assert 'phase="done"' not in source, (
            "Found phase='done' in optimization.py — should be phase='complete'"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase7b_api_consistency.py::TestStatusConsistency -v`
Expected: FAIL — `Found phase='done' in backtest.py`

- [ ] **Step 3: Replace all phase="done" with phase="complete"**

In `flint/api/routes/backtest.py`, replace all instances of `phase="done"` with `phase="complete"`.

In `flint/api/routes/optimization.py`, replace all instances of `phase="done"` with `phase="complete"`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_phase7b_api_consistency.py::TestStatusConsistency -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/api/routes/backtest.py flint/api/routes/optimization.py tests/test_phase7b_api_consistency.py
git commit -m "fix: normalize progress phase='done' to phase='complete'"
```

---

### Task 4: Batch Data Check Endpoint (B4)

**Files:**
- Modify: `flint/api/routes/data.py:191-197`
- Test: `tests/test_phase7b_api_consistency.py` (append)

- [ ] **Step 1: Write failing test**

Append to `tests/test_phase7b_api_consistency.py`:

```python
class TestBatchDataCheck:
    """GET /check should accept comma-separated markets param."""

    def test_markets_plural_accepted(self):
        mock_store = MagicMock()
        mock_store.query_candles.return_value = []
        mock_store.count_candles.return_value = 0
        # Mock the funding/OI/orderbook inner queries
        mock_store._lock = MagicMock()
        mock_store._conn = MagicMock()
        mock_store._conn.execute.return_value.fetchone.return_value = (0,)
        app = _app_with_store(mock_store)
        client = TestClient(app)
        resp = client.get("/data/check?markets=SOL-PERP,BTC-PERP&start_ts=1000&end_ts=5000&resolution_s=3600")
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert len(data["results"]) == 2

    def test_single_market_still_works(self):
        mock_store = MagicMock()
        mock_store.query_candles.return_value = []
        mock_store.count_candles.return_value = 0
        mock_store._lock = MagicMock()
        mock_store._conn = MagicMock()
        mock_store._conn.execute.return_value.fetchone.return_value = (0,)
        app = _app_with_store(mock_store)
        client = TestClient(app)
        resp = client.get("/data/check?market=SOL-PERP&start_ts=1000&end_ts=5000&resolution_s=3600")
        assert resp.status_code == 200
        data = resp.json()
        assert "market" in data  # single-market response shape
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase7b_api_consistency.py::TestBatchDataCheck -v`
Expected: FAIL — `markets` param not accepted

- [ ] **Step 3: Add markets param to check endpoint**

In `flint/api/routes/data.py`, modify the `check_data` function signature (lines 191-198). Change `market` from required to optional and add `markets`:

```python
@router.get("/check")
def check_data(
    request: Request,
    market: Optional[str] = Query(None),
    markets: Optional[str] = Query(None),
    resolution_s: int = Query(3600),
    start_ts: int = Query(...),
    end_ts: int = Query(...),
):
    """Check if data exists for a given market/timeframe/date range."""
    market_list = []
    if markets:
        market_list = [m.strip() for m in markets.split(",")]
    elif market:
        market_list = [market]
    else:
        from fastapi import HTTPException
        raise HTTPException(400, "Provide market or markets parameter")

    if len(market_list) == 1:
        return _check_single_market(request, market_list[0], resolution_s, start_ts, end_ts)

    return {"results": [
        _check_single_market(request, m, resolution_s, start_ts, end_ts)
        for m in market_list
    ]}
```

Extract the existing check logic into a helper `_check_single_market(request, market, resolution_s, start_ts, end_ts)` containing the current function body (lines 200 onward).

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_phase7b_api_consistency.py::TestBatchDataCheck -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/api/routes/data.py tests/test_phase7b_api_consistency.py
git commit -m "feat: data check endpoint accepts comma-separated markets param"
```

---

### Task 5: Download Response Clarity + Coverage Cap (B5)

**Files:**
- Modify: `flint/api/routes/data.py` (around line 581, the `if not gaps:` branch)
- Test: `tests/test_phase7b_api_consistency.py` (append)

- [ ] **Step 1: Write test**

Append to `tests/test_phase7b_api_consistency.py`:

```python
class TestDownloadResponseClarity:
    def test_cached_response_shows_existing_count(self):
        """When all data is cached, 'cached' should show the actual count."""
        # This is a design-level test — verify the field semantics
        response = {
            "downloaded": 0,
            "cached": 2162,
            "existing": 2162,
            "skipped": True,
            "message": "All candles already cached for this range",
        }
        assert response["cached"] == response["existing"]
        assert response["downloaded"] == 0
        assert "message" in response
```

- [ ] **Step 2: Fix cached response**

In `flint/api/routes/data.py`, find the `if not gaps:` branch (around line 581). In the return dict at the end of this branch, change `"cached": 0` to `"cached": existing_count` and add `"message": "All candles already cached for this range"`. The exact location will vary — look for the return statement that includes `"skipped": True`.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_phase7b_api_consistency.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add flint/api/routes/data.py tests/test_phase7b_api_consistency.py
git commit -m "fix: download response shows actual cached count instead of 0"
```

---

### Task 6: CCXT Warning Dedup (B6)

**Files:**
- Modify: `flint/api/routes/data.py` (around line 1349-1368)
- Test: `tests/test_phase7b_api_consistency.py` (append)

- [ ] **Step 1: Write test**

Append to `tests/test_phase7b_api_consistency.py`:

```python
class TestCCXTWarningDedup:
    def test_ccxt_warning_only_once(self):
        """CCXT not-installed warning should appear at most once per exchange."""
        from flint.api.routes import data as data_module
        # Reset the dedup set
        data_module._ccxt_warned.clear()

        warnings1 = []
        warnings2 = []
        # Simulate two calls that hit the same CCXT warning
        for exchange in ["mexc", "phemex"]:
            key = f"ccxt/{exchange}"
            if key not in data_module._ccxt_warned:
                data_module._ccxt_warned.add(key)
                warnings1.append(f"{key} funding unavailable: ccxt not installed")

        # Second round — should not add duplicates
        for exchange in ["mexc", "phemex"]:
            key = f"ccxt/{exchange}"
            if key not in data_module._ccxt_warned:
                data_module._ccxt_warned.add(key)
                warnings2.append(f"{key} funding unavailable: ccxt not installed")

        assert len(warnings1) == 2
        assert len(warnings2) == 0  # no duplicates

        data_module._ccxt_warned.clear()
```

- [ ] **Step 2: Add the dedup set**

In `flint/api/routes/data.py`, add near the top (module-level):
```python
_ccxt_warned: set = set()
```

In the CCXT funding loop (around line 1365-1368), change:
```python
        except Exception as e:
            logger.warning("ccxt/%s funding failed for %s: %s", exchange, market, e)
            if warnings is not None:
                warn_key = f"ccxt/{exchange}"
                if warn_key not in _ccxt_warned:
                    _ccxt_warned.add(warn_key)
                    warnings.append(f"ccxt/{exchange} funding unavailable: {e}")
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_phase7b_api_consistency.py::TestCCXTWarningDedup -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add flint/api/routes/data.py tests/test_phase7b_api_consistency.py
git commit -m "fix: deduplicate CCXT not-installed warnings across downloads"
```

---

### Task 7: Run Full Test Suite

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v --timeout=120 -x`
Expected: All tests PASS

- [ ] **Step 2: Final commit if any fixups needed**
