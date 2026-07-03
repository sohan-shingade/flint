import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor } from '@testing-library/react'
import { renderWithRouter } from '../test-utils'
import { server } from '../mocks/handlers'
import FundingHeatmap from '../../screens/FundingHeatmap'

// Coverage handler mirroring services.data_coverage: hyperliquid has funding,
// jupiter does not — so both the "covered" and "no funding data" cell states are
// exercised. Hand-authored ranges (D26).
function coverageHandler() {
  return http.get('/api/v1/data/coverage', ({ request }) => {
    const url = new URL(request.url)
    const market = url.searchParams.get('market')!
    const venue = url.searchParams.get('venue')!
    const funding =
      venue === 'hyperliquid' ? { start_ms: 1700000000000, end_ms: 1700600000000 } : null
    return HttpResponse.json({
      market,
      venue,
      coverage: {
        candles: { start_ms: 1700000000000, end_ms: 1700600000000 },
        funding,
        oi: null,
      },
    })
  })
}

describe('FundingHeatmap', () => {
  it('renders a markets × venues grid with covered and uncovered funding cells', async () => {
    server.use(coverageHandler())
    renderWithRouter(<FundingHeatmap />)
    await waitFor(() =>
      expect(screen.getByTestId('cell-hyperliquid-SOL-PERP')).toHaveAttribute('data-covered', 'yes'),
    )
    // jupiter has no funding data — a first-class "no funding data" cell, not blank.
    expect(screen.getByTestId('cell-jupiter-SOL-PERP')).toHaveAttribute('data-covered', 'no')
    expect(screen.getAllByText('no funding data').length).toBeGreaterThan(0)
  })

  it('renders a per-cell query failure as a first-class error cell, not a crash', async () => {
    server.use(
      http.get('/api/v1/data/coverage', () =>
        HttpResponse.json({ error: { code: 'venue_unavailable', message: 'nope' } }, { status: 503 }),
      ),
    )
    renderWithRouter(<FundingHeatmap />)
    await waitFor(() =>
      expect(screen.getByTestId('cell-hyperliquid-SOL-PERP')).toHaveAttribute('data-covered', 'error'),
    )
    expect(screen.getAllByText('unavailable').length).toBeGreaterThan(0)
  })
})
