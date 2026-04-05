# Pyth Price Pipeline + Data Model Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace multi-venue candle sources with Pyth oracle prices as the sole canonical price source, migrate existing data, and update the download API.

**Architecture:** New `PythCandleProvider` fetches OHLCV from Pyth's TradingView-compatible Benchmarks API (`benchmarks.pyth.network`). Download endpoint refactored to always fetch Pyth candles, with `execution_venues` controlling supplementary data (funding/borrow/orderbooks). Auto-migration backfills Pyth candles for existing markets on first startup.

**Tech Stack:** Python, httpx, DuckDB, FastAPI, Pyth Benchmarks API (free, keyless)

**Spec:** `docs/superpowers/specs/2026-04-05-pyth-pricing-venue-fill-pipelines-design.md` (Sub-project 1)

---

## Task 1: PythCandleProvider

**Files:**
- Create: `flint/providers/pyth_candles.py`
- Test: `tests/test_pyth_candles.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pyth_candles.py
"""Tests for PythCandleProvider — fetches OHLCV from Pyth Benchmarks API."""
import json
from unittest.mock import MagicMock, patch

from flint.providers.pyth_candles import PythCandleProvider


MOCK_TV_RESPONSE = {
    "s": "ok",
    "t": [1700000000, 1700003600, 1700007200],
    "o": [100.0, 101.0, 102.0],
    "h": [101.5, 102.5, 103.0],
    "l": [99.5, 100.5, 101.0],
    "c": [101.0, 102.0, 102.5],
    "v": [50000.0, 60000.0, 55000.0],
}


def test_fetch_candles():
    provider = PythCandleProvider()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = MOCK_TV_RESPONSE
    mock_resp.raise_for_status = MagicMock()

    with patch.object(provider, "_client") as mock_client:
        mock_client.get.return_value = mock_resp
        candles = provider.fetch_candles("SOL-PERP", 3600, 1700000000, 1700007200)

    assert len(candles) == 3
    assert candles[0].market == "SOL-PERP"
    assert candles[0].venue == "pyth"
    assert candles[0].ts == 1700000000
    assert candles[0].open == 100.0
    assert candles[0].close == 101.0
    assert candles[0].resolution_s == 3600


def test_market_to_symbol():
    provider = PythCandleProvider()
    assert provider._market_to_symbol("SOL-PERP") == "Crypto.SOL/USD"
    assert provider._market_to_symbol("BTC-PERP") == "Crypto.BTC/USD"
    assert provider._market_to_symbol("ETH-PERP") == "Crypto.ETH/USD"


def test_resolution_to_tv_format():
    provider = PythCandleProvider()
    assert provider._resolution_to_tv("60") or provider._resolution_to_tv(60) is not None
    assert provider._resolution_to_tv(3600) == "60"
    assert provider._resolution_to_tv(86400) == "1D"
    assert provider._resolution_to_tv(300) == "5"


def test_fetch_candles_no_data():
    provider = PythCandleProvider()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"s": "no_data", "t": [], "o": [], "h": [], "l": [], "c": [], "v": []}
    mock_resp.raise_for_status = MagicMock()

    with patch.object(provider, "_client") as mock_client:
        mock_client.get.return_value = mock_resp
        candles = provider.fetch_candles("SOL-PERP", 3600, 1700000000, 1700007200)

    assert candles == []


def test_supported_markets():
    provider = PythCandleProvider()
    markets = provider.supported_markets()
    assert "SOL-PERP" in markets
    assert "BTC-PERP" in markets
    assert "ETH-PERP" in markets
    assert len(markets) >= 10


def test_close():
    provider = PythCandleProvider()
    provider.close()  # should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pyth_candles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flint.providers.pyth_candles'`

- [ ] **Step 3: Implement PythCandleProvider**

