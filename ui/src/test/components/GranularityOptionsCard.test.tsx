import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { GranularityOptionsCard } from '../../components/GranularityOptionsCard'
import type { RejectedPayload } from '../../api/types'

// Hand-authored to mirror flint/services/backtest._rejection_from_granularity (D26).
const REJECTED: RejectedPayload = {
  code: 'granularity_unavailable',
  message: "Granularity 'ticks' unavailable over the requested range — backtest rejected.",
  missing: ['SOL-PERP@hyperliquid'],
  available: {},
  hint: 'Run at bars, clip to coverage, backfill via Tardis, or record forward.',
  granularity: 'ticks',
  coverage: {
    'SOL-PERP@hyperliquid': {
      trades: [{ start_ms: 1700000000000, end_ms: 1700300000000 }],
      funding: [{ start_ms: 1700000000000, end_ms: 1700600000000 }],
    },
  },
  options: [
    { action: 'run_bars', granularity: 'candles' },
    { action: 'clip_to_coverage', effective_range: { start_ms: 1700000000000, end_ms: 1700300000000 } },
    { action: 'vendor_backfill', vendor: 'tardis', requires_secret: 'TARDIS_API_KEY', available: null },
    { action: 'record_forward', hint: 'flint data record --venue hyperliquid --market SOL-PERP' },
  ],
}

describe('GranularityOptionsCard', () => {
  it('renders the rejection, coverage, and all four ways out', () => {
    render(<GranularityOptionsCard rejected={REJECTED} />)
    expect(screen.getByText(/rejected · granularity_unavailable/)).toBeInTheDocument()
    expect(screen.getByText(/requested tier: ticks/)).toBeInTheDocument()
    // Per-kind coverage is shown so the user sees exactly which stream is short.
    expect(screen.getByText('trades')).toBeInTheDocument()
    // The four machine-readable ways out render as affordances.
    expect(screen.getByRole('button', { name: /run at bars \(candles\)/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /clip to coverage/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /backfill via tardis/ })).toBeInTheDocument()
    // vendor_backfill is advertised even without a key (no window yet).
    expect(screen.getByText(/needs TARDIS_API_KEY/)).toBeInTheDocument()
    // record_forward is a display-only CLI hint.
    expect(screen.getByText(/flint data record --venue hyperliquid/)).toBeInTheDocument()
  })

  it('fires resubmit + backfill callbacks with the option payloads', async () => {
    const onRunAtBars = vi.fn()
    const onClip = vi.fn()
    const onBackfill = vi.fn()
    render(
      <GranularityOptionsCard
        rejected={REJECTED}
        actions={{ onRunAtBars, onClip, onBackfill }}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /run at bars/ }))
    expect(onRunAtBars).toHaveBeenCalledWith('candles')

    await userEvent.click(screen.getByRole('button', { name: /clip to coverage/ }))
    expect(onClip).toHaveBeenCalledWith({ start_ms: 1700000000000, end_ms: 1700300000000 })

    await userEvent.click(screen.getByRole('button', { name: /backfill via tardis/ }))
    expect(onBackfill).toHaveBeenCalledWith(null)
  })

  it('disables actionable buttons when no callbacks are wired (view-only surface)', () => {
    render(<GranularityOptionsCard rejected={REJECTED} />)
    expect(screen.getByRole('button', { name: /run at bars/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /clip to coverage/ })).toBeDisabled()
  })
})
