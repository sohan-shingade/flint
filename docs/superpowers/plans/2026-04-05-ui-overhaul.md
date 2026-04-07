# UI Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign Data Explorer, BacktestLab, and Onboarding to reflect the new Pyth-first pricing + per-venue execution model.

**Architecture:** Remove candle source selectors (Pyth is sole price source). Replace funding venues with execution venue cards showing data type + source. Replace BacktestLab fee dropdown with venue selector showing fill model profile. Update onboarding to single execution venues step.

**Tech Stack:** React 19, TypeScript, Tailwind CSS, Vite

**Spec:** `docs/superpowers/specs/2026-04-05-pyth-pricing-venue-fill-pipelines-design.md` (Sub-project 3, sections 3.1-3.3)

---

## Task 1: Shared Venue Constants

Extract venue definitions into a shared constants file so all 3 pages use the same source of truth.

**Files:**
- Create: `ui/src/constants/venues.ts`
- Test: manual — verify imports work

- [ ] **Step 1: Create shared venue constants**

```typescript
// ui/src/constants/venues.ts
export interface ExecutionVenue {
  id: string
  label: string
  type: 'dex' | 'cex'
  dataType: string        // "Funding rates (1h) + Orderbook depth"
  dataSource: string      // "Free" | "Requires Tardis API key"
  color: string
  fillModel: string       // "JIT Auction → DLOB → vAMM"
  takerFee: string        // "10 bps"
  makerFee: string        // "-2 bps"
  latency: string         // "8s ± 5s"
  fundingType: string     // "hourly" | "borrow"
}

export const EXECUTION_VENUES: ExecutionVenue[] = [
  {
    id: 'drift', label: 'Drift', type: 'dex',
    dataType: 'Funding rates (1h) + Orderbook depth',
    dataSource: 'Free (Drift S3)',
    color: '#e8a849',
    fillModel: 'JIT Auction → DLOB → vAMM',
    takerFee: '10 bps', makerFee: '-2 bps (rebate)',
    latency: '8s ± 5s', fundingType: 'hourly',
  },
  {
    id: 'hyperliquid', label: 'Hyperliquid', type: 'dex',
    dataType: 'Funding rates (1h) + Orderbook depth',
    dataSource: 'Free (HL Archive)',
    color: '#22d3ee',
    fillModel: 'CLOB + HLP Backstop',
    takerFee: '4.5 bps', makerFee: '-1.5 bps (rebate)',
    latency: '0.2s ± 0.1s', fundingType: 'hourly',
  },
  {
    id: 'jupiter', label: 'Jupiter', type: 'dex',
    dataType: 'Borrow rates + Pool impact',
    dataSource: 'Free (On-chain)',
    color: '#c4b5fd',
    fillModel: 'Oracle Price + Keeper Delay',
    takerFee: '6 bps', makerFee: '6 bps (flat)',
    latency: '12s ± 8s', fundingType: 'borrow',
  },
  {
    id: 'binance', label: 'Binance', type: 'cex',
    dataType: 'Funding rates (8h) + Orderbook depth',
    dataSource: 'Requires Tardis API key',
    color: '#f0b90b',
    fillModel: 'CLOB Walk',
    takerFee: '5 bps', makerFee: '2 bps',
    latency: '0.2s ± 0.1s', fundingType: 'hourly',
  },
  {
    id: 'okx', label: 'OKX', type: 'cex',
    dataType: 'Funding rates (8h) + Orderbook depth',
    dataSource: 'Requires Tardis API key',
    color: '#a78bfa',
    fillModel: 'CLOB Walk',
    takerFee: '5 bps', makerFee: '2 bps',
    latency: '0.3s ± 0.15s', fundingType: 'hourly',
  },
  {
    id: 'bybit', label: 'Bybit', type: 'cex',
    dataType: 'Funding rates (8h) + Orderbook depth',
    dataSource: 'Requires Tardis API key',
    color: '#57c84d',
    fillModel: 'CLOB Walk + IOC Band',
    takerFee: '5.5 bps', makerFee: '2 bps',
    latency: '0.3s ± 0.15s', fundingType: 'hourly',
  },
]

export const DEX_VENUES = EXECUTION_VENUES.filter(v => v.type === 'dex')
export const CEX_VENUES = EXECUTION_VENUES.filter(v => v.type === 'cex')
export const DEFAULT_EXECUTION_VENUES = ['drift', 'hyperliquid']

export function getVenue(id: string): ExecutionVenue | undefined {
  return EXECUTION_VENUES.find(v => v.id === id)
}
```

- [ ] **Step 2: Commit**

```bash
git add ui/src/constants/venues.ts
git commit -m "feat: add shared ExecutionVenue constants for UI"
```

