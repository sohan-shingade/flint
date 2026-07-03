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
    // all three kinds are listed; oi is "none" (first-class, not omitted)
    expect(within(cell).getByText('candles')).toBeInTheDocument()
    expect(within(cell).getByText('funding')).toBeInTheDocument()
    expect(within(cell).getByText('oi')).toBeInTheDocument()
    expect(within(cell).getByText('none')).toBeInTheDocument()
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
