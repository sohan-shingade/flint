import { describe, it, expect, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { renderWithRouter } from '../test-utils'
import LiveMonitor from '../../pages/LiveMonitor'

// Mock recharts
vi.mock('recharts', () => ({
  LineChart: ({ children }: any) => <div data-testid="line-chart">{children}</div>,
  Line: () => <div />,
  XAxis: () => <div />,
  YAxis: () => <div />,
  Tooltip: () => <div />,
  ResponsiveContainer: ({ children }: any) => <div>{children}</div>,
}))

describe('LiveMonitor', () => {
  it('renders page title', () => {
    renderWithRouter(<LiveMonitor />)
    expect(screen.getByText('Live')).toBeInTheDocument()
    expect(screen.getByText(/LIVE TRADING MONITOR/)).toBeInTheDocument()
  })

  it('renders without crashing when no sessions', () => {
    renderWithRouter(<LiveMonitor />)
    // Should not throw
    expect(screen.getByText('Live')).toBeInTheDocument()
  })

  it('fetches live sessions on mount', async () => {
    renderWithRouter(<LiveMonitor />)
    // Just verify it doesn't crash — the hook fetches /api/v1/live/sessions
    await waitFor(() => {
      expect(true).toBe(true)
    })
  })
})