```python
# flint/providers/pyth_candles.py
"""Pyth Benchmarks candle provider.

Fetches OHLCV data from Pyth's TradingView-compatible API.
Endpoint: https://benchmarks.pyth.network/v1/shims/tradingview/history

Free, keyless. Supports 1m to 1W resolutions.
"""
from __future__ import annotations

from typing import List, Optional

import httpx

from ..models import Candle
from .registry import DataProvider, register

_BENCHMARKS_URL = "https://benchmarks.pyth.network/v1/shims/tradingview"

# Map Flint market names to Pyth TradingView symbols
_MARKET_SYMBOLS = {
    "SOL-PERP": "Crypto.SOL/USD",
    "BTC-PERP": "Crypto.BTC/USD",
    "ETH-PERP": "Crypto.ETH/USD",
    "BONK-PERP": "Crypto.BONK/USD",
    "JUP-PERP": "Crypto.JUP/USD",
    "WIF-PERP": "Crypto.WIF/USD",
    "PYTH-PERP": "Crypto.PYTH/USD",
    "DOGE-PERP": "Crypto.DOGE/USD",
    "AVAX-PERP": "Crypto.AVAX/USD",
    "LINK-PERP": "Crypto.LINK/USD",
    "SUI-PERP": "Crypto.SUI/USD",
    "ARB-PERP": "Crypto.ARB/USD",
    "XRP-PERP": "Crypto.XRP/USD",
    "RENDER-PERP": "Crypto.RENDER/USD",
    "INJ-PERP": "Crypto.INJ/USD",
    "OP-PERP": "Crypto.OP/USD",
    "TIA-PERP": "Crypto.TIA/USD",
    "SEI-PERP": "Crypto.SEI/USD",
    "BNB-PERP": "Crypto.BNB/USD",
    "DRIFT-PERP": "Crypto.DRIFT/USD",
    # Spot pairs
    "SOL": "Crypto.SOL/USD",
    "BTC": "Crypto.BTC/USD",
    "ETH": "Crypto.ETH/USD",
}

# Resolution seconds to TradingView format
_RESOLUTION_MAP = {
    60: "1",
    300: "5",
    900: "15",
    1800: "30",
    3600: "60",
    14400: "240",
    86400: "1D",
    604800: "1W",
}


@register
class PythCandleProvider(DataProvider):
    """Fetches OHLCV candles from Pyth Benchmarks TradingView API."""

    name = "pyth_candles"
    requires_api_key = False

    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._client = client or httpx.Client(timeout=60)
        self._owns_client = client is None

    def is_available(self) -> bool:
        return True

    def supported_data_types(self) -> list:
        return ["candles"]

    def supported_markets(self) -> List[str]:
        return list(_MARKET_SYMBOLS.keys())

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _market_to_symbol(self, market: str) -> str:
        """Convert Flint market name to Pyth TradingView symbol."""
        symbol = _MARKET_SYMBOLS.get(market)
        if symbol:
            return symbol
        # Try extracting base from market name (e.g., "SOL-PERP" -> "SOL")
        base = market.split("-")[0]
        return f"Crypto.{base}/USD"

    def _resolution_to_tv(self, resolution_s: int) -> str:
        """Convert resolution in seconds to TradingView format."""
        return _RESOLUTION_MAP.get(resolution_s, str(resolution_s // 60))

    def fetch_candles(
        self, market: str, resolution_s: int, start_ts: int, end_ts: int,
    ) -> List[Candle]:
        """Fetch OHLCV candles from Pyth Benchmarks API."""
        symbol = self._market_to_symbol(market)
        tv_res = self._resolution_to_tv(resolution_s)

        resp = self._client.get(
            f"{_BENCHMARKS_URL}/history",
            params={
                "symbol": symbol,
                "resolution": tv_res,
                "from": start_ts,
                "to": end_ts,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("s") != "ok" or not data.get("t"):
            return []

        candles = []
        for i in range(len(data["t"])):
            candles.append(Candle(
                ts=data["t"][i],
                open=data["o"][i],
                high=data["h"][i],
                low=data["l"][i],
                close=data["c"][i],
                volume=data["v"][i] if data.get("v") else 0.0,
                market=market,
                resolution_s=resolution_s,
                venue="pyth",
            ))
        return candles
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pyth_candles.py -v`
Expected: 6 passed

- [ ] **Step 5: Register in providers/__init__.py**

