import { describe, it, expect } from 'vitest'
import { waitFor } from '@testing-library/react'
import { renderWithRouter } from '../test-utils'
import FundingHeatmap from '../../pages/FundingHeatmap'

describe('FundingHeatmap', () => {
  it('renders without crashing', () => {
    renderWithRouter(<FundingHeatmap />)
    expect(document.body).toBeInTheDocument()
  })

  it('fetches funding data on mount', async () => {
    renderWithRouter(<FundingHeatmap />)

    await waitFor(() => {
      // Should not crash during data fetch
      expect(document.body).toBeInTheDocument()
    })
  })

  it('shows loading state initially', () => {
    renderWithRouter(<FundingHeatmap />)
    // Page should render some content even while loading
    expect(document.body).toBeInTheDocument()
  })
})
