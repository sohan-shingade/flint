# Flint UI Bug Report

**Generated**: 2026-04-13
**Test suite**: 115 tests across 14 files — all passing (bug tests document known issues)
**Methodology**: Vitest + React Testing Library + MSW (Mock Service Worker) simulating all 30+ API endpoints

---

## Bug #1: PaperTrading DeployPanel crashes — market objects rendered as React children

**Severity**: Critical
**Page**: PaperTrading (`/paper`)
**Component**: `DeployPanel` — market dropdown selector

### Steps to Reproduce
1. Navigate to `/paper`
2. Click the "DEPLOY.STRATEGY" expandable panel
3. Wait for strategies and markets to load from API
4. **Panel crashes** with React error before fully rendering

### Expected Behavior
The deploy panel should expand showing dropdowns for Strategy, Market, Venue, and Capital. User should be able to select a strategy and click "DEPLOY" to start a paper trading session.

### Actual Behavior
React throws: `"Objects are not valid as a React child (found: object with keys {market, resolution_s, candle_count, first_ts, last_ts})"`

The panel partially renders but the market `<select>` crashes because it tries to render `MarketInfo` objects directly as `<option>` text content.

### Root Cause
`PaperTrading.tsx` line ~469 in `DeployPanel`:
```tsx
fetch('/api/v1/data/markets').then(r => r.json()).then(d => {
  const mkts = d.markets || []
  setMarkets(mkts)                    // ← BUG: mkts is MarketInfo[], not string[]
  if (mkts.length > 0 && !market) setMarket(mkts[0])  // ← mkts[0] is an object
})
```
Then line ~535:
```tsx
{markets.map(m => <option key={m} value={m}>{m}</option>)}
//                                                  ^^^ m is {market, resolution_s, ...}, not "SOL-PERP"
```

The API `GET /api/v1/data/markets` returns `{markets: [{market: "SOL-PERP", resolution_s: 3600, candle_count: 2000, ...}]}` but the component assumes it returns a flat string array.

### Fix
In `PaperTrading.tsx` `DeployPanel`, change the market fetch handler:
```tsx
// Before:
setMarkets(mkts)
if (mkts.length > 0 && !market) setMarket(mkts[0])

// After:
const names = mkts.map((m: any) => typeof m === 'string' ? m : m.market)
setMarkets(names)
if (names.length > 0 && !market) setMarket(names[0])
```

### Files Involved
- `ui/src/pages/PaperTrading.tsx:469` — market state setter
- `ui/src/pages/PaperTrading.tsx:535` — market option rendering
- `flint/api/routes/data.py` — `/api/v1/data/markets` endpoint (returns correct data, frontend misinterprets)

### Tests
- `src/test/pages/PaperTrading.test.tsx` — "BUG: DEPLOY.STRATEGY panel crashes when market API returns objects"
- `src/test/pages/PaperTrading.test.tsx` — "BUG: deploy panel market selector crashes due to object-as-child rendering"
- `src/test/pages/PaperTrading.test.tsx` — "BUG: deploy button cannot be reached due to market rendering crash"

---

## Bug #2: FillAnalysis page crashes — same market object rendering issue

**Severity**: High
**Page**: FillAnalysis (`/fills`)
**Component**: Market filter dropdown

### Steps to Reproduce
1. Navigate to `/fills`
2. Page loads and fetches markets for the filter dropdown
3. **Component crashes** with React error

### Expected Behavior
The fill analysis page should render with filter dropdowns for Session ID, Venue, and Market. Users should be able to filter fills by market.

### Actual Behavior
React throws: `"Objects are not valid as a React child (found: object with keys {market, resolution_s, candle_count, first_ts, last_ts})"`

### Root Cause
`FillAnalysis.tsx` line ~62:
```tsx
fetch('/api/v1/data/markets')
  .then(r => r.json())
  .then(d => setMarkets(d.markets || []))  // ← d.markets is MarketInfo[], not string[]
```

Same issue as Bug #1 — the `GET /api/v1/data/markets` response contains objects, not strings.

### Fix
```tsx
// Before:
.then(d => setMarkets(d.markets || []))

// After:
.then(d => setMarkets((d.markets || []).map((m: any) => typeof m === 'string' ? m : m.market)))
```

### Files Involved
- `ui/src/pages/FillAnalysis.tsx:62` — market state setter

### Tests
- `src/test/pages/FillAnalysis.test.tsx` — "BUG: renders page but market filter crashes with object-as-child error"

---

## Bug #3: PaperTrading DeployPanel duplicate React keys

**Severity**: Medium
**Page**: PaperTrading (`/paper`)
**Component**: `DeployPanel` — strategy and market dropdowns

### Steps to Reproduce
1. Navigate to `/paper`
2. Click "DEPLOY.STRATEGY" panel
3. Open browser console

### Expected Behavior
No console warnings about duplicate keys.

### Actual Behavior
Console warning: `"Encountered two children with the same key, '[object Object]'"`

This is a consequence of Bug #1 — because market objects are used as React keys (`key={m}` where m is an object), React serializes them all to `[object Object]`, causing duplicate key warnings.