Add to `flint/providers/__init__.py`:
```python
from .pyth_candles import PythCandleProvider
```

- [ ] **Step 6: Run existing provider tests**

Run: `pytest tests/ -k "provider" -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add flint/providers/pyth_candles.py flint/providers/__init__.py tests/test_pyth_candles.py
git commit -m "feat: add PythCandleProvider for Benchmarks API OHLCV data"
```

---

## Task 2: Config — price_source and tardis fields

**Files:**
- Modify: `flint/config.py`
- Test: `tests/test_pyth_config.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_pyth_config.py
def test_price_source_default():
    from flint.config import load_config
    cfg = load_config()
    assert cfg.price_source == "pyth"


def test_tardis_config_defaults():
    from flint.config import load_config
    cfg = load_config()
    assert cfg.tardis_api_key == ""
    assert cfg.tardis_max_gb_per_request == 1.0


def test_tardis_config_from_env(monkeypatch):
    monkeypatch.setenv("FLINT_TARDIS_API_KEY", "td_test123")
    monkeypatch.setenv("FLINT_TARDIS_MAX_GB_PER_REQUEST", "2.5")
    from flint.config import load_config
    cfg = load_config()
    assert cfg.tardis_api_key == "td_test123"
    assert cfg.tardis_max_gb_per_request == 2.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pyth_config.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'price_source'`

- [ ] **Step 3: Add fields to FlintConfig**

In `flint/config.py`, add to the FlintConfig class:

```python
# Price source
price_source: str = "pyth"

# Tardis.dev (for CEX orderbook data)
tardis_api_key: str = ""
tardis_max_gb_per_request: float = 1.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pyth_config.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add flint/config.py tests/test_pyth_config.py
git commit -m "feat: add price_source and tardis config fields"
```

---

## Task 3: Download API Refactor — Pyth-first candle downloads

**Files:**
- Modify: `flint/api/routes/data.py` (download endpoint)
- Test: `tests/test_pyth_download_api.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_pyth_download_api.py
"""Tests for refactored download endpoint — Pyth-first candle downloads."""
import os
import tempfile
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from flint.models import Candle


def _make_app():
    from flint.api.main import create_app
    from flint.store import FlintStore
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)
    store = FlintStore(path)
    app = create_app(store=store)
    return app, store, path


def test_download_uses_pyth_by_default():
    """Download endpoint should fetch from Pyth regardless of venue param."""
    app, store, path = _make_app()
    try:
        mock_candles = [
            Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "pyth"),
            Candle(1700003600, 100.5, 102.0, 100.0, 101.0, 1100.0, "SOL-PERP", 3600, "pyth"),
        ]

        with patch("flint.api.routes.data._download_pyth_candles", return_value=(mock_candles, None)):
            client = TestClient(app)
            resp = client.post("/api/v1/data/download", json={
                "market": "SOL-PERP",
                "start_ts": 1700000000,
                "end_ts": 1700007200,
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("source") == "pyth"
    finally:
        store.close()
        os.unlink(path)


def test_execution_venues_param_accepted():
    """New execution_venues param should be accepted."""
    app, store, path = _make_app()
    try:
        with patch("flint.api.routes.data._download_pyth_candles", return_value=([], None)):
            client = TestClient(app)
            resp = client.post("/api/v1/data/download", json={
                "market": "SOL-PERP",
                "start_ts": 1700000000,
                "end_ts": 1700007200,
                "execution_venues": ["drift", "hyperliquid"],
            })
            assert resp.status_code == 200
    finally:
        store.close()
        os.unlink(path)


def test_funding_venues_alias_works():
    """Old funding_venues param should work as alias for execution_venues."""
    app, store, path = _make_app()
    try:
        with patch("flint.api.routes.data._download_pyth_candles", return_value=([], None)):
            client = TestClient(app)
            resp = client.post("/api/v1/data/download", json={
                "market": "SOL-PERP",
                "start_ts": 1700000000,
                "end_ts": 1700007200,
                "funding_venues": ["drift"],
            })
            assert resp.status_code == 200
    finally:
        store.close()
        os.unlink(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pyth_download_api.py -v`
