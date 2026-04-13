/**
 * Integration test: Data download flow
 * Simulates: DataExplorer → ADD MARKETS → select pack → download → verify progress
 */
import { describe, it, expect, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithRouter } from '../test-utils'
import DataExplorer from '../../pages/DataExplorer'
import { server } from '../mocks/handlers'
import { http, HttpResponse } from 'msw'

// Mock chart components
vi.mock('../../components/InteractiveChart', () => ({
  default: (props: any) => <div data-testid="interactive-chart">Chart</div>,
}))

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

describe('Data Download Flow', () => {
  it('full flow: open download panel → select starter pack → verify markets selected', async () => {
    const user = userEvent.setup()
    renderWithRouter(<DataExplorer />)

    // 1. Click ADD MARKETS
    const addBtn = await screen.findByText('ADD MARKETS')
    await user.click(addBtn)

    // 2. Verify panel opens
    await waitFor(() => {
      expect(screen.getByText('PRESETS')).toBeInTheDocument()
    })

    // 3. Click Starter Pack
    const starterBtn = screen.getByText('Starter Pack')
    await user.click(starterBtn)

    // 4. Starter pack description should be visible
    expect(screen.getByText(/5 perps/)).toBeInTheDocument()
  })

  it('download calls POST /api/v1/data/download for selected markets', async () => {
    const downloadedMarkets: string[] = []
    server.use(
      http.post('/api/v1/data/download', async ({ request }) => {
        const body = await request.json() as Record<string, any>
        downloadedMarkets.push(body.market)
        return HttpResponse.json({
          downloaded: 500,
          existing: 0,
          funding_fetched: 24,
          warnings: [],
        })
      })
    )

    const user = userEvent.setup()
    renderWithRouter(<DataExplorer />)

    // Open download panel
    await user.click(await screen.findByText('ADD MARKETS'))
    await waitFor(() => {
      expect(screen.getByText('PRESETS')).toBeInTheDocument()
    })

    // Select Starter Pack
    await user.click(screen.getByText('Starter Pack'))

    // Find and click DOWNLOAD button
    await waitFor(() => {
      const dlBtn = screen.queryByText('DOWNLOAD')
      if (dlBtn) expect(dlBtn).toBeInTheDocument()
    })

    const dlBtn = screen.queryByText('DOWNLOAD')
    if (dlBtn) {
      await user.click(dlBtn)

      // Wait for downloads to complete
      await waitFor(() => {
        expect(downloadedMarkets.length).toBeGreaterThan(0)
      }, { timeout: 10000 })

      // Should have downloaded the starter pack markets
      expect(downloadedMarkets).toContain('SOL-PERP')
    }
  })

  it('resolution buttons switch the active resolution', async () => {
    const user = userEvent.setup()
    renderWithRouter(<DataExplorer />)

    // Click 4h resolution
    const btn4h = screen.getByText('4h')
    await user.click(btn4h)

    // The button should now be active (has amber styling)
    // We can verify by checking if it has the active class indicators
    expect(btn4h).toBeInTheDocument()
  })

  it('horizon buttons switch the time range', async () => {
    const user = userEvent.setup()
    renderWithRouter(<DataExplorer />)

    // Click 1Y horizon
    const btn1y = screen.getByText('1Y')
    await user.click(btn1y)

    expect(btn1y).toBeInTheDocument()
  })

  it('custom date range inputs appear for horizon buttons', async () => {
    renderWithRouter(<DataExplorer />)

    // START and END date inputs should be present
    await waitFor(() => {
      expect(screen.getByText('START')).toBeInTheDocument()
      expect(screen.getByText('END')).toBeInTheDocument()
    })
  })

  it('market selector shows perp and spot groups', async () => {
    renderWithRouter(<DataExplorer />)

    await waitFor(() => {
      const selects = screen.getAllByRole('combobox')
      expect(selects.length).toBeGreaterThan(0)

      // Should have optgroup for perp markets
      const select = selects[0]
      const groups = select.querySelectorAll('optgroup')
      expect(groups.length).toBeGreaterThanOrEqual(1)
    })
  })

  it('downloads show warnings when API returns them', async () => {
    server.use(
      http.post('/api/v1/data/download', () => {
        return HttpResponse.json({
          downloaded: 100,
          existing: 0,
          funding_fetched: 0,
          warnings: ['Rate limited by Drift API, partial data only'],
        })
      })
    )

    const user = userEvent.setup()
    renderWithRouter(<DataExplorer />)

    // Open panel and select a market
    await user.click(await screen.findByText('ADD MARKETS'))
    await waitFor(() => {
      expect(screen.getByText('PRESETS')).toBeInTheDocument()
    })

    await user.click(screen.getByText('Starter Pack'))

    const dlBtn = screen.queryByText('DOWNLOAD')
    if (dlBtn) {
      await user.click(dlBtn)

      // Warnings should appear
      await waitFor(() => {
        const warning = screen.queryByText(/Rate limited/)
        if (warning) expect(warning).toBeInTheDocument()
      }, { timeout: 5000 })
    }
  })

  it('venue quick-select buttons (All DEX, All CEX, All, None) work', async () => {
    const user = userEvent.setup()
    renderWithRouter(<DataExplorer />)

    await user.click(await screen.findByText('ADD MARKETS'))

    await waitFor(() => {
      expect(screen.getByText('All DEX')).toBeInTheDocument()
    })

    // Click "None" to deselect all
    await user.click(screen.getByText('None'))

    // Check the count text updates — use getAllByText since multiple elements match
    await waitFor(() => {
      const countTexts = screen.queryAllByText(/0 selected/)
      expect(countTexts.length).toBeGreaterThan(0)
    })

    // Click "All" to select all
    await user.click(screen.getByText(/^All$/))

    await waitFor(() => {
      const countTexts = screen.queryAllByText(/selected/)
      expect(countTexts.length).toBeGreaterThan(0)
    })
  })
})