### Root Cause
Same as Bug #1 — objects used as keys in `.map()` calls.

### Fix
Fixing Bug #1 also fixes this issue.

### Files Involved
- `ui/src/pages/PaperTrading.tsx:535`

---

## Bug #4: Deploy-to-Paper dialog uses inconsistent design system

**Severity**: Low (UX)
**Page**: BacktestLab (`/backtest`)
**Component**: Deploy to Paper Trading modal dialog

### Steps to Reproduce
1. Navigate to `/backtest`
2. Run a successful backtest
3. Click "DEPLOY" button in results panel
4. Observe the modal styling

### Expected Behavior
The deploy dialog should use the same terminal/hacker aesthetic as the rest of the app (dark bg, amber accents, squared borders, monospace text).

### Actual Behavior
The dialog uses:
- `bg-zinc-800` (generic Tailwind gray) instead of the app's `bg-surface` or `bg-panel`
- `rounded-xl` and `rounded-lg` (rounded corners) — the rest of the app uses no border-radius
- `bg-purple-600` for the deploy button — the rest of the app uses `bg-amber`
- Standard Tailwind spacing vs. the app's custom design tokens

This creates a visual inconsistency that breaks the terminal aesthetic.

### Root Cause
The deploy dialog in `BacktestLab.tsx` (lines ~1883-1963) was likely added separately and not aligned with the design system used everywhere else.

### Fix
Restyle the dialog to match the app:
```tsx
// Replace rounded styles with squared terminal style
// bg-zinc-800 → bg-panel border-border
// rounded-xl → (no rounding)
// bg-purple-600 → bg-amber text-void
// rounded-lg → (no rounding)
```

### Files Involved
- `ui/src/pages/BacktestLab.tsx:1883-1963` — deploy dialog

---

## Bug #5: BacktestLab RUN button disabled state unclear

**Severity**: Low (UX)
**Page**: BacktestLab (`/backtest`)
**Component**: RUN_BACKTEST button

### Steps to Reproduce
1. Navigate to `/backtest`
2. Observe the RUN button when data is not available
3. The button says "NEED DATA" but doesn't explain which market or date range

### Expected Behavior
When the RUN button is disabled, the user should clearly understand:
- Which market is missing data
- What date range needs to be downloaded
- A direct link or instruction to fix it

### Actual Behavior
Button shows "NEED DATA" or "FIX CONFIG" or "MISSING DATA" — somewhat informative but not actionable. The tooltip (`title` attribute) has more detail but is not always visible. The data check indicator below the button is more helpful but users may not notice it.

### Files Involved
- `ui/src/pages/BacktestLab.tsx:1386-1401` — button label/state logic
- `ui/src/pages/BacktestLab.tsx:1290-1311` — data check indicator

---

## Bug #6: PaperTrading page error when portfolio API is down

**Severity**: Medium
**Page**: PaperTrading (`/paper`)
**Component**: Page-level error handling

### Steps to Reproduce
1. Stop the Flint backend server
2. Navigate to `/paper`
3. Page shows "API ERROR" with the raw error string

### Expected Behavior
A user-friendly error message with instructions (e.g., "Cannot connect to Flint server. Run `flint serve` to start it.")

### Actual Behavior
Shows `API ERROR: TypeError: Failed to fetch` — a raw JavaScript error string that means nothing to end users.

### Root Cause
`PaperTrading.tsx` line ~606-607 shows `error` directly from the `usePaperPortfolio` hook, which just does `setError(String(e))`.

### Fix
Wrap error display with user-friendly messaging:
```tsx
// In usePaperTrading.ts, catch block:
if (active) setError('Cannot connect to server — run flint serve')
```

### Files Involved
- `ui/src/hooks/usePaperTrading.ts:57` — error setter
- `ui/src/pages/PaperTrading.tsx:671` — error display

---

## UX Audit Notes (Quant Desk Perspective)

### Finding: Data download → Backtest flow requires page switching
A quant needs to: (1) go to Data Explorer, (2) download data, (3) switch to BacktestLab, (4) hope the data check passes. There's no in-line "download now" option on BacktestLab when data is missing. The "NEED DATA" button should offer a one-click download option.

### Finding: Paper Trading has no way to deploy without BacktestLab
The DeployPanel on PaperTrading lets you start a built-in strategy, but if a user wants to deploy custom code, they must go through BacktestLab's deploy dialog. There's no code editor on the Paper Trading page. This is fine but should be more clearly communicated.

### Finding: Keyboard shortcuts not discoverable
`Ctrl+Enter` to run and `Ctrl+S` to save are mentioned at the bottom of the config panel in tiny text. No keyboard shortcut overlay or `?` help is available. TradingView and QuantConnect both have discoverable shortcut panels.

### Finding: Loading states inconsistent
- BacktestLab: Shows progress bar with phase/percentage
- DataExplorer: Shows "LOADING..." text
- PaperTrading: Shows "CONNECTING..." with pulse animation
- Dashboard: No loading state at all

These should be standardized.