Expected: FAIL — `_download_pyth_candles` doesn't exist or `source` isn't `"pyth"`

- [ ] **Step 3: Implement download refactor**

In `flint/api/routes/data.py`:

1. Add a new helper function `_download_pyth_candles()`:

```python
def _download_pyth_candles(market: str, resolution_s: int, start_ts: int, end_ts: int):
    """Download candles from Pyth Benchmarks API.
    
    Returns (List[Candle], Optional[error_msg])
    """
    from flint.providers.pyth_candles import PythCandleProvider
    provider = PythCandleProvider()
    try:
        candles = provider.fetch_candles(market, resolution_s, start_ts, end_ts)
        return candles, None
    except Exception as e:
        return [], str(e)
    finally:
        provider.close()
```

2. Modify the `download_market_data` handler:
   - Accept `execution_venues` parameter (list of venue IDs)
   - Accept `funding_venues` as alias (if `execution_venues` not provided, use `funding_venues`)
   - Replace the candle download logic: instead of calling `_download_range()` or `_download_range_for_venue()`, call `_download_pyth_candles()`
   - Keep the old `venue` param but ignore it for candle downloads (backward compat — log a deprecation warning)
   - Set `source = "pyth"` in the response
   - Still call `_download_funding_all_venues()` for funding data using the resolved execution venues list

3. The old `_download_range()` and `_download_range_for_venue()` functions remain in the file (not deleted) as they may be used by other code paths. They just stop being called from the download endpoint.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pyth_download_api.py -v`
Expected: 3 passed

- [ ] **Step 5: Run existing API tests**

Run: `pytest tests/ -k "api" -v`
Expected: All pass (backward compat via `funding_venues` alias)

- [ ] **Step 6: Commit**

```bash
git add flint/api/routes/data.py tests/test_pyth_download_api.py
git commit -m "feat: refactor download endpoint to use Pyth as sole price source"
```

---

## Task 4: CLI init — Switch to Pyth

**Files:**
- Modify: `flint/cli.py` (init command, around line 133-181)
- Test: `tests/test_pyth_cli_init.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_pyth_cli_init.py
"""Tests for flint init using Pyth candles."""
from unittest.mock import MagicMock, patch

from flint.models import Candle


def test_init_uses_pyth_provider():
    """flint init should use PythCandleProvider instead of DriftCandleProvider."""
    mock_candles = [
        Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "pyth"),
    ]

    with patch("flint.cli.PythCandleProvider") as MockProvider:
        instance = MockProvider.return_value
        instance.fetch_candles.return_value = mock_candles
        instance.close = MagicMock()

        # Import after patching
        from flint.cli import _download_init_candles
        candles = _download_init_candles("SOL-PERP", 3600, 1700000000, 1700003600)

        assert len(candles) == 1
        assert candles[0].venue == "pyth"
        MockProvider.assert_called_once()
        instance.fetch_candles.assert_called_once_with("SOL-PERP", 3600, 1700000000, 1700003600)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pyth_cli_init.py -v`
Expected: FAIL — `ImportError: cannot import name '_download_init_candles'`

- [ ] **Step 3: Refactor CLI init**

In `flint/cli.py`:

1. Extract the candle download logic from the `init` command into a helper function:

```python
def _download_init_candles(market: str, resolution_s: int, start_ts: int, end_ts: int) -> list:
    """Download candles for init using Pyth as primary source."""
    from flint.providers.pyth_candles import PythCandleProvider
    provider = PythCandleProvider()
    try:
        candles = provider.fetch_candles(market, resolution_s, start_ts, end_ts)
        if candles:
            return candles
    except Exception:
        pass
    finally:
        provider.close()

    # Fallback to Drift if Pyth fails
    from flint.providers.drift_candles import DriftCandleProvider
    drift = DriftCandleProvider()
    try:
        return drift.fetch_candles(market, resolution_s, start_ts, end_ts)
    except Exception:
        return []
    finally:
        drift.close()
