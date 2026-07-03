import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithRouter } from '../test-utils'
import LiveMonitor from '../../screens/LiveMonitor'
import type { PaperSnapshot } from '../../api/types'

// Controllable mock WebSocket — the hook opens one; tests drive its frames.
class MockWebSocket {
  static last: MockWebSocket | null = null
  url: string
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor(url: string) {
    this.url = url
    MockWebSocket.last = this
    setTimeout(() => this.onopen?.(), 0)
  }
  send() {}
  close() {
    this.onclose?.()
  }
  emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) })
  }
}

const SNAP: PaperSnapshot = {
  run_id: 'p1',
  kind: 'paper',
  status: 'running',
  positions: [
    { market: 'SOL-PERP', venue: 'hyperliquid', size: 10, entry_price: 150, mark_price: 155, unrealized_pnl: 50 },
  ],
  funding_accrued: -3.25,
  liq_distances_pct: { 'SOL-PERP': 0.42 },
  drift: { fill_slippage_bps: 1.5, funding_timing: 'aligned' },
  alerts: [],
  final_equity: 100050,
}

beforeEach(() => {
  vi.stubGlobal('WebSocket', MockWebSocket)
})
afterEach(() => {
  vi.unstubAllGlobals()
  MockWebSocket.last = null
})

async function connect(runId = 'p1') {
  renderWithRouter(<LiveMonitor />)
  await userEvent.type(screen.getByLabelText('run id'), runId)
  await userEvent.click(screen.getByRole('button', { name: 'connect' }))
  await waitFor(() => expect(MockWebSocket.last).not.toBeNull())
}

describe('LiveMonitor', () => {
  it('renders positions, funding accrual, liq distance and the drift attribution table', async () => {
    await connect()
    act(() => MockWebSocket.last!.emit(SNAP))
    // SOL-PERP appears in both the positions row and the liq-distance row.
    await waitFor(() => expect(screen.getAllByText('SOL-PERP').length).toBeGreaterThan(0))
    // funding accrual + drift table present
    expect(screen.getByText('funding accrued')).toBeInTheDocument()
    expect(screen.getByTestId('drift-table')).toBeInTheDocument()
    expect(screen.getByText('fill_slippage_bps')).toBeInTheDocument()
    // liq distance rendered
    expect(screen.getByText('liquidation distance')).toBeInTheDocument()
  })

  it('renders empty/degraded blocks first-class when the snapshot is bare', async () => {
    await connect()
    act(() =>
      MockWebSocket.last!.emit({
        run_id: 'p1',
        kind: 'paper',
        status: 'running',
        positions: [],
        funding_accrued: null,
        liq_distances_pct: {},
        drift: {},
        alerts: [],
        final_equity: null,
      }),
    )
    await waitFor(() => expect(screen.getByText('no open positions.')).toBeInTheDocument())
    expect(screen.getByText('no liquidation exposure.')).toBeInTheDocument()
    expect(screen.getByText('no drift recorded.')).toBeInTheDocument()
  })

  it('surfaces a structured stream error (bad token close) as a first-class error', async () => {
    await connect()
    act(() => MockWebSocket.last!.emit({ error: { code: 'unauthorized', message: 'bad token' } }))
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.getByText(/bad token/)).toBeInTheDocument()
  })
})
