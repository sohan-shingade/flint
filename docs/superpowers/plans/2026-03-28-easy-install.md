# Easy Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Flint installable via a single command (Docker or curl|sh) with a browser-based first-run wizard that handles market selection and data download.

**Architecture:** New `/api/v1/system/status` endpoint detects empty DB. React setup wizard at `/setup` guides market selection and triggers downloads. Docker entrypoint and shell install script both just start the server — the wizard does the rest.

**Tech Stack:** FastAPI (system routes), React 19 + React Router (wizard UI), Docker (entrypoint.sh), Bash (install.sh), Make (dev targets)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `flint/api/routes/system.py` | Create | `/api/v1/system/status` and `/api/v1/system/config` endpoints |
| `flint/api/main.py` | Modify | Register system router (line ~121) |
| `tests/test_system_routes.py` | Create | Tests for system endpoints |
| `ui/src/pages/Setup.tsx` | Create | 5-step setup wizard component |
| `ui/src/App.tsx` | Modify | Add `/setup` route, add init redirect logic |
| `docker-entrypoint.sh` | Create | Smart entrypoint for container |
| `Dockerfile` | Modify | Use entrypoint, add healthcheck, add curl |
| `docker-compose.yml` | Modify | Named volumes, env_file, healthcheck |
| `install.sh` | Create | Full-auto shell installer |
| `Makefile` | Create | Dev convenience targets |

---

### Task 1: System Status API Endpoint

**Files:**
- Create: `flint/api/routes/system.py`
- Modify: `flint/api/main.py:14,113-121`
- Create: `tests/test_system_routes.py`

- [ ] **Step 1: Write failing test for GET /api/v1/system/status**

```python
# tests/test_system_routes.py
"""Tests for system status and config endpoints."""
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


def _make_app(candle_count: int = 0):
    """Create a test app with a mocked store."""
    mock_store = MagicMock()
    mock_store._conn = MagicMock()
    mock_store._lock = MagicMock()
    mock_store._conn.execute.return_value.fetchone.return_value = (candle_count,)

    with patch("flint.api.main.load_config") as mock_cfg, \
         patch("flint.api.main.FlintStore", return_value=mock_store), \
         patch("flint.api.main.CollectorService"), \
         patch("flint.api.main.PaperTradingEngine") as mock_pe, \
         patch("flint.api.main.PriceTicker") as mock_pt:
        mock_cfg.return_value = MagicMock(
            db_path=":memory:",
            collector_enabled=False,
            max_concurrent_backtests=1,
            default_markets=["SOL-PERP"],
            cors_origins=["http://localhost:5173"],
        )
        mock_pe.return_value = MagicMock()
        mock_pt.return_value = MagicMock()
        from flint.api.main import app
        client = TestClient(app)
    return client, mock_store


def test_system_status_uninitialized():
    client, mock_store = _make_app(candle_count=0)
    resp = client.get("/api/v1/system/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["initialized"] is False
    assert "version" in body


def test_system_status_initialized():
    client, mock_store = _make_app(candle_count=500)
    resp = client.get("/api/v1/system/status")
    assert resp.status_code == 200
    assert resp.json()["initialized"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_system_routes.py -v`
Expected: FAIL — no module `flint.api.routes.system` or no route registered

- [ ] **Step 3: Create system routes module**

```python
# flint/api/routes/system.py
"""System status and configuration endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class SystemStatus(BaseModel):
    initialized: bool
    version: str


@router.get("/status", response_model=SystemStatus)
def system_status(request: Request):
    """Check if Flint has been initialized (has candle data)."""
    store = getattr(request.app.state, "store", None)
    has_data = False
    if store is not None:
        with store._lock:
            row = store._conn.execute("SELECT COUNT(*) FROM candles").fetchone()
            has_data = row[0] > 0 if row else False
    return SystemStatus(initialized=has_data, version="0.3.0")
```

- [ ] **Step 4: Register the router in main.py**

In `flint/api/main.py`, add the import at line 14:

```python
from .routes import backtest, strategies, data, mev, user_strategies, collector, paper, optimization, journal, system
```

Add the router registration after line 121:

```python
app.include_router(system.router, prefix="/api/v1/system", tags=["system"])
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_system_routes.py -v`
Expected: PASS