```

2. Replace the existing candle download block in the `init` command to call `_download_init_candles()` instead of directly using `DriftCandleProvider` / `DriftS3Provider`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pyth_cli_init.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add flint/cli.py tests/test_pyth_cli_init.py
git commit -m "feat: switch flint init to use Pyth candles (Drift fallback)"
```

---

## Task 5: Store — Migration helper and Pyth candle fallback

**Files:**
- Modify: `flint/store.py`
- Test: `tests/test_pyth_migration.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pyth_migration.py
"""Tests for Pyth migration helpers in FlintStore."""
import os
import tempfile

from flint.models import Candle, SyncMetadata
from flint.store import FlintStore


def _make_store():
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)
    return FlintStore(path), path


def test_get_markets_needing_pyth_migration():
    """Should return markets that have venue candles but no Pyth candles."""
    store, path = _make_store()
    try:
        # Insert Drift candles for SOL-PERP
        store.upsert_candles([
            Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "drift"),
            Candle(1700003600, 100.5, 102.0, 100.0, 101.0, 1100.0, "SOL-PERP", 3600, "drift"),
        ])
        # Insert Pyth candles for BTC-PERP (already migrated)
        store.upsert_candles([
            Candle(1700000000, 40000.0, 40100.0, 39900.0, 40050.0, 500.0, "BTC-PERP", 3600, "pyth"),
        ])

        needs_migration = store.get_markets_needing_pyth_migration()
        assert "SOL-PERP" in needs_migration
        assert "BTC-PERP" not in needs_migration
    finally:
        store.close()
        os.unlink(path)


def test_get_market_date_range():
    """Should return the min/max timestamps for a market's candle data."""
    store, path = _make_store()
    try:
        store.upsert_candles([
            Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "drift"),
            Candle(1700050000, 100.5, 102.0, 100.0, 101.0, 1100.0, "SOL-PERP", 3600, "drift"),
        ])

        start, end = store.get_market_date_range("SOL-PERP")
        assert start == 1700000000
        assert end == 1700050000
    finally:
        store.close()
        os.unlink(path)


def test_get_market_date_range_no_data():
    store, path = _make_store()
    try:
        result = store.get_market_date_range("SOL-PERP")
        assert result is None
    finally:
        store.close()
        os.unlink(path)


def test_query_candles_pyth_fallback():
    """When querying with venue='pyth' and no Pyth data exists, fall back to any venue."""
    store, path = _make_store()
    try:
        store.upsert_candles([
            Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "drift"),
        ])

        # Query for pyth specifically — should return empty
        pyth_candles = store.query_candles("SOL-PERP", 3600, venue="pyth")
        assert len(pyth_candles) == 0

        # Query with fallback
        candles = store.query_candles_with_fallback("SOL-PERP", 3600, 1700000000, 1700003600)
        assert len(candles) == 1
        # Returns whatever venue is available
        assert candles[0].ts == 1700000000
    finally:
        store.close()
        os.unlink(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pyth_migration.py -v`
Expected: FAIL — `AttributeError: 'FlintStore' object has no attribute 'get_markets_needing_pyth_migration'`

- [ ] **Step 3: Implement migration helpers**

Add to `FlintStore` in `flint/store.py`:

```python
def get_markets_needing_pyth_migration(self) -> list:
    """Return markets that have non-Pyth candle data but no Pyth candles."""
    with self._lock:
        rows = self._conn.execute("""
            SELECT DISTINCT market FROM candles
            WHERE venue != 'pyth'
            AND market NOT IN (
                SELECT DISTINCT market FROM candles WHERE venue = 'pyth'
            )
        """).fetchall()
    return [r[0] for r in rows]

def get_market_date_range(self, market: str):
    """Return (min_ts, max_ts) for a market's candle data across all venues.
    
    Returns None if no data exists.
    """
    with self._lock:
        rows = self._conn.execute(
            "SELECT MIN(ts), MAX(ts) FROM candles WHERE market = ?",
            [market],
        ).fetchall()
    if rows and rows[0][0] is not None:
        return (rows[0][0], rows[0][1])
    return None

def query_candles_with_fallback(
    self, market: str, resolution_s: int,
    start_ts: int = None, end_ts: int = None,
) -> list:
    """Query candles preferring venue='pyth', falling back to any venue."""
    # Try Pyth first
    candles = self.query_candles(market, resolution_s, start_ts, end_ts, venue="pyth")
    if candles:
        return candles
    # Fallback: any venue
    return self.query_candles(market, resolution_s, start_ts, end_ts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pyth_migration.py -v`
