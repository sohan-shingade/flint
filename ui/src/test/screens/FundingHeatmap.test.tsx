import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor } from '@testing-library/react'
import { renderWithRouter } from '../test-utils'
import { server } from '../mocks/handlers'
import FundingHeatmap from '../../screens/FundingHeatmap'
import type { FundingLab } from '../../api/types'

// Hand-authored lab payload mirroring services.funding_lab (D26).
const LAB: FundingLab = {
  markets: ['SOL-PERP'],
  venues: ['hyperliquid', 'binance'],
  rate_type: 'predicted',
  requested_range: { start_ms: 0, end_ms: 3600000 },
  effective_range: { start_ms: 0, end_ms: 3600000 },
  cells: {
    'hyperliquid/SOL-PERP': {
      venue: 'hyperliquid',
      market: 'SOL-PERP',
      n: 3,
      mean_hourly: 0.0002,
      annualized: 1.752,
      interval_s: 3600,
      settlements_per_year: 8760,
    },
    'binance/SOL-PERP': null,
  },
  dislocation: { 'SOL-PERP': { ts: 0, venue: 'hyperliquid', dislocation_hourly: 0.0004 } },
  fidelity: [],
}

const EMPTY_LAB: FundingLab = {
  markets: ['SOL-PERP', 'BTC-PERP', 'ETH-PERP'],
  venues: ['hyperliquid', 'binance', 'okx'],
  rate_type: 'predicted',
  requested_range: { start_ms: 0, end_ms: 3600000 },
  effective_range: { start_ms: 0, end_ms: 3600000 },
  cells: {},
  dislocation: {},
  fidelity: [],
}

describe('FundingHeatmap (lab values)', () => {
  it('renders real annualized carry + mean rate and the widest dislocation headline', async () => {
    server.use(http.get('/api/v1/lab/funding', () => HttpResponse.json(LAB)))
    renderWithRouter(<FundingHeatmap />)
    await waitFor(() =>
      expect(screen.getByTestId('fund-hyperliquid-SOL-PERP')).toHaveAttribute('data-observed', 'yes'),
    )
    expect(screen.getByText(/175\.2%\/yr/)).toBeInTheDocument()
    // widest cross-venue dislocation headline for the market
    expect(screen.getByText(/widest: hyperliquid/)).toBeInTheDocument()
    // a venue with no observations is a first-class null cell, not blank
    expect(screen.getByTestId('fund-binance-SOL-PERP')).toHaveAttribute('data-observed', 'no')
    expect(screen.getByText('no observations')).toBeInTheDocument()
  })

  it('falls back to the coverage view when the lab has no observations', async () => {
    server.use(
      http.get('/api/v1/lab/funding', () => HttpResponse.json(EMPTY_LAB)),
      http.get('/api/v1/data/coverage', ({ request }) => {
        const url = new URL(request.url)
        return HttpResponse.json({
          market: url.searchParams.get('market'),
          venue: url.searchParams.get('venue'),
          coverage: { candles: null, funding: { start_ms: 0, end_ms: 3600000 }, oi: null },
        })
      }),
    )
    renderWithRouter(<FundingHeatmap />)
    await waitFor(() =>
      expect(screen.getByText(/no funding observations in this window/)).toBeInTheDocument(),
    )
    // the fallback coverage grid renders funding-data coverage cells
    await waitFor(() =>
      expect(screen.getByTestId('cov-hyperliquid-SOL-PERP')).toHaveAttribute('data-covered', 'yes'),
    )
  })

  it('renders a lab query fault as a first-class error state', async () => {
    server.use(
      http.get('/api/v1/lab/funding', () =>
        HttpResponse.json({ error: { code: 'internal', message: 'lab boom' } }, { status: 500 }),
      ),
    )
    renderWithRouter(<FundingHeatmap />)
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.getByText(/lab boom/)).toBeInTheDocument()
  })
})
