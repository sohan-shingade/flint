# Web Dashboard Enhancements — Design Spec

> Sub-project 5.3 of Phase 5 (ROADMAP.md §5.3)
> Date: 2026-04-02

## Overview

Add 5 new views to the React dashboard for live trading monitoring, fill analysis, funding spreads, MEV opportunities, and strategy deployment. Follows existing UI patterns: React 19, Vite, Tailwind, terminal/hacker aesthetic, hooks + polling.

### Scope

**In scope:**
- 3 new pages: LiveMonitor, FillAnalysis, FundingHeatmap
- 1 page extension: MevDashboard (add timeline)
- 1 page extension: PaperTrading (add deployment panel)
- 2 new API endpoints for data access
- 2 new hooks for data polling
- Navigation updates (3 new routes)

**Out of scope:**
- Mobile responsive design (desktop-first)
- WebSocket-based real-time updates (polling is sufficient)
- Dark/light theme toggle (dark only)

---

## 1. Live Trading Monitor

**New file:** `ui/src/pages/LiveMonitor.tsx`

Single-page dashboard layout with 4 sections:

### Layout
```
┌──────────┬──────────┬──────────┬──────────┐
│ EQUITY   │ PNL      │ POSITIONS│ DRAWDOWN │  ← Metrics bar (MetricsCard pattern)
│ $12,450  │ +$450    │ 3        │ -2.1%    │
├──────────┴──────┬───┴──────────┴──────────┤
│ EQUITY CURVE    │ OPEN POSITIONS           │  ← 2:1 split
│ (EquityCurve)   │ SOL-PERP LONG 10 @150   │
│                 │ BTC-PERP SHORT 0.1 @65k  │
├─────────────────┴──────────────────────────┤
│ RECENT FILLS                               │  ← Fill log (TradeTable pattern)
│ 12:04:32 BUY SOL-PERP 10.0 @ 150.20       │
└────────────────────────────────────────────┘
```

### Data Source
- Polls `GET /api/v1/paper/portfolio` every 2s (existing endpoint)
- Polls `GET /api/v1/live/equity?session_id=` for equity history (new endpoint)
- Polls `GET /api/v1/live/fills?session_id=` for recent fills (new endpoint)

### Components Used
- `MetricsCard` — existing, for top metrics bar
- `EquityCurve` — existing, polled with live data
- Position list — simple table, similar to PaperTrading positions
- Fill log — similar to `TradeTable` but with timestamp, side, size, price, fee

### New Hook
```typescript
// ui/src/hooks/useLiveMonitor.ts
export function useLiveMonitor(sessionId: string, pollInterval = 2000) {
  // Polls equity history and recent fills
  // Returns { equity, positions, fills, error }
}
```

---

## 2. Fill Analysis View

**New file:** `ui/src/pages/FillAnalysis.tsx`

Per-trade execution quality analysis.

### Layout
```
┌────────────────────────────────────────────┐
│ Session: [dropdown] │ Venue: [dropdown]     │  ← Filters
├──────────────────────┬─────────────────────┤
│ IMPACT vs SIZE       │ LATENCY vs TIME     │  ← Scatter plots (recharts)
│ (scatter plot)       │ (scatter plot)       │
├──────────────────────┴─────────────────────┤
│ FILL TABLE                                  │
│ Time │ Market │ Side │ Size │ Price │ Fee   │
│      │        │      │      │ Impact│ Latency│
└────────────────────────────────────────────┘
```

### Data Source
- `GET /api/v1/live/fills?venue=&market=&session_id=` (new endpoint)
- Returns fill records with impact_bps, latency_ms, tx_cost fields

### New API Endpoint
```python
# flint/api/routes/live.py (new file)
@router.get("/fills")
def get_live_fills(request: Request, session_id: str = "", venue: str = "", market: str = ""):
    """Query live fills with optional filters."""
    store = getattr(request.app.state, "store", None)
    # Use store.get_live_fills() or store.query_live_fills_by_venue()
```

---

## 3. Funding Spread Heatmap

**New file:** `ui/src/pages/FundingHeatmap.tsx`

Grid visualization of funding rates across venues and markets.

### Layout
```
┌────────────────────────────────────────────┐
│ FUNDING SPREAD HEATMAP                      │
│                                              │
│        Drift   Hyperl.  Binance  OKX  Bybit │
│ SOL    +3.2    +1.1     +2.5    +2.0  +2.2  │  ← Green/red color by rate
│ BTC    +1.0    +0.5     +0.8    +0.7  +0.9  │
│ ETH    -0.5    +0.3     +0.1    +0.2  +0.1  │
│ ...                                          │
├────────────────────────────────────────────┤
│ SPREAD: SOL Drift→Hyperl. = 2.1 bps        │  ← Highlighted spread
└────────────────────────────────────────────┘
```