Expected: 4 passed

- [ ] **Step 5: Run existing store tests**

Run: `pytest tests/test_store.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add flint/store.py tests/test_pyth_migration.py
git commit -m "feat: add Pyth migration helpers and candle fallback query"
```

---

## Task 6: Auto-Migration on Startup

**Files:**
- Create: `flint/migration.py`
- Modify: `flint/api/main.py` (startup hook)
- Test: `tests/test_pyth_auto_migration.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pyth_auto_migration.py
"""Tests for auto-migration of candle data to Pyth on startup."""
import os
import tempfile
from unittest.mock import MagicMock, patch

from flint.models import Candle
from flint.store import FlintStore
from flint.migration import run_pyth_migration


def _make_store():
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)
    return FlintStore(path), path


def test_migration_downloads_pyth_for_existing_markets():
    store, path = _make_store()
    try:
        # Pre-existing Drift candles
        store.upsert_candles([
            Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "drift"),
            Candle(1700003600, 100.5, 102.0, 100.0, 101.0, 1100.0, "SOL-PERP", 3600, "drift"),
        ])

        mock_pyth_candles = [
            Candle(1700000000, 100.1, 101.1, 99.1, 100.6, 1000.0, "SOL-PERP", 3600, "pyth"),
            Candle(1700003600, 100.6, 102.1, 100.1, 101.1, 1100.0, "SOL-PERP", 3600, "pyth"),
        ]

        with patch("flint.migration.PythCandleProvider") as MockProvider:
            instance = MockProvider.return_value
            instance.fetch_candles.return_value = mock_pyth_candles
            instance.close = MagicMock()

            result = run_pyth_migration(store)

        assert result["markets_migrated"] == ["SOL-PERP"]
        assert result["candles_downloaded"] == 2

        # Verify Pyth candles now exist
        pyth = store.query_candles("SOL-PERP", 3600, venue="pyth")
        assert len(pyth) == 2
    finally:
        store.close()
        os.unlink(path)


def test_migration_skips_already_migrated():
    store, path = _make_store()
    try:
        # Already has Pyth candles
        store.upsert_candles([
            Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "pyth"),
        ])

        result = run_pyth_migration(store)
        assert result["markets_migrated"] == []
        assert result["candles_downloaded"] == 0
    finally:
        store.close()
        os.unlink(path)


def test_migration_handles_pyth_failure():
    store, path = _make_store()
    try:
        store.upsert_candles([
            Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "drift"),
        ])

        with patch("flint.migration.PythCandleProvider") as MockProvider:
            instance = MockProvider.return_value
            instance.fetch_candles.side_effect = Exception("Pyth API down")
            instance.close = MagicMock()

            result = run_pyth_migration(store)

        # Should not crash, just report the failure
        assert result["errors"] == ["SOL-PERP: Pyth API down"]
        assert result["markets_migrated"] == []
    finally:
        store.close()
        os.unlink(path)


def test_migration_is_idempotent():
    store, path = _make_store()
    try:
        store.upsert_candles([
            Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "drift"),
        ])

        mock_candles = [
            Candle(1700000000, 100.1, 101.1, 99.1, 100.6, 1000.0, "SOL-PERP", 3600, "pyth"),
        ]

        with patch("flint.migration.PythCandleProvider") as MockProvider:
            instance = MockProvider.return_value
            instance.fetch_candles.return_value = mock_candles
            instance.close = MagicMock()

            # Run twice
            run_pyth_migration(store)
            result = run_pyth_migration(store)

        # Second run should find nothing to migrate
        assert result["markets_migrated"] == []
    finally:
        store.close()
        os.unlink(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pyth_auto_migration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flint.migration'`

- [ ] **Step 3: Implement migration module**