- [ ] **Step 6: Write failing test for POST /api/v1/system/config**

Add to `tests/test_system_routes.py`:

```python
import os
import tempfile


def test_system_config_saves_api_keys(tmp_path):
    """POST /api/v1/system/config writes keys to .env file."""
    env_file = tmp_path / ".env"
    client, _ = _make_app()

    with patch("flint.api.routes.system._get_env_path", return_value=str(env_file)):
        resp = client.post("/api/v1/system/config", json={
            "birdeye_api_key": "test-birdeye-key",
            "helius_api_key": "test-helius-key",
        })

    assert resp.status_code == 200
    assert resp.json()["saved"] is True

    content = env_file.read_text()
    assert "FLINT_BIRDEYE_API_KEY=test-birdeye-key" in content
    assert "FLINT_HELIUS_API_KEY=test-helius-key" in content


def test_system_config_preserves_existing_keys(tmp_path):
    """POST /api/v1/system/config does not overwrite unrelated keys."""
    env_file = tmp_path / ".env"
    env_file.write_text("SOLANA_RPC_URL=https://example.com\nFLINT_BIRDEYE_API_KEY=old-key\n")
    client, _ = _make_app()

    with patch("flint.api.routes.system._get_env_path", return_value=str(env_file)):
        resp = client.post("/api/v1/system/config", json={
            "birdeye_api_key": "new-key",
        })

    assert resp.status_code == 200
    content = env_file.read_text()
    assert "FLINT_BIRDEYE_API_KEY=new-key" in content
    assert "SOLANA_RPC_URL=https://example.com" in content


def test_system_config_skips_empty_values(tmp_path):
    """Empty string values are not written to .env."""
    env_file = tmp_path / ".env"
    client, _ = _make_app()

    with patch("flint.api.routes.system._get_env_path", return_value=str(env_file)):
        resp = client.post("/api/v1/system/config", json={
            "birdeye_api_key": "",
            "helius_api_key": "real-key",
        })

    assert resp.status_code == 200
    content = env_file.read_text()
    assert "FLINT_BIRDEYE_API_KEY" not in content
    assert "FLINT_HELIUS_API_KEY=real-key" in content
```

- [ ] **Step 7: Run test to verify it fails**

Run: `pytest tests/test_system_routes.py::test_system_config_saves_api_keys -v`
Expected: FAIL

- [ ] **Step 8: Implement POST /api/v1/system/config**

Add to `flint/api/routes/system.py`:

```python
from pathlib import Path
from typing import Optional


def _get_env_path() -> str:
    """Return path to .env file."""
    return str(Path(".env"))


class ConfigUpdate(BaseModel):
    birdeye_api_key: Optional[str] = None
    helius_api_key: Optional[str] = None


class ConfigResponse(BaseModel):
    saved: bool


# Mapping from request field names to .env variable names
_KEY_MAP = {
    "birdeye_api_key": "FLINT_BIRDEYE_API_KEY",
    "helius_api_key": "FLINT_HELIUS_API_KEY",
}


@router.post("/config", response_model=ConfigResponse)
def update_config(body: ConfigUpdate):
    """Save optional API keys to .env file."""
    env_path = Path(_get_env_path())

    # Read existing .env lines
    existing: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                existing[key.strip()] = value.strip()

    # Update only non-empty values
    updates = body.model_dump(exclude_none=True)
    for field_name, env_var in _KEY_MAP.items():
        if field_name in updates:
            value = updates[field_name]
            if value:  # skip empty strings
                existing[env_var] = value

    # Write back
    lines = [f"{k}={v}" for k, v in existing.items()]
    env_path.write_text("\n".join(lines) + "\n" if lines else "")

    return ConfigResponse(saved=True)
```

- [ ] **Step 9: Run all system tests**

Run: `pytest tests/test_system_routes.py -v`
Expected: All PASS

- [ ] **Step 10: Commit**

```bash
git add flint/api/routes/system.py flint/api/main.py tests/test_system_routes.py
git commit -m "feat: add /api/v1/system/status and /config endpoints"
```

---

### Task 2: Setup Wizard — React Page

**Files:**
- Create: `ui/src/pages/Setup.tsx`
- Modify: `ui/src/App.tsx`