---

## Task 2: Data Explorer Redesign

**Files:**
- Modify: `ui/src/pages/DataExplorer.tsx`

The redesign:
1. Remove CANDLE_VENUES constant and candle source selector section
2. Remove ALL_VENUES / DEFAULT_VENUES — use EXECUTION_VENUES from shared constants
3. Replace candle source + funding venues sections with a single "Execution Venues" section using venue cards
4. Add Pyth price source informational banner
5. Update download handler to use `execution_venues` param instead of `venue` + `funding_venues`
6. Update download progress to show per-venue status
7. Add migration banner (shown once)

- [ ] **Step 1: Replace venue constants with imports**

Remove the inline `CANDLE_VENUES`, `ALL_VENUES`, `DEFAULT_CANDLE_VENUES`, `DEFAULT_VENUES` constants. Replace with:

```typescript
import { EXECUTION_VENUES, DEFAULT_EXECUTION_VENUES, DEX_VENUES, CEX_VENUES, getVenue } from '../constants/venues'
```

Replace `selectedCandleVenues` and `selectedVenues` state with a single:
```typescript
const [selectedExecutionVenues, setSelectedExecutionVenues] = useState<string[]>(DEFAULT_EXECUTION_VENUES)
```

- [ ] **Step 2: Replace Candle Sources + Funding Venues sections with Execution Venues cards**

Remove the "CANDLE SOURCE" section (the row of Drift/Hyperliquid/Binance/OKX/Bybit buttons).
Remove the "FUNDING VENUES" section (the row of funding venue buttons with freq badges).

Replace with a single "EXECUTION VENUES" section:

```tsx
{/* Execution Venues */}
<div className="mb-6">
  <div className="flex items-center justify-between mb-2">
    <label className="text-ghost text-[10px] tracking-wider">
      EXECUTION VENUES <span className="text-ghost/40">— venues to simulate trading on</span>
    </label>
    <div className="flex gap-2 text-[9px]">
      <button onClick={() => setSelectedExecutionVenues(DEX_VENUES.map(v => v.id))}
              className="text-ghost/60 hover:text-ghost">All DEX</button>
      <button onClick={() => setSelectedExecutionVenues(CEX_VENUES.map(v => v.id))}
              className="text-ghost/60 hover:text-ghost">All CEX</button>
      <button onClick={() => setSelectedExecutionVenues(EXECUTION_VENUES.map(v => v.id))}
              className="text-ghost/60 hover:text-ghost">All</button>
      <button onClick={() => setSelectedExecutionVenues([])}
              className="text-ghost/60 hover:text-ghost">None</button>
    </div>
  </div>
  <div className="grid grid-cols-3 gap-2">
    {EXECUTION_VENUES.map(v => {
      const selected = selectedExecutionVenues.includes(v.id)
      return (
        <button
          key={v.id}
          onClick={() => setSelectedExecutionVenues(prev =>
            prev.includes(v.id) ? prev.filter(x => x !== v.id) : [...prev, v.id]
          )}
          className={`p-3 border text-left transition-colors ${
            selected ? 'border-amber bg-amber-glow/10' : 'border-border hover:border-border-bright'
          }`}
          style={selected ? { borderColor: v.color } : {}}
        >
          <div className="flex items-center justify-between mb-1">
            <span className="text-[11px] font-medium" style={selected ? { color: v.color } : { color: '#9ca3af' }}>
              {v.label}
            </span>
            <span className="text-[8px] px-1.5 py-0.5 border border-border/50 text-ghost/50 uppercase">
              {v.type}
            </span>
          </div>
          <div className="text-[9px] text-ghost/40">{v.dataType}</div>
          <div className="text-[8px] text-ghost/30 mt-1">{v.dataSource}</div>
        </button>
      )
    })}
  </div>
  <p className="text-ghost/40 text-[9px] mt-1">
    {selectedExecutionVenues.length} selected — downloads orderbook depth + funding/borrow data per venue
  </p>
</div>

{/* Price Source Banner */}
<div className="mb-6 px-3 py-2 border border-border/30 bg-panel/50">
  <span className="text-[9px] text-ghost/50">
    PRICE DATA: <span className="text-amber/60">Pyth Oracle</span> — canonical oracle prices used across all venues
  </span>
</div>
```

- [ ] **Step 3: Update download handler**

In `handleBulkDownload`, replace the inner loop that iterates candle venues with a single Pyth download + execution venues for supplementary data:

```typescript
// Old: for (const cv of candleVenuesToFetch) { fetch(..., venue: cv) }
// New: single call with execution_venues
const res = await fetch('/api/v1/data/download', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    market: mkt,
    resolution_s: 3600,
    start_ts: startTs,
    end_ts: endTs,
    execution_venues: selectedExecutionVenues,
  }),
})
```

