import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor } from '@testing-library/react'
import { renderWithRouter } from '../test-utils'
import { server } from '../mocks/handlers'
import Lab from '../../screens/Lab'

// Hand-authored template registry mirroring GET /templates (app.templates, D26).
const TEMPLATES = {
  templates: [
    { name: 'ma_cross', category: 'technical', summary: 'moving-average cross', is_ml: false, params: { fast: 10, slow: 30 } },
    { name: 'funding_harvest', category: 'funding', summary: 'harvest funding', is_ml: false, params: { deadband: 0.0001 } },
  ],
  executable_venues: ['hyperliquid'],
}

describe('Lab (template mode)', () => {
  it('lists templates grouped by category and auto-generates the param editor', async () => {
    server.use(http.get('/api/v1/templates', () => HttpResponse.json(TEMPLATES)))
    renderWithRouter(<Lab />)

    // Templates fetched and rendered as pickers.
    await waitFor(() => expect(screen.getByTestId('tmpl-ma_cross')).toBeInTheDocument())
    expect(screen.getByTestId('tmpl-funding_harvest')).toBeInTheDocument()

    // Category headers present.
    expect(screen.getByText('technical')).toBeInTheDocument()
    expect(screen.getByText('funding')).toBeInTheDocument()

    // First template auto-selected: its summary + params render as editable inputs.
    expect(screen.getByText('moving-average cross')).toBeInTheDocument()
    expect(screen.getByLabelText('fast')).toHaveValue(10)
    expect(screen.getByLabelText('slow')).toHaveValue(30)

    // Shared run-config is present.
    expect(screen.getByLabelText('universe')).toHaveValue('BTC-PERP,ETH-PERP,SOL-PERP')
    expect(screen.getByRole('button', { name: /run backtest/i })).toBeInTheDocument()
  })

  it('surfaces a first-class error when the template registry faults', async () => {
    server.use(
      http.get('/api/v1/templates', () =>
        HttpResponse.json({ error: { code: 'error', message: 'registry unavailable' } }, { status: 500 }),
      ),
    )
    renderWithRouter(<Lab />)
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.getByText(/registry unavailable/)).toBeInTheDocument()
  })
})
