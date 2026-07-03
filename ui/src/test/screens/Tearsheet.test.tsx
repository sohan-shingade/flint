import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithRouter } from '../test-utils'
import { server } from '../mocks/handlers'
import Tearsheet, { TearsheetView } from '../../screens/Tearsheet'
import type { BacktestResult } from '../../api/types'

// Hand-authored fixtures mirroring flint/services/backtest.py _summary (D26).
const OK_RESULT: BacktestResult = {
  verdict: 'ok',
  strategy: 'ma-crossover',
  universe: ['SOL-PERP'],
  venues: ['hyperliquid'],
  fill_mode: 'auto',
  requested_range: { start_ms: 1700000000000, end_ms: 1700600000000 },
  effective_range: { start_ms: 1700000000000, end_ms: 1700600000000 },
  clipped: false,
  metrics: {
    sharpe: 1.23,
    annualized_sharpe: 4.5,
    sortino: 1.8,
    annualized_sortino: 6.0,
    max_drawdown: 0.12,
    mean_bar_return: 0.001,
    n_returns: 168,
    annualization_factor: 24.49,
    evaluated_start_ts: 1700000000000,
    evaluated_end_ts: 1700600000000,
  },
  deflated_sharpe: null,
  n_trials: 0,
  win_rate: 0.55,
  cost: { funding: -12.5, trading_pnl: 340.0, fees: -8.2, slippage: -4.1, funding_settlements: 7 },
  equity_curve: [100000, 100120, 99980, 100340],
  rejections: [],
  fidelity_lines: ['SOL-PERP hyperliquid: Tier A (recorded book) 100%'],
  notes: ['single un-tuned backtest'],
  timing: {},
}

const REJECTED_RESULT: BacktestResult = {
  verdict: 'rejected',
  strategy: 'funding-harvest',
  rejected: {
    code: 'funding_gap',
    message: 'Funding data missing — backtest rejected (funding is a hard requirement).',
    missing: ['hyperliquid/BTC-PERP'],
    available: { 'hyperliquid/BTC-PERP': { start_ms: 1700000000000, end_ms: 1700100000000 } },
    hint: 'Re-run over a covered range, or pass a clip-to-coverage mode.',
  },
}

describe('TearsheetView (ok verdict)', () => {
  it('always shows raw Sharpe alongside a DSR labeled n/a with the trial count', () => {
    renderWithRouter(<TearsheetView result={OK_RESULT} />)
    // raw Sharpe present and labeled "raw"
    expect(screen.getByText('sharpe (raw)')).toBeInTheDocument()
    expect(screen.getByText('1.23')).toBeInTheDocument()
    // DSR is honest: a single un-tuned run is n/a with N trials, never invented
    expect(screen.getByText('n/a (0 trials)')).toBeInTheDocument()
  })

  it('labels the funding cost line "funding (settled, cumulative)" with settlement count', () => {
    renderWithRouter(<TearsheetView result={OK_RESULT} />)
    expect(screen.getByText('funding (settled, cumulative)')).toBeInTheDocument()
    expect(screen.getByText('7 settlements')).toBeInTheDocument()
  })

  it('shows the annualization factor and effective range', () => {
    renderWithRouter(<TearsheetView result={OK_RESULT} />)
    expect(screen.getByText('annualization ×')).toBeInTheDocument()
    expect(screen.getByText('24.49')).toBeInTheDocument()
    expect(screen.getByText('effective range')).toBeInTheDocument()
  })

  it('renders the fill-fidelity tiers and the equity curve', () => {
    renderWithRouter(<TearsheetView result={OK_RESULT} />)
    expect(screen.getByText(/Tier A \(recorded book\)/)).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'equity curve' })).toBeInTheDocument()
  })
})

describe('TearsheetView (rejected verdict)', () => {
  it('renders the funding-gap rejection as a first-class state (missing legs + fix)', () => {
    renderWithRouter(<TearsheetView result={REJECTED_RESULT} />)
    expect(screen.getByText(/rejected · funding_gap/)).toBeInTheDocument()
    expect(screen.getByText('· hyperliquid/BTC-PERP')).toBeInTheDocument()
    expect(screen.getByText(/Re-run over a covered range/)).toBeInTheDocument()
  })
})

describe('Tearsheet (fetch flow)', () => {
  it('loads a run by id from the API and shows its tearsheet', async () => {
    server.use(
      http.get('/api/v1/backtests/:runId', () => HttpResponse.json(OK_RESULT)),
    )
    renderWithRouter(<Tearsheet />)
    await userEvent.type(screen.getByLabelText('run id'), 'run-abc')
    await userEvent.click(screen.getByRole('button', { name: 'load' }))
    await waitFor(() => expect(screen.getByText('sharpe (raw)')).toBeInTheDocument())
  })

  it('renders a faulted response as a first-class error state, never a blank screen', async () => {
    server.use(
      http.get('/api/v1/backtests/:runId', () =>
        HttpResponse.json(
          { error: { code: 'not_found', message: "run 'missing' has no result yet", hint: 'poll status' } },
          { status: 404 },
        ),
      ),
    )
    renderWithRouter(<Tearsheet />)
    await userEvent.type(screen.getByLabelText('run id'), 'missing')
    await userEvent.click(screen.getByRole('button', { name: 'load' }))
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.getByText(/not_found/)).toBeInTheDocument()
    expect(screen.getByText(/poll status/)).toBeInTheDocument()
  })
})
