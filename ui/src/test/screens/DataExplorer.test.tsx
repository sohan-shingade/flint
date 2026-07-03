import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithRouter } from '../test-utils'
import { server } from '../mocks/handlers'
import DataExplorer from '../../screens/DataExplorer'

// Default grid: SOL-PERP,BTC-PERP × hyperliquid,jupiter. Candles+funding covered,
// oi absent — so the "none" per-kind state renders alongside covered ranges (D26).
function coverageHandler() {
  return http.get('/api/v1/data/coverage', ({ request }) => {
    const url = new URL(request.url)
    const market = url.searchParams.get('market')!
    const venue = url.searchParams.get('venue')!
    return HttpResponse.json({
      market,
      venue,
      coverage: {
        candles: { start_ms: 1700000000000, end_ms: 1700600000000 },
        funding: { start_ms: 1700000000000, end_ms: 1700600000000 },
        oi: null,
        trades: { start_ms: 1700000000000, end_ms: 1700300000000 },
      },
      detail: {
        candles: [{ start_ms: 1700000000000, end_ms: 1700600000000, source: 'hl_rest' }],
        trades: [{ start_ms: 1700000000000, end_ms: 1700300000000, source: 'recorder' }],
      },
      tiers: {
        candles: [{ start_ms: 1700000000000, end_ms: 1700600000000 }],
        ticks: [{ start_ms: 1700000000000, end_ms: 1700300000000 }],
        book: [],
      },
    })
  })
}

describe('DataExplorer', () => {
  it('renders a per-cell coverage matrix with each data kind', async () => {
    server.use(coverageHandler())
    renderWithRouter(<DataExplorer />)
    await waitFor(() => expect(screen.getByTestId('cov-hyperliquid-SOL-PERP')).toBeInTheDocument())
    const cell = screen.getByTestId('cov-hyperliquid-SOL-PERP')
    const kinds = within(cell).getByTestId('kinds')
    // all three kinds are listed; oi is "none" (first-class, not omitted)
    expect(within(kinds).getByText('candles')).toBeInTheDocument()
    expect(within(kinds).getByText('funding')).toBeInTheDocument()
    expect(within(kinds).getByText('oi')).toBeInTheDocument()
    expect(within(kinds).getByText('none')).toBeInTheDocument()
  })

  it('renders a per-tier coverage ladder colored by provenance', async () => {
    server.use(coverageHandler())
    renderWithRouter(<DataExplorer />)
    await waitFor(() =>
      expect(screen.getByTestId('ladder-hyperliquid-SOL-PERP')).toBeInTheDocument(),
    )
    const ladder = screen.getByTestId('ladder-hyperliquid-SOL-PERP')
    // Candles segment served by the free venue REST tier; ticks by the recorder.
    const candlesSeg = within(ladder).getAllByTestId('seg-candles')[0]
    const ticksSeg = within(ladder).getAllByTestId('seg-ticks')[0]
    expect(candlesSeg).toHaveAttribute('data-source', 'hl_rest')
    expect(ticksSeg).toHaveAttribute('data-source', 'recorder')
    // The book tier has no coverage — a first-class "none" rung, not omitted.
    expect(within(within(ladder).getByTestId('tier-book')).getByText('none')).toBeInTheDocument()
  })

  it('marks hyperliquid executable and jupiter as a lab venue (D28)', async () => {
    server.use(coverageHandler())
    renderWithRouter(<DataExplorer />)
    await waitFor(() => expect(screen.getByTestId('cov-hyperliquid-SOL-PERP')).toBeInTheDocument())
    expect(within(screen.getByTestId('cov-hyperliquid-SOL-PERP')).getByText(/executable/)).toBeInTheDocument()
    expect(within(screen.getByTestId('cov-jupiter-SOL-PERP')).getByText(/lab/)).toBeInTheDocument()
  })

  it('shows a per-cell coverage failure inline without sinking the grid', async () => {
    server.use(
      http.get('/api/v1/data/coverage', () =>
        HttpResponse.json({ error: { code: 'venue_unavailable', message: 'coverage down' } }, { status: 503 }),
      ),
    )
    renderWithRouter(<DataExplorer />)
    await waitFor(() =>
      expect(screen.getAllByText(/coverage query failed/).length).toBeGreaterThan(0),
    )
  })
})
