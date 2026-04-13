import { describe, it, expect, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithRouter } from '../test-utils'
import PaperTrading from '../../pages/PaperTrading'
import { server } from '../mocks/handlers'
import { http, HttpResponse } from 'msw'

// Mock EquityCurve to avoid chart rendering issues
vi.mock('../../components/EquityCurve', () => ({
  default: (props: any) => <div data-testid="equity-curve">Equity Curve ({props.equity?.length || 0} points)</div>,
}))

describe('PaperTrading', () => {
  it('renders page title', async () => {
    renderWithRouter(<PaperTrading />)
    expect(screen.getByText('Paper')).toBeInTheDocument()
  })

  it('shows sidebar with strategy sessions from portfolio API', async () => {
    renderWithRouter(<PaperTrading />)

    await waitFor(() => {
      expect(screen.getByText('MA Crossover')).toBeInTheDocument()
      expect(screen.getByText('RSI')).toBeInTheDocument()
    })
  })

  it('shows session count in sidebar header', async () => {
    renderWithRouter(<PaperTrading />)

    await waitFor(() => {
      expect(screen.getByText('2')).toBeInTheDocument() // 2 sessions
    })
  })

  it('shows empty state when no sessions exist', async () => {
    server.use(
      http.get('/api/v1/paper/portfolio', () => {
        return HttpResponse.json({
          total_equity: 0,
          total_pnl: 0,
          total_initial_capital: 0,
          active_sessions: 0,
          total_sessions: 0,
          per_strategy: [],
        })
      })
    )

    renderWithRouter(<PaperTrading />)

    await waitFor(() => {
      expect(screen.getByText(/NO.SESSIONS/i)).toBeInTheDocument()
    })
  })

  it('empty state has a link to BacktestLab', async () => {
    server.use(
      http.get('/api/v1/paper/portfolio', () => {
        return HttpResponse.json({
          total_equity: 0, total_pnl: 0, total_initial_capital: 0,
          active_sessions: 0, total_sessions: 0, per_strategy: [],
        })
      })
    )

    renderWithRouter(<PaperTrading />)

    await waitFor(() => {
      const link = screen.getByText(/DEPLOY_FROM_LAB/i)
      expect(link).toBeInTheDocument()
      expect(link.closest('a')).toHaveAttribute('href', '/backtest')
    })
  })

  it('auto-selects first live session and shows detail panel', async () => {
    renderWithRouter(<PaperTrading />)

    await waitFor(() => {
      expect(screen.getByText('MA Crossover')).toBeInTheDocument()
    })

    await waitFor(() => {
      expect(screen.getByText('EQUITY')).toBeInTheDocument()
      expect(screen.getByText('REALIZED.PNL')).toBeInTheDocument()
    })
  })

  it('STOP button shows confirmation on first click', async () => {
    const user = userEvent.setup()
    renderWithRouter(<PaperTrading />)

    await waitFor(() => {
      expect(screen.getByText('STOP')).toBeInTheDocument()
    })

    await user.click(screen.getByText('STOP'))

    expect(screen.getByText('CONFIRM STOP?')).toBeInTheDocument()
    expect(screen.getByText('cancel')).toBeInTheDocument()
  })

  it('KILL button shows confirmation on first click', async () => {
    const user = userEvent.setup()
    renderWithRouter(<PaperTrading />)

    await waitFor(() => {
      expect(screen.getByText('KILL')).toBeInTheDocument()
    })

    await user.click(screen.getByText('KILL'))

    expect(screen.getByText('CONFIRM KILL?')).toBeInTheDocument()
  })

  it('cancel button resets confirmation state', async () => {
    const user = userEvent.setup()
    renderWithRouter(<PaperTrading />)

    await waitFor(() => {
      expect(screen.getByText('STOP')).toBeInTheDocument()
    })

    await user.click(screen.getByText('STOP'))
    expect(screen.getByText('CONFIRM STOP?')).toBeInTheDocument()

    await user.click(screen.getByText('cancel'))
    expect(screen.getByText('STOP')).toBeInTheDocument()
  })

  it('DEPLOY.STRATEGY panel expands and shows form fields', async () => {
    const user = userEvent.setup()
    renderWithRouter(<PaperTrading />)

    const deployBtn = await screen.findByText('DEPLOY.STRATEGY')
    await user.click(deployBtn)

    // After fix: panel should expand without crashing
    await waitFor(() => {
      expect(screen.getByText('STRATEGY')).toBeInTheDocument()
      expect(screen.getByText('MARKET')).toBeInTheDocument()
    })
  })

  it('deploy panel populates strategy and market dropdowns', async () => {
    const user = userEvent.setup()
    renderWithRouter(<PaperTrading />)

    const deployBtn = await screen.findByText('DEPLOY.STRATEGY')
    await user.click(deployBtn)

    await waitFor(() => {
      // Strategy dropdown should contain options from API
      // Use getAllByText since "MA Crossover" appears in sidebar too
      const matches = screen.getAllByText('MA Crossover')
      expect(matches.length).toBeGreaterThanOrEqual(2) // sidebar + dropdown option
    })

    await waitFor(() => {
      // Markets should be string names now (after fix), not objects
      const selects = screen.getAllByRole('combobox')
      expect(selects.length).toBeGreaterThanOrEqual(2)
    })
  })

  it('deploy button calls POST /api/v1/paper/start', async () => {
    const user = userEvent.setup()
    let postCalled = false
    server.use(
      http.post('/api/v1/paper/start', () => {
        postCalled = true
        return HttpResponse.json({ session_id: 'new-sess', status: 'running' })
      })
    )

    renderWithRouter(<PaperTrading />)

    const deployToggle = await screen.findByText('DEPLOY.STRATEGY')
    await user.click(deployToggle)

    await waitFor(() => {
      expect(screen.getByText('> DEPLOY')).toBeInTheDocument()
    })

    await user.click(screen.getByText('> DEPLOY'))

    await waitFor(() => {
      expect(postCalled).toBe(true)
    })
  })

  it('shows trade history table when trades exist', async () => {
    renderWithRouter(<PaperTrading />)

    await waitFor(() => {
      expect(screen.getByText('TRADE.HISTORY')).toBeInTheDocument()
    })

    await waitFor(() => {
      expect(screen.getByText(/fills/)).toBeInTheDocument()
    })
  })

  it('shows open positions section when positions exist', async () => {
    renderWithRouter(<PaperTrading />)

    // Wait for session detail to fully load (polled via useSessionStatus)
    await waitFor(() => {
      expect(screen.getByText('EQUITY')).toBeInTheDocument()
    })

    // Positions section and LONG label should appear from mock data
    await waitFor(() => {
      expect(screen.getByText('OPEN.POSITIONS')).toBeInTheDocument()
    }, { timeout: 5000 })

    await waitFor(() => {
      const longs = screen.getAllByText('LONG')
      expect(longs.length).toBeGreaterThanOrEqual(1)
    }, { timeout: 5000 })
  })

  it('displays user-friendly error message when API is down', async () => {
    server.use(
      http.get('/api/v1/paper/portfolio', () => {
        return HttpResponse.error()
      })
    )

    renderWithRouter(<PaperTrading />)

    await waitFor(() => {
      // After fix: should show friendly message, not raw TypeError
      const errorEl = screen.getByText(/API ERROR/i)
      expect(errorEl).toBeInTheDocument()
      expect(errorEl.textContent).toContain('Cannot connect to server')
    })
  })
})