- [ ] **Step 1: Create the Setup wizard page**

```tsx
// ui/src/pages/Setup.tsx
import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

type Step = 'welcome' | 'markets' | 'keys' | 'downloading' | 'done'

interface Market {
  market: string
  source: string
}

interface DownloadProgress {
  market: string
  status: 'pending' | 'downloading' | 'done' | 'failed'
  detail: string
}

const BACKFILL_OPTIONS = [
  { label: '30 days', value: 30 },
  { label: '60 days', value: 60 },
  { label: '90 days', value: 90 },
  { label: '180 days', value: 180 },
]

const DEFAULT_MARKETS = ['SOL-PERP', 'BTC-PERP', 'ETH-PERP']

export default function Setup() {
  const navigate = useNavigate()
  const [step, setStep] = useState<Step>('welcome')
  const [availableMarkets, setAvailableMarkets] = useState<Market[]>([])
  const [selectedMarkets, setSelectedMarkets] = useState<string[]>(DEFAULT_MARKETS)
  const [backfillDays, setBackfillDays] = useState(90)
  const [birdeyeKey, setBirdeyeKey] = useState('')
  const [heliusKey, setHeliusKey] = useState('')
  const [showKeys, setShowKeys] = useState(false)
  const [downloadProgress, setDownloadProgress] = useState<DownloadProgress[]>([])
  const [loadingMarkets, setLoadingMarkets] = useState(false)

  // Fetch available markets when entering market selection step
  useEffect(() => {
    if (step === 'markets' && availableMarkets.length === 0) {
      setLoadingMarkets(true)
      fetch('/api/v1/data/available-markets')
        .then(r => r.json())
        .then(data => {
          const markets = (data.markets || []).map((m: any) => ({
            market: m.market || m,
            source: m.source || 'drift',
          }))
          setAvailableMarkets(markets)
        })
        .catch(() => {})
        .finally(() => setLoadingMarkets(false))
    }
  }, [step, availableMarkets.length])

  const toggleMarket = (market: string) => {
    setSelectedMarkets(prev =>
      prev.includes(market) ? prev.filter(m => m !== market) : [...prev, market]
    )
  }

  const saveKeys = async () => {
    if (birdeyeKey || heliusKey) {
      await fetch('/api/v1/system/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          birdeye_api_key: birdeyeKey || undefined,
          helius_api_key: heliusKey || undefined,
        }),
      }).catch(() => {})
    }
  }

  const startDownload = async () => {
    setStep('downloading')

    const progress: DownloadProgress[] = selectedMarkets.map(m => ({
      market: m, status: 'pending', detail: '',
    }))
    setDownloadProgress([...progress])

    const now = Math.floor(Date.now() / 1000)
    const startTs = now - backfillDays * 86400

    for (let i = 0; i < selectedMarkets.length; i++) {
      progress[i].status = 'downloading'
      setDownloadProgress([...progress])

      try {
        const res = await fetch('/api/v1/data/download', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            market: selectedMarkets[i],
            resolution_s: 3600,
            start_ts: startTs,
            end_ts: now,
            funding_venues: [],
          }),
        })
        const data = await res.json()
        const total = (data.downloaded || 0) + (data.existing || 0)
        progress[i].status = 'done'
        progress[i].detail = total > 0 ? `${total.toLocaleString()} candles` : 'no data'
      } catch {
        progress[i].status = 'failed'
        progress[i].detail = 'download failed'
      }
      setDownloadProgress([...progress])
    }

    setStep('done')
  }

  const handleKeysNext = async () => {
    await saveKeys()
    startDownload()
  }

  // ── Render ──────────────────────────────────────────────────

  if (step === 'welcome') {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-8">
        <div className="text-center">
          <h1 className="text-amber text-2xl font-bold tracking-[0.2em] mb-3">FLINT</h1>
          <p className="text-ghost text-sm tracking-wider">
            Algorithmic trading, backtesting & MEV research for Solana
          </p>
        </div>
        <button
          onClick={() => setStep('markets')}
          className="px-8 py-3 border border-amber text-amber text-xs tracking-[0.2em] hover:bg-amber-glow transition-colors"
        >
          GET STARTED
        </button>
        <p className="text-ghost/50 text-[10px] tracking-wider">
          This will download market data so you can start backtesting
        </p>
      </div>
    )
  }

  if (step === 'markets') {
    const perpMarkets = availableMarkets.filter(m => m.market.endsWith('-PERP'))
    const spotMarkets = availableMarkets.filter(m => !m.market.endsWith('-PERP'))

    return (
      <div className="max-w-2xl mx-auto">
        <h2 className="text-amber text-sm tracking-[0.2em] mb-1">SELECT MARKETS</h2>
        <p className="text-ghost text-xs mb-6">Choose which markets to download. You can add more later from the Data page.</p>

        {/* Backfill period */}
        <div className="mb-6">
          <label className="text-ghost text-[10px] tracking-wider block mb-2">BACKFILL PERIOD</label>
          <div className="flex gap-2">
            {BACKFILL_OPTIONS.map(opt => (
              <button
                key={opt.value}
                onClick={() => setBackfillDays(opt.value)}
                className={`px-3 py-1.5 text-[11px] border transition-colors ${
                  backfillDays === opt.value
                    ? 'border-amber text-amber bg-amber-glow'
                    : 'border-border text-ghost hover:border-border-bright'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {loadingMarkets ? (
          <p className="text-ghost text-xs animate-pulse">Loading available markets...</p>
        ) : (
          <>
            {/* Perpetuals */}
            {perpMarkets.length > 0 && (
              <div className="mb-4">
                <h3 className="text-ghost text-[10px] tracking-wider mb-2">PERPETUALS</h3>
                <div className="grid grid-cols-3 sm:grid-cols-4 gap-1.5">
                  {perpMarkets.map(m => (
                    <button
                      key={m.market}
                      onClick={() => toggleMarket(m.market)}
                      className={`px-2 py-1.5 text-[11px] border text-left transition-colors ${
                        selectedMarkets.includes(m.market)
                          ? 'border-amber text-amber bg-amber-glow'
                          : 'border-border text-ghost hover:border-border-bright'
                      }`}
                    >
                      {m.market}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Spot */}
            {spotMarkets.length > 0 && (
              <div className="mb-4">
                <h3 className="text-ghost text-[10px] tracking-wider mb-2">SPOT</h3>
                <div className="grid grid-cols-3 sm:grid-cols-4 gap-1.5">
                  {spotMarkets.map(m => (
                    <button
                      key={m.market}
                      onClick={() => toggleMarket(m.market)}
                      className={`px-2 py-1.5 text-[11px] border text-left transition-colors ${
                        selectedMarkets.includes(m.market)
                          ? 'border-amber text-amber bg-amber-glow'
                          : 'border-border text-ghost hover:border-border-bright'
                      }`}
                    >
                      {m.market}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        <div className="flex items-center justify-between mt-8 pt-4 border-t border-border">
          <span className="text-ghost text-[10px]">{selectedMarkets.length} market{selectedMarkets.length !== 1 ? 's' : ''} selected</span>
          <button
            onClick={() => setStep('keys')}
            disabled={selectedMarkets.length === 0}
            className="px-6 py-2 border border-amber text-amber text-xs tracking-[0.15em] hover:bg-amber-glow transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          >
            NEXT
          </button>
        </div>
      </div>
    )
  }

  if (step === 'keys') {
    return (
      <div className="max-w-lg mx-auto">
        <h2 className="text-amber text-sm tracking-[0.2em] mb-1">API KEYS</h2>
        <p className="text-ghost text-xs mb-6">Optional — most data sources work without keys. You can add these later.</p>

        <button
          onClick={() => setShowKeys(!showKeys)}
          className="text-ghost text-[11px] hover:text-terminal transition-colors mb-4"
        >
          {showKeys ? '▾' : '▸'} Configure API keys
        </button>

        {showKeys && (
          <div className="space-y-4 mb-6 p-4 border border-border">
            <div>
              <label className="text-ghost text-[10px] tracking-wider block mb-1">BIRDEYE API KEY</label>
              <input
                type="text"
                value={birdeyeKey}
                onChange={e => setBirdeyeKey(e.target.value)}
                placeholder="For any Solana token OHLCV data"
                className="w-full bg-void border border-border px-3 py-2 text-xs text-terminal placeholder:text-ghost/40 focus:border-amber focus:outline-none"
              />
              <p className="text-ghost/50 text-[10px] mt-1">Free at birdeye.so/developers</p>
            </div>
            <div>
              <label className="text-ghost text-[10px] tracking-wider block mb-1">HELIUS API KEY</label>
              <input
                type="text"
                value={heliusKey}
                onChange={e => setHeliusKey(e.target.value)}
                placeholder="For liquidations & whale tracking"
                className="w-full bg-void border border-border px-3 py-2 text-xs text-terminal placeholder:text-ghost/40 focus:border-amber focus:outline-none"
              />
              <p className="text-ghost/50 text-[10px] mt-1">Free at helius.dev</p>
            </div>
          </div>
        )}

        <div className="flex items-center justify-between mt-8 pt-4 border-t border-border">
          <button
            onClick={() => setStep('markets')}
            className="text-ghost text-xs hover:text-terminal transition-colors"
          >
            BACK
          </button>
          <button
            onClick={handleKeysNext}
            className="px-6 py-2 border border-amber text-amber text-xs tracking-[0.15em] hover:bg-amber-glow transition-colors"
          >
            {selectedMarkets.length > 0 ? 'DOWNLOAD DATA' : 'SKIP'}
          </button>
        </div>
      </div>
    )
  }

  if (step === 'downloading') {
    const completed = downloadProgress.filter(p => p.status === 'done' || p.status === 'failed').length
    return (
      <div className="max-w-lg mx-auto">
        <h2 className="text-amber text-sm tracking-[0.2em] mb-1">DOWNLOADING</h2>
        <p className="text-ghost text-xs mb-6">
          Fetching {backfillDays} days of data for {selectedMarkets.length} market{selectedMarkets.length !== 1 ? 's' : ''}...
        </p>

        <div className="space-y-2">
          {downloadProgress.map(p => (
            <div key={p.market} className="flex items-center justify-between py-1.5 px-3 border border-border text-xs">
              <span className="text-terminal">{p.market}</span>
              <span className={
                p.status === 'done' ? 'text-gain' :
                p.status === 'failed' ? 'text-loss' :
                p.status === 'downloading' ? 'text-amber animate-pulse' :
                'text-ghost/40'
              }>
                {p.status === 'pending' ? 'waiting' :
                 p.status === 'downloading' ? 'downloading...' :
                 p.detail}
              </span>
            </div>
          ))}
        </div>

        <div className="mt-4">
          <div className="h-px bg-border relative">
            <div
              className="h-px bg-amber transition-all duration-500"
              style={{ width: `${selectedMarkets.length > 0 ? (completed / selectedMarkets.length) * 100 : 0}%` }}
            />
          </div>
          <p className="text-ghost/50 text-[10px] mt-2 text-right">{completed}/{selectedMarkets.length}</p>
        </div>
      </div>
    )
  }

  // step === 'done'
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-6">
      <div className="text-center">
        <h2 className="text-gain text-sm tracking-[0.2em] mb-2">SETUP COMPLETE</h2>
        <p className="text-ghost text-xs">
          {downloadProgress.filter(p => p.status === 'done').length} market{downloadProgress.filter(p => p.status === 'done').length !== 1 ? 's' : ''} ready for backtesting
        </p>
      </div>

      <div className="flex gap-3">
        <button
          onClick={() => navigate('/backtest')}
          className="px-6 py-2 border border-amber text-amber text-xs tracking-[0.15em] hover:bg-amber-glow transition-colors"
        >
          RUN A BACKTEST
        </button>
        <button
          onClick={() => navigate('/')}
          className="px-6 py-2 border border-border text-ghost text-xs tracking-[0.15em] hover:border-border-bright transition-colors"
        >
          GO TO DASHBOARD
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Add /setup route and init redirect to App.tsx**

In `ui/src/App.tsx`, add the import at the top:

```tsx
import Setup from './pages/Setup'
```

Add state and effect for init check inside the `App` component, after the clock effect (after line 32):

```tsx
  const [initChecked, setInitChecked] = useState(false)
  const [isInitialized, setIsInitialized] = useState(true) // assume true to avoid flash

  useEffect(() => {
    fetch('/api/v1/system/status')
      .then(r => r.json())
      .then(data => {
        setIsInitialized(data.initialized)
        setInitChecked(true)
        if (!data.initialized && window.location.pathname !== '/setup') {
          navigate('/setup')
        }
      })
      .catch(() => setInitChecked(true)) // on error, let user through
  }, [navigate])
```

Add the route inside `<Routes>` (after line 95):

```tsx
          <Route path="/setup" element={<Setup />} />
```

- [ ] **Step 3: Verify the UI builds**

Run: `cd ui && npm run build`
Expected: Build succeeds with no type errors

- [ ] **Step 4: Commit**

```bash
git add ui/src/pages/Setup.tsx ui/src/App.tsx
git commit -m "feat: add browser setup wizard for first-run experience"
```

---

### Task 3: Docker Entrypoint and Polish

**Files:**
- Create: `docker-entrypoint.sh`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Create the entrypoint script**

```bash
#!/bin/sh
set -e

# Create directories if missing
mkdir -p /app/data /app/strategies/user

# Generate default config if missing
if [ ! -f /app/flint.yaml ]; then
    python3 -c "
from flint.config import FlintConfig
import yaml
cfg = FlintConfig()
data = {
    'db': {'path': '/app/data/flint.duckdb'},
    'trading': {
        'default_markets': list(cfg.default_markets),
        'default_fee_rate': cfg.default_fee_rate,
        'default_capital': cfg.default_capital,
    },
    'collector': {'enabled': True},
}
print(yaml.dump(data, default_flow_style=False))
" > /app/flint.yaml
    echo "Generated default flint.yaml"
fi

exec uvicorn flint.api.main:app --host 0.0.0.0 --port 8000
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x docker-entrypoint.sh`

- [ ] **Step 3: Update the Dockerfile**

Replace the full contents of `Dockerfile`:

```dockerfile
# Flint — Solana Algo Trading Platform
# Multi-stage build: Python backend + Node.js frontend

FROM python:3.11-slim AS backend
WORKDIR /app
COPY pyproject.toml .
COPY flint/ flint/
RUN pip install --no-cache-dir -e .

FROM node:20-slim AS frontend
WORKDIR /app/ui
COPY ui/package.json ui/package-lock.json* ./
RUN npm ci --silent
COPY ui/ .
RUN npm run build

FROM python:3.11-slim
WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Install backend
COPY pyproject.toml .
COPY flint/ flint/
COPY scripts/ scripts/
RUN pip install --no-cache-dir -e .

# Copy built frontend
COPY --from=frontend /app/ui/dist /app/ui/dist

# Copy entrypoint
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Create data directory
RUN mkdir -p data strategies/user

EXPOSE 8000
ENV FLINT_DB_PATH=/app/data/flint.duckdb
ENV FLINT_COLLECTOR_ENABLED=true

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
```

- [ ] **Step 4: Update docker-compose.yml**

Replace the full contents of `docker-compose.yml`:

```yaml
services:
  flint:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - flint-data:/app/data
      - ./strategies:/app/strategies
    env_file:
      - path: .env
        required: false
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped

volumes:
  flint-data:
```

- [ ] **Step 5: Verify Docker builds**

Run: `docker build -t flint-test .`
Expected: Build completes successfully (multi-stage, all 3 stages pass)

- [ ] **Step 6: Commit**

```bash
git add docker-entrypoint.sh Dockerfile docker-compose.yml
git commit -m "feat: add Docker entrypoint with auto-config and healthcheck"
```

---

### Task 4: Shell Install Script

**Files:**
- Create: `install.sh`

- [ ] **Step 1: Create the install script**

```bash
#!/usr/bin/env bash
# Flint installer — single command to install and run Flint
# Usage: curl -fsSL https://raw.githubusercontent.com/<owner>/flint/main/install.sh | bash
set -euo pipefail

FLINT_HOME="${FLINT_HOME:-$HOME/.flint}"
REPO_URL="${FLINT_REPO_URL:-https://github.com/sohan/flint.git}"  # set FLINT_REPO_URL to override
MIN_PYTHON="3.10"
MIN_NODE="18"

# ── Helpers ──────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
AMBER='\033[0;33m'
DIM='\033[2m'
RESET='\033[0m'

step=0
total_steps=7

progress() {
    step=$((step + 1))
    printf "${AMBER}[%d/%d]${RESET} %s\n" "$step" "$total_steps" "$1"
}

success() {
    printf "  ${GREEN}✓${RESET} %s\n" "$1"
}

fail() {
    printf "  ${RED}✗${RESET} %s\n" "$1"
    exit 1
}

info() {
    printf "  ${DIM}%s${RESET}\n" "$1"
}

version_gte() {
    # Returns 0 if $1 >= $2 (semantic version comparison)
    printf '%s\n%s\n' "$2" "$1" | sort -V -C
}

# ── Step 1: Detect OS ────────────────────────────────────────

progress "Detecting OS..."

OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
    Darwin) OS_TYPE="macos" ;;
    Linux)  OS_TYPE="linux" ;;
    *)      fail "Unsupported OS: $OS. Flint supports macOS and Linux." ;;
esac

if [ "$OS_TYPE" = "linux" ]; then
    if command -v apt-get >/dev/null 2>&1; then
        PKG_MGR="apt"
    elif command -v dnf >/dev/null 2>&1; then
        PKG_MGR="dnf"
    elif command -v pacman >/dev/null 2>&1; then
        PKG_MGR="pacman"
    else
        fail "No supported package manager found (apt, dnf, pacman)"
    fi
fi

success "$OS_TYPE $ARCH"

# ── Step 2: Check/install Python ─────────────────────────────

progress "Checking Python ${MIN_PYTHON}+..."

install_python() {
    info "Installing Python..."
    if [ "$OS_TYPE" = "macos" ]; then
        if ! command -v brew >/dev/null 2>&1; then
            info "Installing Homebrew first..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null)"
        fi
        brew install python@3.12
    elif [ "$PKG_MGR" = "apt" ]; then
        sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-venv python3-pip
    elif [ "$PKG_MGR" = "dnf" ]; then
        sudo dnf install -y python3
    elif [ "$PKG_MGR" = "pacman" ]; then
        sudo pacman -Sy --noconfirm python
    fi
}

PYTHON_CMD=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ver="$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')"
        if version_gte "$ver" "$MIN_PYTHON"; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    install_python
    for cmd in python3.12 python3.11 python3.10 python3; do
        if command -v "$cmd" >/dev/null 2>&1; then
            PYTHON_CMD="$cmd"
            break
        fi
    done
fi

[ -z "$PYTHON_CMD" ] && fail "Could not find or install Python ${MIN_PYTHON}+"
PY_VER="$("$PYTHON_CMD" --version 2>&1)"
success "Found $PY_VER"

# ── Step 3: Check/install Node ───────────────────────────────

progress "Checking Node ${MIN_NODE}+..."

install_node() {
    info "Installing Node.js..."
    if [ "$OS_TYPE" = "macos" ]; then
        if ! command -v brew >/dev/null 2>&1; then
            info "Installing Homebrew first..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null)"
        fi
        brew install node
    elif [ "$PKG_MGR" = "apt" ]; then
        curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
        sudo apt-get install -y -qq nodejs
    elif [ "$PKG_MGR" = "dnf" ]; then
        curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo bash -
        sudo dnf install -y nodejs
    elif [ "$PKG_MGR" = "pacman" ]; then
        sudo pacman -Sy --noconfirm nodejs npm
    fi
}

if command -v node >/dev/null 2>&1; then
    NODE_VER="$(node --version | grep -oE '[0-9]+' | head -1)"
    if [ "$NODE_VER" -ge "$MIN_NODE" ]; then
        success "Found Node $(node --version)"
    else
        install_node
        success "Installed Node $(node --version)"
    fi
else
    install_node
    command -v node >/dev/null 2>&1 || fail "Could not install Node.js"
    success "Installed Node $(node --version)"
fi

# ── Step 4: Clone/update repo ────────────────────────────────

progress "Setting up Flint at ${FLINT_HOME}..."

if [ -d "$FLINT_HOME/.git" ]; then
    info "Updating existing installation..."
    git -C "$FLINT_HOME" pull --ff-only 2>/dev/null || git -C "$FLINT_HOME" pull
    success "Updated"
else
    git clone "$REPO_URL" "$FLINT_HOME"
    success "Cloned"
fi

# ── Step 5: Create venv and install ──────────────────────────

progress "Installing Python dependencies..."

VENV_DIR="$FLINT_HOME/.venv"

if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_CMD" -m venv "$VENV_DIR"
fi

# Activate and install
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -e "$FLINT_HOME" --quiet
success "Installed flint package"

# ── Step 6: Build UI ─────────────────────────────────────────

progress "Building UI..."

cd "$FLINT_HOME/ui"
if [ ! -d "node_modules" ]; then
    npm install --silent 2>/dev/null
fi
npm run build --silent 2>/dev/null
cd "$FLINT_HOME"
success "UI built"

# ── Step 7: Start server ─────────────────────────────────────

progress "Starting Flint..."

# Kill any existing flint server on port 8000
lsof -ti:8000 2>/dev/null | xargs kill 2>/dev/null || true

# Start in background
"$VENV_DIR/bin/flint" serve > "$FLINT_HOME/flint.log" 2>&1 &
FLINT_PID=$!

# Wait for server to be ready
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/api/v1/health >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

if curl -sf http://localhost:8000/api/v1/health >/dev/null 2>&1; then
    success "Running at http://localhost:8000 (PID: $FLINT_PID)"
else
    fail "Server failed to start. Check $FLINT_HOME/flint.log"
fi

# Open browser
if [ "$OS_TYPE" = "macos" ]; then
    open "http://localhost:8000"
elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://localhost:8000"
fi

printf "\n${GREEN}Flint is ready!${RESET}\n"
printf "  ${DIM}Dashboard:${RESET}  http://localhost:8000\n"
printf "  ${DIM}Logs:${RESET}       $FLINT_HOME/flint.log\n"
printf "  ${DIM}Stop:${RESET}       kill $FLINT_PID\n"
printf "  ${DIM}Restart:${RESET}    $VENV_DIR/bin/flint serve\n"
printf "  ${DIM}Uninstall:${RESET}  rm -rf $FLINT_HOME\n"
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x install.sh`

- [ ] **Step 3: Test the script parses correctly**

Run: `bash -n install.sh`
Expected: No output (no syntax errors)

- [ ] **Step 4: Commit**

```bash
git add install.sh
git commit -m "feat: add single-command install script (curl | sh)"
```

---

### Task 5: Makefile

**Files:**
- Create: `Makefile`

- [ ] **Step 1: Create the Makefile**

```makefile
.PHONY: install dev serve test build clean

install: ## Install Python + UI dependencies
	pip install -e ".[dev]"
	cd ui && npm install

dev: ## Start API (hot reload) + UI dev server
	flint serve --dev &
	cd ui && npm run dev

serve: ## Build UI and start production server
	cd ui && npm run build
	flint serve

test: ## Run all tests
	pytest tests/ -v

build: ## Build Docker image
	docker build -t flint .

clean: ## Remove generated files
	rm -rf data/ ui/dist/ ui/node_modules/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[33m%-12s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
```

- [ ] **Step 2: Verify make help works**

Run: `make help`
Expected: Prints colored list of targets with descriptions

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "feat: add Makefile with dev convenience targets"
```

---

### Task 6: Integration Test and Final Verification

**Files:**
- No new files

- [ ] **Step 1: Run the full test suite to ensure nothing is broken**

Run: `pytest tests/ -v --tb=short`
Expected: All existing tests pass, new system route tests pass

- [ ] **Step 2: Verify UI builds with the new Setup page**

Run: `cd ui && npm run build`
Expected: Build succeeds, `ui/dist/index.html` exists

- [ ] **Step 3: Verify Docker builds end to end**

Run: `docker build -t flint-test .`
Expected: All 3 stages complete successfully

- [ ] **Step 4: Verify install script has no syntax errors**

Run: `bash -n install.sh && shellcheck install.sh 2>/dev/null || echo "shellcheck not installed, skipping"`
Expected: No bash syntax errors

- [ ] **Step 5: Final commit if any fixups were needed**

```bash
git add -A
git commit -m "fix: integration fixes from end-to-end verification"
```

Only commit if there are staged changes. Skip if clean.