```python
# flint/migration.py
"""Auto-migration for Pyth price data.

On first run after upgrade, detects markets with existing venue candles
but no Pyth candles, and backfills from Pyth Benchmarks API.
"""
from __future__ import annotations

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


def run_pyth_migration(store) -> dict:
    """Migrate existing markets to Pyth candle data.
    
    Returns summary dict with markets_migrated, candles_downloaded, errors.
    Idempotent — safe to run multiple times.
    """
    from .providers.pyth_candles import PythCandleProvider

    result = {"markets_migrated": [], "candles_downloaded": 0, "errors": []}

    markets = store.get_markets_needing_pyth_migration()
    if not markets:
        return result

    logger.info(f"Pyth migration: {len(markets)} markets need migration: {markets}")

    provider = PythCandleProvider()
    try:
        for market in markets:
            date_range = store.get_market_date_range(market)
            if not date_range:
                continue

            start_ts, end_ts = date_range
            logger.info(f"Migrating {market}: {start_ts} → {end_ts}")

            try:
                candles = provider.fetch_candles(market, 3600, start_ts, end_ts)
                if candles:
                    count = store.upsert_candles(candles)
                    result["markets_migrated"].append(market)
                    result["candles_downloaded"] += count
                    logger.info(f"Migrated {market}: {count} Pyth candles")
                else:
                    result["errors"].append(f"{market}: no Pyth data available")
            except Exception as e:
                result["errors"].append(f"{market}: {e}")
                logger.warning(f"Pyth migration failed for {market}: {e}")
    finally:
        provider.close()

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pyth_auto_migration.py -v`
Expected: 4 passed

- [ ] **Step 5: Wire migration into startup**

In `flint/api/main.py`, find the lifespan/startup hook. Add migration call:

```python
# In the startup section (after store initialization)
from flint.migration import run_pyth_migration
try:
    migration_result = run_pyth_migration(store)
    if migration_result["markets_migrated"]:
        logger.info(f"Pyth migration complete: {migration_result}")
except Exception as e:
    logger.warning(f"Pyth migration failed (non-fatal): {e}")
```

This runs once on startup. Since `run_pyth_migration` is idempotent, subsequent starts are no-ops.

- [ ] **Step 6: Commit**

```bash
git add flint/migration.py flint/api/main.py tests/test_pyth_auto_migration.py
git commit -m "feat: add auto-migration to Pyth candles on startup"
```

---

## Task 7: Backtest Engine — Use Pyth candles with fallback

**Files:**
- Modify: `flint/backtest/engine.py`
- Test: `tests/test_pyth_backtest_integration.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_pyth_backtest_integration.py
"""Test that backtest engine uses Pyth candles by default."""
from flint.models import Candle


def test_backtest_works_with_pyth_venue_candles():
    """Candles with venue='pyth' should work normally in backtests."""
    from flint.backtest.engine import BacktestEngine

    candles = [
        Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "pyth"),
        Candle(1700003600, 100.5, 102.0, 100.0, 101.0, 1100.0, "SOL-PERP", 3600, "pyth"),
        Candle(1700007200, 101.0, 103.0, 100.5, 102.0, 1200.0, "SOL-PERP", 3600, "pyth"),
    ]

    code = '''
class Strategy:
    def __init__(self):
        self.bar = 0
    def on_candle(self, ctx):
        self.bar += 1
        if self.bar == 1:
            ctx.market_order("SOL-PERP", "buy", 1.0)
'''

    engine = BacktestEngine(
        candles=candles,
        strategy_code=code,
        initial_capital=10000.0,
        fee_rate=0.0006,
    )
    result = engine.run()
    assert result is not None
    assert result.total_trades >= 1
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_pyth_backtest_integration.py -v`
Expected: PASS — the engine shouldn't care about the venue field on candles. If it does fail, the engine has venue-specific logic that needs updating.

- [ ] **Step 3: Verify and fix if needed**

Read `flint/backtest/engine.py` and check if there's any logic that filters or treats candles differently based on `venue`. If so, ensure `venue='pyth'` is handled. The engine should be venue-agnostic for price data.