### Data Source
- `GET /api/v1/data/funding?grouped=true` (existing, returns by venue)
- Or new `GET /api/v1/data/funding-matrix` that returns `{market: {venue: rate}}`

### Implementation
- Simple HTML table with CSS background-color based on rate magnitude
- Green = positive (paying longs), Red = negative (paying shorts)
- Click a cell to see historical rate chart for that market+venue

---

## 4. MEV Opportunity Timeline

**Extend:** `ui/src/pages/MevDashboard.tsx`

Add a timeline section below existing arb route display.

### Layout Addition
```
┌────────────────────────────────────────────┐
│ [existing MevDashboard content]             │
├────────────────────────────────────────────┤
│ OPPORTUNITY TIMELINE                        │
│ profit_bps                                  │
│ 50 │        ●                               │
│ 30 │    ●       ●   ●                       │
│ 10 │ ●    ●  ●    ●    ●  ●               │
│  0 ├────────────────────────────── time     │
│    12:00  12:15  12:30  12:45  13:00       │
│    ● size = route hops (2-4)               │
└────────────────────────────────────────────┘
```

### Data Source
- Existing `POST /api/v1/mev/scan/arb` results cached client-side
- Or new `GET /api/v1/mev/history` if we store arb scan results

### Implementation
- Recharts `ScatterChart` with time on X, profit_bps on Y
- Dot size proportional to hop count
- Color: green if profitable, amber if marginal

---

## 5. Strategy Deployment Panel

**Extend:** `ui/src/pages/PaperTrading.tsx`

Collapsible panel at top of page for deploying strategies.

### Layout Addition
```
┌────────────────────────────────────────────┐
│ ▼ DEPLOY STRATEGY                [Collapse]│
│                                              │
│ Strategy: [momentum_breakout ▼]             │
│ Market:   [SOL-PERP ▼]                     │
│ Venue:    [drift ▼]                         │
│ Capital:  [$10,000 ____]                    │
│                                              │
│ Parameters:                                  │
│ breakout_lookback: [20 ____]                │
│ trailing_stop_pct: [0.02 ____]              │
│ oracle_confirmation: [✓]                    │
│                                              │
│ [Deploy Paper] [Deploy Dry-Run]             │
├────────────────────────────────────────────┤
│ [existing PaperTrading session list]        │
└────────────────────────────────────────────┘
```

### Data Sources
- `GET /api/v1/strategies` — list available strategies (existing)
- `GET /api/v1/data/markets` — list markets (existing)
- `POST /api/v1/paper/start` — deploy (existing, may need venue param)

### Implementation
- Collapsible section using React state (`isDeployOpen`)
- Strategy dropdown fetches parameters via strategy metadata
- Parameter form auto-generated from `parameters()` dict
- Deploy button calls paper start API with strategy config

---

## 6. New API Endpoints

**New file:** `flint/api/routes/live.py`

```python
router = APIRouter(prefix="/api/v1/live", tags=["live"])

@router.get("/fills")
# Query live fills by session, venue, market

@router.get("/equity")
# Query equity history by session

@router.get("/sessions")
# List active/recent live sessions
```

Register in `flint/api/main.py`.

---

## 7. Navigation Updates

**Modify:** `ui/src/App.tsx`

Add 3 new nav entries:
```typescript
{ to: '/live', label: 'LIVE', key: '7' },
{ to: '/fills', label: 'FILLS', key: '8' },
{ to: '/funding', label: 'FUNDING', key: '9' },
```

Add 3 new routes:
```typescript
<Route path="/live" element={<LiveMonitor />} />
<Route path="/fills" element={<FillAnalysis />} />
<Route path="/funding" element={<FundingHeatmap />} />
```

---

## 8. Dependencies

No new npm packages. Uses existing:
- `recharts` — scatter plots, line charts
- `lightweight-charts` — if needed for candlestick views
- Existing Tailwind theme and component patterns

---

## 9. ROADMAP Update

After implementation, update ROADMAP.md §5.3 with "Implemented" checkboxes.

---

## 10. Testing

- React components tested manually via `npm run dev`
- API endpoints tested via existing pytest patterns (mock store)
- No unit tests for React components (consistent with existing codebase — no component tests exist)
