/**
 * Venue configuration — single source of truth is the backend
 * (flint/execution/venue_config.py), served via GET /api/v1/system/venues.
 *
 * This file provides static fallback data and the fetchVenues() function
 * to load the full list from the API at runtime.
 */

export interface ExecutionVenue {
  id: string
  label: string
  type: 'dex' | 'cex'
  takerFeeBps: number
  makerFeeBps: number
  maxLeverage: number
  latencyS: number
  color: string
}

// Colors for each venue (used in charts and UI)
const VENUE_COLORS: Record<string, string> = {
  drift: '#e8a849',
  hyperliquid: '#22d3ee',
  jupiter: '#c4b5fd',
  binance: '#f0b90b',
  okx: '#a78bfa',
  bybit: '#57c84d',
  dydx: '#6366f1',
  coinbase: '#3b82f6',
  kraken: '#7c3aed',
  kucoin: '#06b6d4',
  gate: '#ef4444',
  bitget: '#f97316',
  mexc: '#14b8a6',
  htx: '#8b5cf6',
}

// Static fallback (used before API responds)
const STATIC_VENUES: ExecutionVenue[] = [
  { id: 'drift', label: 'Drift', type: 'dex', takerFeeBps: 10, makerFeeBps: -2, maxLeverage: 10, latencyS: 8, color: '#e8a849' },
  { id: 'hyperliquid', label: 'Hyperliquid', type: 'dex', takerFeeBps: 3.5, makerFeeBps: 1, maxLeverage: 20, latencyS: 1, color: '#22d3ee' },
  { id: 'jupiter', label: 'Jupiter', type: 'dex', takerFeeBps: 6, makerFeeBps: 6, maxLeverage: 100, latencyS: 12, color: '#c4b5fd' },
  { id: 'binance', label: 'Binance', type: 'cex', takerFeeBps: 4.5, makerFeeBps: 2, maxLeverage: 50, latencyS: 0.2, color: '#f0b90b' },
  { id: 'okx', label: 'OKX', type: 'cex', takerFeeBps: 5, makerFeeBps: 2, maxLeverage: 50, latencyS: 0.3, color: '#a78bfa' },
  { id: 'bybit', label: 'Bybit', type: 'cex', takerFeeBps: 5.5, makerFeeBps: 2, maxLeverage: 50, latencyS: 0.3, color: '#57c84d' },
  { id: 'dydx', label: 'dYdX', type: 'cex', takerFeeBps: 5, makerFeeBps: 1, maxLeverage: 20, latencyS: 2, color: '#6366f1' },
  { id: 'coinbase', label: 'Coinbase', type: 'cex', takerFeeBps: 6, makerFeeBps: 4, maxLeverage: 10, latencyS: 0.15, color: '#3b82f6' },
  { id: 'kraken', label: 'Kraken', type: 'cex', takerFeeBps: 4, makerFeeBps: 1.6, maxLeverage: 50, latencyS: 0.3, color: '#7c3aed' },
  { id: 'kucoin', label: 'KuCoin', type: 'cex', takerFeeBps: 6, makerFeeBps: 2, maxLeverage: 50, latencyS: 0.3, color: '#06b6d4' },
  { id: 'gate', label: 'Gate.io', type: 'cex', takerFeeBps: 7.5, makerFeeBps: 3.5, maxLeverage: 50, latencyS: 0.3, color: '#ef4444' },
  { id: 'bitget', label: 'Bitget', type: 'cex', takerFeeBps: 6, makerFeeBps: 2, maxLeverage: 50, latencyS: 0.3, color: '#f97316' },
  { id: 'mexc', label: 'MEXC', type: 'cex', takerFeeBps: 0, makerFeeBps: 0, maxLeverage: 50, latencyS: 0.3, color: '#14b8a6' },
  { id: 'htx', label: 'HTX', type: 'cex', takerFeeBps: 5, makerFeeBps: 2, maxLeverage: 50, latencyS: 0.3, color: '#8b5cf6' },
]

// Mutable venue list — populated from API or static fallback
let _venues: ExecutionVenue[] = [...STATIC_VENUES]
let _fetched = false

/** Fetch venues from the backend API. Call once on app init. */
export async function fetchVenues(): Promise<ExecutionVenue[]> {
  if (_fetched) return _venues
  try {
    const r = await fetch('/api/v1/system/venues')
    if (r.ok) {
      const data = await r.json()
      _venues = (data.venues || []).map((v: any) => ({
        id: v.id,
        label: v.label,
        type: v.type as 'dex' | 'cex',
        takerFeeBps: v.taker_fee_bps,
        makerFeeBps: v.maker_fee_bps,
        maxLeverage: v.max_leverage,
        latencyS: v.latency_s,
        color: VENUE_COLORS[v.id] || '#888888',
      }))
      _fetched = true
    }
  } catch { /* use static fallback */ }
  return _venues
}

// Exports that match the old API so existing components work without changes
export const EXECUTION_VENUES = STATIC_VENUES
export const DEX_VENUES = STATIC_VENUES.filter(v => v.type === 'dex')
export const CEX_VENUES = STATIC_VENUES.filter(v => v.type === 'cex')
export const DEFAULT_EXECUTION_VENUES = ['drift', 'hyperliquid']

export function getVenue(id: string): ExecutionVenue | undefined {
  return _venues.find(v => v.id === id)
}

export function getVenueColor(id: string): string {
  return VENUE_COLORS[id] || '#888888'
}

export function getAllVenues(): ExecutionVenue[] {
  return _venues
}
