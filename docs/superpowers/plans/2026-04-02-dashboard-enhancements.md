# Dashboard Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add live monitoring, fill analysis, funding heatmap, MEV timeline, and strategy deployment to the Flint dashboard.

**Architecture:** 3 new React pages + 2 page extensions + new API routes for live data access. Follows existing patterns: hooks + polling, recharts, Tailwind terminal theme.

**Tech Stack:** React 19, TypeScript, recharts, Tailwind CSS, FastAPI.

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `flint/api/routes/live.py` | Live fills, equity, sessions API | Create |
| `flint/api/main.py` | Register live router | Modify |
| `ui/src/pages/LiveMonitor.tsx` | Live trading dashboard | Create |
| `ui/src/pages/FillAnalysis.tsx` | Per-trade execution analysis | Create |
| `ui/src/pages/FundingHeatmap.tsx` | Cross-venue funding grid | Create |
| `ui/src/pages/MevDashboard.tsx` | Add opportunity timeline | Modify |
| `ui/src/pages/PaperTrading.tsx` | Add strategy deployment panel | Modify |
| `ui/src/hooks/useLiveMonitor.ts` | Live data polling hook | Create |
| `ui/src/App.tsx` | Add 3 new routes + nav entries | Modify |
| `ROADMAP.md` | Mark §5.3 as implemented | Modify |
| `tests/test_live_api.py` | API endpoint tests | Create |

---

### Task 1: Live API Endpoints

**Files:**
- Create: `flint/api/routes/live.py`
- Modify: `flint/api/main.py`
- Create: `tests/test_live_api.py`

Create FastAPI routes for live data access. Follow the pattern in `flint/api/routes/data.py`.

**`flint/api/routes/live.py`:**
```python
"""Live trading data API endpoints."""
from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/v1/live", tags=["live"])


@router.get("/fills")
def get_live_fills(request: Request, session_id: str = "", venue: str = "", market: str = ""):
    """Query live fills with optional filters."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        return {"error": "Store not available"}
    if session_id:
        fills = store.get_live_fills(session_id, market=market or None)
    elif venue and market:
        fills = store.query_live_fills_by_venue(venue, market)
    else:
        fills = []
    return {"fills": fills}


@router.get("/equity")
def get_live_equity(request: Request, session_id: str = ""):
    """Query equity history for a session."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        return {"error": "Store not available"}
    if not session_id:
        return {"error": "session_id required"}
    try:
        rows = store._conn.execute(
            "SELECT ts, equity, cash, unrealized_pnl FROM live_equity_history "
            "WHERE session_id = ? ORDER BY ts ASC", [session_id]
        ).fetchall()
        return {"equity": [{"ts": r[0], "equity": r[1], "cash": r[2], "unrealized_pnl": r[3]} for r in rows]}
    except Exception as e:
        return {"error": str(e)}


@router.get("/sessions")
def get_live_sessions(request: Request):
    """List live trading sessions."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        return {"error": "Store not available"}
    try:
        rows = store._conn.execute(
            "SELECT session_id, strategy, market, venue, status, started_at, stopped_at "
            "FROM live_sessions ORDER BY started_at DESC LIMIT 20"
        ).fetchall()
        return {"sessions": [
            {"session_id": r[0], "strategy": r[1], "market": r[2], "venue": r[3],
             "status": r[4], "started_at": r[5], "stopped_at": r[6]}
            for r in rows
        ]}
    except Exception as e:
        return {"error": str(e)}
```

Register in `flint/api/main.py` — add `from .routes.live import router as live_router` and `app.include_router(live_router)`.

**Test (`tests/test_live_api.py`):**
```python
"""Tests for live API endpoints."""
import pytest
from flint.store import FlintStore


class TestLiveAPI:
    def test_fills_endpoint_returns_list(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        store.insert_live_fill(
            fill_id="f1", order_id="o1", session_id="s1",
            market="SOL-PERP", side="long", price=150.0, size=10.0,
            fee=0.075, tx_sig="tx1", venue="drift", is_partial=False, ts=1000,
        )
        fills = store.get_live_fills("s1")
        assert len(fills) == 1
        store.close()
```

Commit: `feat: add live API endpoints (fills, equity, sessions)`

---

### Task 2: LiveMonitor Page