- [ ] **Step 4: Commit**

```bash
git add tests/test_pyth_backtest_integration.py
git commit -m "test: verify backtest engine works with Pyth candles"
```

---

## Task 8: Integration Test — Full Pipeline

**Files:**
- Create: `tests/test_pyth_pipeline_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_pyth_pipeline_integration.py
"""End-to-end test: Pyth candle download → store → backtest."""
import os
import tempfile
from unittest.mock import MagicMock, patch

from flint.models import Candle, SyncMetadata
from flint.store import FlintStore
from flint.migration import run_pyth_migration


def _make_store():
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)
    return FlintStore(path), path


def test_full_pipeline_pyth_download_and_backtest():
    """Download Pyth candles → store → run backtest → verify results."""
    store, path = _make_store()
    try:
        # Simulate PythCandleProvider response
        mock_candles = [
            Candle(1700000000 + i * 3600, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i,
                   1000.0, "SOL-PERP", 3600, "pyth")
            for i in range(10)
        ]

        # Store candles
        count = store.upsert_candles(mock_candles)
        assert count == 10

        # Query back
        candles = store.query_candles("SOL-PERP", 3600, venue="pyth")
        assert len(candles) == 10

        # Run backtest with these candles
        from flint.backtest.engine import BacktestEngine

        code = '''
class Strategy:
    def __init__(self):
        self.bar = 0
    def on_candle(self, ctx):
        self.bar += 1
        if self.bar == 1:
            ctx.market_order("SOL-PERP", "buy", 1.0)
        elif self.bar == 8:
            ctx.market_order("SOL-PERP", "sell", 1.0)
'''

        engine = BacktestEngine(
            candles=candles,
            strategy_code=code,
            initial_capital=10000.0,
            fee_rate=0.0006,
        )
        result = engine.run()
        assert result is not None
        assert result.total_trades == 2
        # Price went up from ~100 to ~107, so PnL should be positive
        assert result.total_pnl > 0
    finally:
        store.close()
        os.unlink(path)


def test_migration_then_backtest():
    """Migrate from Drift candles to Pyth, then run backtest on Pyth data."""
    store, path = _make_store()
    try:
        # Start with Drift candles
        drift_candles = [
            Candle(1700000000 + i * 3600, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i,
                   1000.0, "SOL-PERP", 3600, "drift")
            for i in range(5)
        ]
        store.upsert_candles(drift_candles)

        # Mock Pyth provider returns similar data
        pyth_candles = [
            Candle(1700000000 + i * 3600, 100.1 + i, 101.1 + i, 99.1 + i, 100.6 + i,
                   1000.0, "SOL-PERP", 3600, "pyth")
            for i in range(5)
        ]

        with patch("flint.migration.PythCandleProvider") as MockProvider:
            instance = MockProvider.return_value
            instance.fetch_candles.return_value = pyth_candles
            instance.close = MagicMock()

            result = run_pyth_migration(store)

        assert result["markets_migrated"] == ["SOL-PERP"]

        # Now query with fallback — should get Pyth candles
        candles = store.query_candles_with_fallback("SOL-PERP", 3600, 1700000000, 1700020000)
        assert len(candles) == 5
        assert candles[0].venue == "pyth"
    finally:
        store.close()
        os.unlink(path)
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/test_pyth_pipeline_integration.py -v`
Expected: 2 passed

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_pyth_pipeline_integration.py
git commit -m "test: add Pyth pipeline integration tests"
```

---

## Summary

| Task | Component | Est. Lines |
|------|-----------|-----------|
| 1 | PythCandleProvider | ~120 |
| 2 | Config fields | ~10 |
| 3 | Download API refactor | ~50 |
| 4 | CLI init switch | ~30 |
| 5 | Store migration helpers | ~40 |
| 6 | Auto-migration module | ~60 |
| 7 | Backtest engine verification | ~10 |
| 8 | Integration tests | ~80 |
| **Total** | | **~400** |

**Dependencies**: Task 1 (provider) must come first. Tasks 2-5 are independent. Task 6 depends on 1+5. Tasks 7-8 depend on all above.
