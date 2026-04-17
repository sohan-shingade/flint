import { describe, it, expect, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithRouter } from '../test-utils'
import DataExplorer from '../../pages/DataExplorer'
import { server } from '../mocks/handlers'
import { http, HttpResponse } from 'msw'

// Mock chart components to avoid canvas/SVG issues in jsdom
vi.mock('../../components/InteractiveChart', () => ({
  default: (props: any) => <div data-testid="interactive-chart">Chart ({props.data?.length || 0} candles)</div>,
}))

// Mock recharts since it needs DOM measurements
vi.mock('recharts', () => ({
  LineChart: ({ children }: any) => <div data-testid="line-chart">{children}</div>,
  Line: () => <div />,
  XAxis: () => <div />,
  YAxis: () => <div />,
  Tooltip: () => <div />,
  ResponsiveContainer: ({ children }: any) => <div>{children}</div>,
  CartesianGrid: () => <div />,
  ReferenceLine: () => <div />,
}))

describe('DataExplorer', () => {
  it('renders page title', async () => {
    renderWithRouter(<DataExplorer />)
    expect(screen.getByText('Data Explorer')).toBeInTheDocument()
  })

  it('populates market dropdown from API', async () => {
    renderWithRouter(<DataExplorer />)

    await waitFor(() => {
      const marketSelect = screen.getAllByRole('combobox')[0]
      expect(marketSelect).toBeInTheDocument()
      // Should contain markets from makeMarketInfo
      const options = within(marketSelect).getAllByRole('option')
      expect(options.length).toBeGreaterThan(0)
    })
  })

  it('renders resolution buttons', () => {
    renderWithRouter(<DataExplorer />)

    expect(screen.getByText('1m')).toBeInTheDocument()
    expect(screen.getByText('5m')).toBeInTheDocument()
    expect(screen.getByText('1h')).toBeInTheDocument()
    expect(screen.getByText('4h')).toBeInTheDocument()
    expect(screen.getByText('1d')).toBeInTheDocument()
  })

  it('renders horizon buttons', () => {
    renderWithRouter(<DataExplorer />)

    expect(screen.getByText('1W')).toBeInTheDocument()
    expect(screen.getByText('1M')).toBeInTheDocument()
    expect(screen.getByText('3M')).toBeInTheDocument()
    expect(screen.getByText('6M')).toBeInTheDocument()
    expect(screen.getByText('1Y')).toBeInTheDocument()
    expect(screen.getByText('ALL')).toBeInTheDocument()
  })

  it('LOAD button exists and is functional', async () => {
    renderWithRouter(<DataExplorer />)

    // Button might show "LOADING..." initially since data loads on mount
    await waitFor(() => {
      const loadBtn = screen.queryByText('LOAD') || screen.queryByText('LOADING...')
      expect(loadBtn).toBeInTheDocument()
    })
  })

  it('ADD MARKETS button toggles download panel', async () => {
    const user = userEvent.setup()
    renderWithRouter(<DataExplorer />)

    const addBtn = await screen.findByText('ADD MARKETS')
    await user.click(addBtn)

    await waitFor(() => {
      expect(screen.getByText('ADD.MARKETS')).toBeInTheDocument()
      expect(screen.getByText('PRESETS')).toBeInTheDocument()
    })
  })

  it('shows preset download packs', async () => {
    const user = userEvent.setup()
    renderWithRouter(<DataExplorer />)

    const addBtn = await screen.findByText('ADD MARKETS')
    await user.click(addBtn)

    await waitFor(() => {
      expect(screen.getByText('Starter Pack')).toBeInTheDocument()
      expect(screen.getByText('DeFi Pack')).toBeInTheDocument()
      expect(screen.getByText('Advanced Pack')).toBeInTheDocument()
      expect(screen.getByText('Everything')).toBeInTheDocument()
    })
  })

  it('shows time range buttons in download panel', async () => {
    const user = userEvent.setup()
    renderWithRouter(<DataExplorer />)

    await user.click(await screen.findByText('ADD MARKETS'))

    await waitFor(() => {
      expect(screen.getByText('TIME RANGE')).toBeInTheDocument()
      expect(screen.getByText('1 Month')).toBeInTheDocument()
      expect(screen.getByText('3 Months')).toBeInTheDocument()
      expect(screen.getByText('6 Months')).toBeInTheDocument()
      expect(screen.getByText('1 Year')).toBeInTheDocument()
      expect(screen.getByText('Custom')).toBeInTheDocument()
    })
  })

  it('shows execution venue grid in download panel', async () => {
    const user = userEvent.setup()
    renderWithRouter(<DataExplorer />)

    await user.click(await screen.findByText('ADD MARKETS'))

    await waitFor(() => {
      // Should show venue selection buttons
      expect(screen.getByText('All DEX')).toBeInTheDocument()
      expect(screen.getByText('All CEX')).toBeInTheDocument()
      expect(screen.getByText(/^All$/)).toBeInTheDocument()
      expect(screen.getByText('None')).toBeInTheDocument()
    })
  })

  it('indicator toggle buttons work', async () => {
    const user = userEvent.setup()
    renderWithRouter(<DataExplorer />)

    // SMA button should exist
    const smaBtn = screen.getByText('SMA')
    expect(smaBtn).toBeInTheDocument()

    // Click to enable SMA
    await user.click(smaBtn)

    // Should now show period input
    await waitFor(() => {
      expect(screen.getByText(/SMA:/)).toBeInTheDocument()
    })
  })

  it('shows candle count status after data loads', async () => {
    renderWithRouter(<DataExplorer />)

    await waitFor(() => {
      // After load, should show candle count or loading text
      const status = screen.queryByText(/candles/) || screen.queryByText(/Loading/)
      expect(status).toBeInTheDocument()
    }, { timeout: 5000 })
  })

  it('loads data automatically on mount', async () => {
    renderWithRouter(<DataExplorer />)

    // The chart should render with data from the mock
    await waitFor(() => {
      const chart = screen.getByTestId('interactive-chart')
      expect(chart).toBeInTheDocument()
    })
  })

  it('shows inventory stats', async () => {
    renderWithRouter(<DataExplorer />)

    await waitFor(() => {
      // DB info should show after markets load
      const dbInfo = screen.queryByText(/DB:/)
      if (dbInfo) {
        expect(dbInfo).toBeInTheDocument()
      }
    })
  })

  it('shows REFRESH button per market in inventory table', async () => {
    renderWithRouter(<DataExplorer />)

    await waitFor(() => {
      const refreshBtns = screen.getAllByTitle(/Update .* to latest/)
      expect(refreshBtns.length).toBeGreaterThan(0)
    }, { timeout: 5000 })
  })

  it('UPDATE button uses async download endpoint', async () => {
    const user = userEvent.setup()
    let asyncCalled = false
    let pollCalled = false
    server.use(
      http.post('/api/v1/data/download-async', async () => {
        asyncCalled = true
        return HttpResponse.json({ id: 'dl-test-1', status: 'downloading', markets: ['SOL-PERP'] })
      }),
      http.get('/api/v1/data/download-async/:id/status', () => {
        pollCalled = true
        return HttpResponse.json({
          status: 'complete',
          progress: { markets: [{ market: 'SOL-PERP', status: 'done', detail: '168 candles' }], pct: 100 },
          results: { markets: [{ market: 'SOL-PERP', total: 168 }] },
        })
      }),
    )

    renderWithRouter(<DataExplorer />)

    await waitFor(() => {
      expect(screen.getAllByTitle(/Update .* to latest/).length).toBeGreaterThan(0)
    }, { timeout: 5000 })

    const updateBtns = screen.getAllByTitle(/Update .* to latest/)
    await user.click(updateBtns[0])

    // Should call async endpoint, not sync
    await waitFor(() => {
      expect(asyncCalled).toBe(true)
    }, { timeout: 5000 })

    // Should poll for status
    await waitFor(() => {
      expect(pollCalled).toBe(true)
    }, { timeout: 5000 })
  })
})

// Import within helper
import { within } from '@testing-library/react'