**Files:**
- Create: `ui/src/pages/LiveMonitor.tsx`
- Create: `ui/src/hooks/useLiveMonitor.ts`

Create the live trading monitor page with metrics bar, equity curve, positions, and fill log.

The page:
- Fetches sessions from `GET /api/v1/live/sessions`, shows dropdown to select active session
- Polls `GET /api/v1/live/equity?session_id=` every 2s for equity curve
- Polls `GET /api/v1/live/fills?session_id=` every 2s for recent fills
- Uses `EquityCurve` component for the chart
- Uses `MetricsCard` pattern for top metrics bar
- Simple table for open positions and fill log

Follow the patterns in `PaperTrading.tsx` for polling and layout.

Commit: `feat: add LiveMonitor page with equity curve and fill log`

---

### Task 3: FillAnalysis Page

**Files:**
- Create: `ui/src/pages/FillAnalysis.tsx`

Fill analysis page with scatter plots and detailed fill table.

The page:
- Session/venue/market dropdowns as filters
- Scatter plot: impact_bps (Y) vs size (X) using recharts `ScatterChart`
- Scatter plot: latency_ms (Y) vs time (X)
- Table: all fills with columns: time, market, side, size, price, fee, impact_bps, latency_ms, tx_cost
- Fetches from `GET /api/v1/live/fills?session_id=&venue=&market=`

Commit: `feat: add FillAnalysis page with impact scatter plot`

---

### Task 4: FundingHeatmap Page

**Files:**
- Create: `ui/src/pages/FundingHeatmap.tsx`

Funding rate grid across venues and markets.

The page:
- Fetches from `GET /api/v1/data/funding` (existing endpoint, returns by venue)
- Builds a matrix: rows = markets, columns = venues
- Each cell shows the latest hourly funding rate
- Cell background color: green gradient for positive, red for negative
- Highlight cells where cross-venue spread > 5 bps

Simple HTML table with inline styles for cell colors. No charting library needed.

Commit: `feat: add FundingHeatmap page with cross-venue spread grid`

---

### Task 5: MevDashboard Timeline Extension

**Files:**
- Modify: `ui/src/pages/MevDashboard.tsx`

Add an "Opportunity Timeline" section below existing arb route content.

The addition:
- New section with recharts `ScatterChart`
- X = timestamp, Y = profit_bps, dot size = hop count
- Color: green for profitable routes
- Data from existing arb scan results (cached in component state)
- If no scan data exists, show "Run an arb scan to see timeline"

Commit: `feat: add MEV opportunity timeline to MevDashboard`

---

### Task 6: Strategy Deployment Panel

**Files:**
- Modify: `ui/src/pages/PaperTrading.tsx`

Add collapsible deployment panel at top of page.

The addition:
- Collapsible section: "Deploy Strategy" with toggle
- Strategy dropdown: fetches from `GET /api/v1/strategies` (existing)
- Market dropdown: fetches from `GET /api/v1/data/markets` (existing)
- Venue dropdown: hardcoded ["drift", "hyperliquid", "paper"]
- Capital input field
- Parameter form: auto-generated from strategy's parameter metadata
- Deploy button: calls `POST /api/v1/paper/start` with config
- Existing session list below (unchanged)

Commit: `feat: add strategy deployment panel to PaperTrading page`

---

### Task 7: Navigation + ROADMAP

**Files:**
- Modify: `ui/src/App.tsx`
- Modify: `ROADMAP.md`

Navigation changes:
- Add imports for LiveMonitor, FillAnalysis, FundingHeatmap
- Add nav items with keyboard shortcuts (7, 8, 9)
- Add Route entries

ROADMAP: Add "Implemented" section to §5.3.

Commit: `feat: add navigation for new dashboard pages + update ROADMAP §5.3`

---

## Task Dependencies

```
Task 1 (API) ──→ Task 2 (LiveMonitor) ──→ Task 7 (Nav + ROADMAP)
                  Task 3 (FillAnalysis) ──→ Task 7
Task 4 (FundingHeatmap) ──────────────→ Task 7
Task 5 (MevTimeline) ────────────────→ Task 7
Task 6 (StrategyDeploy) ─────────────→ Task 7
```

**Parallelizable:** Tasks 2-6 are independent (after Task 1 for API).
**Sequential:** Task 1 first (API needed by pages), Task 7 last.