- [ ] **Step 4: Remove multi-venue price overlay state**

Remove `priceVenues`, `priceViewMode`, `venueCandles`, `VENUE_COLORS` state and the venue price overlay UI section. Price data now comes from a single source (Pyth).

- [ ] **Step 5: Build and verify**

```bash
cd ui && npm run build
```

- [ ] **Step 6: Commit**

```bash
git add ui/src/pages/DataExplorer.tsx
git commit -m "feat: redesign Data Explorer with execution venues + Pyth price source"
```

---

## Task 3: BacktestLab Venue Selector

**Files:**
- Modify: `ui/src/pages/BacktestLab.tsx`

Replace the fee preset dropdown with an execution venue selector that shows the venue's fill model profile.

- [ ] **Step 1: Import shared constants**

```typescript
import { EXECUTION_VENUES, getVenue } from '../constants/venues'
```

- [ ] **Step 2: Replace fee preset dropdown with venue selector**

Replace the fee `<select>` with optgroups (lines 916-952) with:

```tsx
{/* Execution Venue Selector */}
<div>
  <label className="text-[9px] text-ghost tracking-wider">EXECUTION VENUE</label>
  <select
    value={venue}
    onChange={(e) => {
      const v = e.target.value
      setVenue(v)
      // Auto-set fee rate from venue config
      const venueInfo = getVenue(v)
      if (venueInfo) {
        setFeeRate(parseFloat(venueInfo.takerFee) / 10000)
        setFeePreset(`${v}_taker`)
      }
    }}
    className={inputClass}
  >
    <optgroup label="DEX">
      {EXECUTION_VENUES.filter(v => v.type === 'dex').map(v => (
        <option key={v.id} value={v.id}>{v.label}</option>
      ))}
    </optgroup>
    <optgroup label="CEX">
      {EXECUTION_VENUES.filter(v => v.type === 'cex').map(v => (
        <option key={v.id} value={v.id}>{v.label}</option>
      ))}
    </optgroup>
  </select>
</div>

{/* Venue Execution Profile */}
{(() => {
  const venueInfo = getVenue(venue)
  if (!venueInfo) return null
  return (
    <div className="col-span-2 p-2 border border-border/30 bg-panel/50 text-[9px]">
      <div className="text-ghost/60 mb-1">EXECUTION PROFILE</div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-ghost/40">
        <span>Fill Model:</span><span className="text-ghost/70">{venueInfo.fillModel}</span>
        <span>Taker Fee:</span><span className="text-ghost/70">{venueInfo.takerFee}</span>
        <span>Maker Fee:</span><span className="text-ghost/70">{venueInfo.makerFee}</span>
        <span>Latency:</span><span className="text-ghost/70">{venueInfo.latency}</span>
        <span>Depth:</span><span className="text-ghost/70">{venueInfo.dataSource}</span>
        <span>Funding:</span><span className="text-ghost/70">{venueInfo.fundingType === 'borrow' ? 'Borrow fees (continuous)' : `Funding rates (${venueInfo.fundingType})`}</span>
      </div>
    </div>
  )
})()}
```

- [ ] **Step 3: Keep old fee dropdown as Advanced Override**

Wrap the existing fee preset dropdown in a collapsible "Advanced Override" section:

```tsx
<details className="col-span-2">
  <summary className="text-[9px] text-ghost/40 cursor-pointer hover:text-ghost/60">
    Advanced: Override fee rate manually
  </summary>
  <div className="mt-2">
    {/* existing fee preset <select> here */}
  </div>
</details>
```

- [ ] **Step 4: Update venue fee display for multi-venue strategies**

Update the multi-venue info section (around line 888) to use `getVenue()`:

```tsx
{(stratProfile.venues.length > 0 ? stratProfile.venues : ['drift', 'hyperliquid']).map(v => {
  const info = getVenue(v)
  return (
    <div key={v} className="text-[9px] text-ghost/50">
      {v.toUpperCase()}: {info?.takerFee || '?'} taker / {info?.fillModel || 'default'}
    </div>
  )
})}
```

- [ ] **Step 5: Update deploy venue selector**

Replace the hardcoded venue list with EXECUTION_VENUES:

```tsx
<select value={deployVenue} onChange={e => setDeployVenue(e.target.value)} className={inputClass}>
  {EXECUTION_VENUES.map(v => (
    <option key={v.id} value={v.id}>{v.label}</option>
  ))}
</select>
```

- [ ] **Step 6: Set default venue to 'drift'**

Change `const [venue, setVenue] = useState('default')` to `useState('drift')`.

