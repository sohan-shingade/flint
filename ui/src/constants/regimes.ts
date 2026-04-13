/**
 * Market regime definitions for multi-regime backtesting.
 * Keep in sync with: flint/regimes.py
 */

export interface Regime {
  id: string
  label: string
  regimeType: string // bull, bear, crash, sideways, high_vol
  startTs: number
  endTs: number
  startDate: string
  endDate: string
  color: string
  description: string
}

export const REGIMES: Regime[] = [
  {
    id: 'pre_etf_consolidation',
    label: 'Pre-ETF Consolidation',
    regimeType: 'sideways',
    startTs: 1701388800,
    endTs: 1704758400,
    startDate: '2023-12-01',
    endDate: '2024-01-09',
    color: '#78788a',
    description: 'Post-FTX recovery, low volatility, awaiting ETF catalyst',
  },
  {
    id: 'etf_bull_run',
    label: 'ETF Bull Run',
    regimeType: 'bull',
    startTs: 1704844800,
    endTs: 1717113600,
    startDate: '2024-01-10',
    endDate: '2024-05-31',
    color: '#57c84d',
    description: 'BTC spot ETF approval drives +70% rally across majors',
  },
  {
    id: 'summer_correction',
    label: 'Summer Correction',
    regimeType: 'bear',
    startTs: 1717200000,
    endTs: 1725062400,
    startDate: '2024-06-01',
    endDate: '2024-08-31',
    color: '#e84d4d',
    description: 'Profit-taking phase, BTC -12%, moderate volatility',
  },
  {
    id: 'recovery_rally',
    label: 'Recovery Rally',
    regimeType: 'bull',
    startTs: 1725148800,
    endTs: 1735603200,
    startDate: '2024-09-01',
    endDate: '2024-12-31',
    color: '#57c84d',
    description: 'Re-acceleration to new ATHs, BTC +58%, SOL +40%',
  },
  {
    id: 'peak_distribution',
    label: 'Peak & Distribution',
    regimeType: 'high_vol',
    startTs: 1735689600,
    endTs: 1743379200,
    startDate: '2025-01-01',
    endDate: '2025-03-31',
    color: '#e8a849',
    description: 'Topping pattern with high volatility and funding spikes',
  },
  {
    id: 'extended_decline',
    label: 'Extended Decline',
    regimeType: 'bear',
    startTs: 1743465600,
    endTs: 1759190400,
    startDate: '2025-04-01',
    endDate: '2025-09-30',
    color: '#e84d4d',
    description: 'Slow grind lower, SOL $240 to $186, declining momentum',
  },
  {
    id: 'crash_phase_1',
    label: 'Crash Phase 1',
    regimeType: 'crash',
    startTs: 1759276800,
    endTs: 1767139200,
    startDate: '2025-10-01',
    endDate: '2025-12-31',
    color: '#c84d4d',
    description: 'Accelerating sell-off, SOL $186 to $125, capitulation events',
  },
  {
    id: 'crash_phase_2',
    label: 'Crash Phase 2',
    regimeType: 'crash',
    startTs: 1767225600,
    endTs: 1775865600,
    startDate: '2026-01-01',
    endDate: '2026-04-09',
    color: '#c84d4d',
    description: 'Continued decline, SOL $125 to $83, dead cat bounces',
  },
]

export const REGIME_MAP = Object.fromEntries(REGIMES.map(r => [r.id, r]))

export const REGIME_TYPES: Record<string, { label: string; color: string }> = {
  bull: { label: 'Bull', color: '#57c84d' },
  bear: { label: 'Bear', color: '#e84d4d' },
  crash: { label: 'Crash', color: '#c84d4d' },
  sideways: { label: 'Sideways', color: '#78788a' },
  high_vol: { label: 'High Vol', color: '#e8a849' },
}

export function getRegime(id: string): Regime | undefined {
  return REGIME_MAP[id]
}

export function getRegimesByType(type: string): Regime[] {
  return REGIMES.filter(r => r.regimeType === type)
}
