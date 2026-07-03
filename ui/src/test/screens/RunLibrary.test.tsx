import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithRouter } from '../test-utils'
import { server } from '../mocks/handlers'
import RunLibrary from '../../screens/RunLibrary'
import type { RunRow } from '../../api/types'

// Hand-authored run rows mirroring services.runs _row (D26).
const RUNS: RunRow[] = [
  {
    run_id: 'aaaaaaaa1111',
    strategy: 'ma-crossover',
    kind: 'backtest',
    created_ts: 1700000000000,
    effective_start_ts: 1700000000000,
    effective_end_ts: 1700600000000,
    metrics: { sharpe: 0.9 },
    seed: 0,
    engine_version: 'e1',
  },
  {
    run_id: 'bbbbbbbb2222',
    strategy: 'funding-harvest',
    kind: 'backtest',
    created_ts: 1700100000000,
    effective_start_ts: 1700100000000,
    effective_end_ts: 1700700000000,
    metrics: { sharpe: 1.7 },
    seed: 0,
    engine_version: 'e1',
  },
]

describe('RunLibrary', () => {
  it('lists runs and sorts by sharpe on header click', async () => {
    server.use(http.get('/api/v1/runs', () => HttpResponse.json({ runs: RUNS })))
    renderWithRouter(<RunLibrary />)
    await waitFor(() => expect(screen.getByTestId('run-aaaaaaaa1111')).toBeInTheDocument())

    // sort by sharpe — first click gives descending: funding-harvest (1.7) first
    await userEvent.click(screen.getByText(/^sharpe/))
    const rows = screen.getAllByTestId(/^run-/)
    expect(within(rows[0]).getByText('funding-harvest')).toBeInTheDocument()
  })

  it('compares two selected runs and shows the different-effective-range warning', async () => {
    server.use(
      http.get('/api/v1/runs', () => HttpResponse.json({ runs: RUNS })),
      http.get('/api/v1/runs/compare', () =>
        HttpResponse.json({
          run_ids: ['aaaaaaaa1111', 'bbbbbbbb2222'],
          warnings: ['runs cover different effective ranges — metrics are not directly comparable'],
          metrics: { sharpe: [0.9, 1.7] },
        }),
      ),
    )
    renderWithRouter(<RunLibrary />)
    await waitFor(() => expect(screen.getByTestId('run-aaaaaaaa1111')).toBeInTheDocument())

    await userEvent.click(screen.getByLabelText('select aaaaaaaa1111'))
    await userEvent.click(screen.getByLabelText('select bbbbbbbb2222'))
    await userEvent.click(screen.getByRole('button', { name: 'compare 2' }))

    await waitFor(() => expect(screen.getByTestId('comparison')).toBeInTheDocument())
    expect(screen.getByText(/different effective ranges/)).toBeInTheDocument()
    // metric diff row rendered
    const cmp = screen.getByTestId('comparison')
    expect(within(cmp).getByText('sharpe')).toBeInTheDocument()
  })

  it('renders an empty library as a first-class note, not a blank screen', async () => {
    server.use(http.get('/api/v1/runs', () => HttpResponse.json({ runs: [] })))
    renderWithRouter(<RunLibrary />)
    await waitFor(() => expect(screen.getByText(/no runs yet/)).toBeInTheDocument())
  })
})
