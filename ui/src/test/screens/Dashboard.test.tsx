import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor } from '@testing-library/react'
import { renderWithRouter } from '../test-utils'
import { server } from '../mocks/handlers'
import Dashboard from '../../screens/Dashboard'
import type { RunRow } from '../../api/types'

// Hand-authored rows mirroring services.runs _row (D26). One carries the funding-gate
// note the dashboard reads to classify a run as "rejected".
const RUNS: RunRow[] = [
  {
    run_id: 'aaaaaaaa1111',
    strategy: 'ma_cross',
    kind: 'backtest',
    created_ts: 1700000000000,
    effective_start_ts: 1700000000000,
    effective_end_ts: 1700600000000,
    metrics: { sharpe: 1.2 },
    seed: 0,
    engine_version: 'v1',
    note: '',
  },
  {
    run_id: 'bbbbbbbb2222',
    strategy: 'funding_harvest',
    kind: 'backtest',
    created_ts: 1700100000000,
    effective_start_ts: null,
    effective_end_ts: null,
    metrics: {},
    seed: 0,
    engine_version: 'v1',
    note: 'rejected: funding_gap',
  },
]

function stubEndpoints() {
  server.use(
    http.get('/api/v1/runs', () => HttpResponse.json({ runs: RUNS })),
    http.get('/api/v1/templates', () =>
      HttpResponse.json({
        templates: [
          { name: 'ma_cross', category: 'technical', summary: 'ma cross', is_ml: false, params: { fast: 10 } },
          { name: 'funding_harvest', category: 'funding', summary: 'harvest', is_ml: false, params: {} },
        ],
        executable_venues: ['hyperliquid'],
      }),
    ),
    http.get('/api/v1/system/health', () => HttpResponse.json({ ok: true, version: '1.2.3' })),
  )
}

describe('Dashboard', () => {
  it('renders status cards and a recent-runs table that deep-links to results', async () => {
    stubEndpoints()
    renderWithRouter(<Dashboard />)

    await waitFor(() => expect(screen.getByTestId('recent-aaaaaaaa1111')).toBeInTheDocument())

    // Both runs appear; the funding-gated one reads "rejected", the other "ok".
    expect(screen.getByTestId('recent-bbbbbbbb2222')).toBeInTheDocument()
    expect(screen.getByText('rejected')).toBeInTheDocument()
    expect(screen.getAllByText('ok').length).toBeGreaterThan(0)

    // The run id cell links into RESULTS by run id.
    const link = screen.getByRole('link', { name: 'aaaaaaaa' })
    expect(link).toHaveAttribute('href', '/results?run=aaaaaaaa1111')

    // Template count surfaced from GET /templates.
    expect(screen.getByText('ONLINE')).toBeInTheDocument()
  })

  it('renders a first-class error state when /runs faults, never a blank screen', async () => {
    server.use(
      http.get('/api/v1/runs', () =>
        HttpResponse.json({ error: { code: 'unauthorized', message: 'bad token', hint: 'restart flint serve' } }, { status: 401 }),
      ),
      http.get('/api/v1/templates', () => HttpResponse.json({ templates: [], executable_venues: [] })),
      http.get('/api/v1/system/health', () => HttpResponse.json({ ok: true, version: '1.2.3' })),
    )
    renderWithRouter(<Dashboard />)
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.getByText(/restart flint serve/)).toBeInTheDocument()
  })
})