- [ ] **Step 7: Build and verify**

```bash
cd ui && npm run build
```

- [ ] **Step 8: Commit**

```bash
git add ui/src/pages/BacktestLab.tsx
git commit -m "feat: replace fee dropdown with execution venue selector in BacktestLab"
```

---

## Task 4: Onboarding Redesign

**Files:**
- Modify: `ui/src/pages/Setup.tsx`

Replace the two-section venue step (candle sources + funding venues) with a single execution venues section.

- [ ] **Step 1: Import shared constants and replace inline constants**

```typescript
import { EXECUTION_VENUES, DEFAULT_EXECUTION_VENUES } from '../constants/venues'
```

Remove `CANDLE_VENUES` and `FUNDING_VENUES` inline constants.
Replace `selectedCandleVenues` and `selectedFundingVenues` state with:

```typescript
const [selectedExecutionVenues, setSelectedExecutionVenues] = useState<string[]>(DEFAULT_EXECUTION_VENUES)
```

- [ ] **Step 2: Replace venue selection UI**

Replace the two sections (CANDLE SOURCES + FUNDING VENUES) with:

```tsx
{step === 'venues' && (
  <div className="max-w-2xl mx-auto">
    <h2 className="text-xl font-mono text-amber mb-2">SELECT EXECUTION VENUES</h2>
    <p className="text-ghost text-xs mb-6">
      Choose which venues to simulate trading on. Flint downloads orderbook depth + funding data for realistic fill modeling.
      Price data comes from Pyth oracle automatically.
    </p>

    <div className="grid grid-cols-3 gap-3 mb-6">
      {EXECUTION_VENUES.map(v => {
        const selected = selectedExecutionVenues.includes(v.id)
        return (
          <button
            key={v.id}
            onClick={() => setSelectedExecutionVenues(prev =>
              prev.includes(v.id) ? prev.filter(x => x !== v.id) : [...prev, v.id]
            )}
            className={`p-3 border text-left transition-colors ${
              selected ? 'border-amber bg-amber-glow/10' : 'border-border hover:border-border-bright'
            }`}
            style={selected ? { borderColor: v.color } : {}}
          >
            <div className="flex items-center justify-between mb-1">
              <span className="text-[12px] font-medium" style={selected ? { color: v.color } : { color: '#9ca3af' }}>
                {v.label}
              </span>
              <span className="text-[8px] px-1.5 py-0.5 border border-border/50 text-ghost/40 uppercase">
                {v.type}
              </span>
            </div>
            <div className="text-[9px] text-ghost/40">{v.dataSource}</div>
          </button>
        )
      })}
    </div>

    <p className="text-ghost/30 text-[9px] mb-4">
      * CEX orderbook data requires a Tardis API key. Without it, synthetic depth models are used.
    </p>

    <p className="text-ghost/40 text-[9px]">
      {selectedExecutionVenues.length} selected — Drift + Hyperliquid recommended for getting started
    </p>

    <hr className="border-border/30 my-6" />
    {/* BACK / NEXT buttons */}
  </div>
)}
```

- [ ] **Step 3: Update download handler**

In `startDownload`, replace the candle venue loop with a single call using `execution_venues`:

```typescript
const res = await fetch('/api/v1/data/download', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    market: selectedMarkets[i],
    resolution_s: 3600,
    start_ts: startTs,
    end_ts: now,
    execution_venues: selectedExecutionVenues,
  }),
})
```

Remove the inner `for (const venue of selectedCandleVenues)` loop — it's now a single API call per market.

- [ ] **Step 4: Build and verify**

```bash
cd ui && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add ui/src/pages/Setup.tsx
git commit -m "feat: redesign onboarding with single execution venues step"
```

---

## Task 5: Final Build + Smoke Test

- [ ] **Step 1: Build production UI**

```bash
cd ui && npm run build
```

- [ ] **Step 2: Restart server and verify**

```bash
pkill -f "flint.cli serve" 2>/dev/null; sleep 1
python3.10 -m flint.cli serve &
sleep 3
curl -s http://127.0.0.1:8000/api/v1/health
```

- [ ] **Step 3: Commit build**

```bash
git add ui/src/
git commit -m "feat: complete UI overhaul — Pyth pricing + execution venue model"
```

---

## Summary

| Task | Component | What Changes |
|------|-----------|-------------|
| 1 | Shared constants | New `venues.ts` with ExecutionVenue type + all 6 venues |
| 2 | Data Explorer | Remove candle sources, add venue cards, Pyth banner, update download |
| 3 | BacktestLab | Venue selector with fill profile, advanced fee override |
| 4 | Onboarding | Single execution venues step, updated download |
| 5 | Build + verify | Production build and smoke test |
