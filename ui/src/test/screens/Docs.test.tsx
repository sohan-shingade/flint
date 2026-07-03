import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithRouter } from '../test-utils'
import Docs from '../../screens/Docs'

// Docs is static content (no API), so it renders synchronously with no handlers.
describe('Docs', () => {
  it('renders the sidebar sections and the default topic', () => {
    renderWithRouter(<Docs />)

    // Section headers from data/docs.ts.
    expect(screen.getByText('GETTING STARTED')).toBeInTheDocument()
    expect(screen.getByText('STRATEGY API')).toBeInTheDocument()
    expect(screen.getByText('RUNNING & VALIDATION')).toBeInTheDocument()

    // Default topic content shown.
    expect(screen.getByRole('heading', { name: 'Quickstart' })).toBeInTheDocument()
  })

  it('switches topics from the sidebar', async () => {
    renderWithRouter(<Docs />)
    await userEvent.click(screen.getByRole('button', { name: 'The funding gate' }))
    expect(screen.getByRole('heading', { name: 'The funding gate' })).toBeInTheDocument()
    expect(screen.getByText(/never silently filled/)).toBeInTheDocument()
  })
})
