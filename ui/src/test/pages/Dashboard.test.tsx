import { describe, it, expect } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { renderWithRouter } from '../test-utils'
import Dashboard from '../../pages/Dashboard'

// Mock AsciiFire to avoid canvas issues in jsdom
vi.mock('../../components/AsciiFire', () => ({
  default: () => <div data-testid="ascii-fire">fire</div>,
}))

describe('Dashboard', () => {
  it('renders the page title', async () => {
    renderWithRouter(<Dashboard />)
    expect(screen.getByText('Flint')).toBeInTheDocument()
  })

  it('shows health status as ONLINE when API is healthy', async () => {
    renderWithRouter(<Dashboard />)

    await waitFor(() => {
      expect(screen.getByText('ONLINE')).toBeInTheDocument()
    })
  })

  it('displays market count from API', async () => {
    renderWithRouter(<Dashboard />)

    await waitFor(() => {
      // We have 5 markets from makeMarketInfo()
      expect(screen.getByText('5')).toBeInTheDocument()
    })
  })

  it('displays total candle count', async () => {
    renderWithRouter(<Dashboard />)

    await waitFor(() => {
      // 5 markets x 2000 candles each = 10,000
      expect(screen.getByText('10,000')).toBeInTheDocument()
    })
  })

  it('renders navigation links to main pages', async () => {
    renderWithRouter(<Dashboard />)

    await waitFor(() => {
      // Dashboard has quick-action links
      const links = screen.getAllByRole('link')
      expect(links.length).toBeGreaterThan(0)
    })
  })

  it('shows DRIFT as the protocol', async () => {
    renderWithRouter(<Dashboard />)
    expect(screen.getByText('DRIFT')).toBeInTheDocument()
  })
})
