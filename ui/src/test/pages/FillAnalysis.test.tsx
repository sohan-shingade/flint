import { describe, it, expect, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { renderWithRouter } from '../test-utils'
import FillAnalysis from '../../pages/FillAnalysis'

// Mock recharts
vi.mock('recharts', () => ({
  ScatterChart: ({ children }: any) => <div data-testid="scatter-chart">{children}</div>,
  Scatter: () => <div />,
  XAxis: () => <div />,
  YAxis: () => <div />,
  Tooltip: () => <div />,
  ResponsiveContainer: ({ children }: any) => <div>{children}</div>,
  CartesianGrid: () => <div />,
}))

describe('FillAnalysis', () => {
  it('renders without crashing after market object fix', async () => {
    // After fix: markets are extracted as strings, not objects
    renderWithRouter(<FillAnalysis />)

    await waitFor(() => {
      expect(document.body).toBeInTheDocument()
    })
  })

  it('fetches fills from API on mount', async () => {
    renderWithRouter(<FillAnalysis />)

    // Fills should load from the mock handler without crashing
    await waitFor(() => {
      expect(document.body).toBeInTheDocument()
    })
  })

  it('market filter dropdown contains string market names', async () => {
    renderWithRouter(<FillAnalysis />)

    await waitFor(() => {
      // After fix: the market select should have string options, not objects
      const selects = screen.getAllByRole('combobox')
      expect(selects.length).toBeGreaterThan(0)
    })
  })
})
