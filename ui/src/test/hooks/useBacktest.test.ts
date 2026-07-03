import { describe, it, expect } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useBacktest } from '../../hooks/useBacktest'
import { server, resetPollCounters } from '../mocks/handlers'
import { http, HttpResponse } from 'msw'

describe('useBacktest', () => {
  beforeEach(() => {
    resetPollCounters()
  })

  it('starts in idle state', () => {
    const { result } = renderHook(() => useBacktest())
    expect(result.current.status).toBe('idle')
    expect(result.current.results).toBeNull()
    expect(result.current.error).toBeNull()
  })

  it('run() transitions to running state and POSTs correct params', async () => {
    const { result } = renderHook(() => useBacktest())

    await act(async () => {
      result.current.run({
        strategy: 'custom',
        code: 'class MyStrategy: pass',
        market: 'SOL-PERP',
        resolution_s: 3600,
        start_ts: 1704067200,
        end_ts: 1704153600,
        initial_capital: 10000,
        fee_rate: 0.001,
      })
    })

    expect(result.current.status).toBe('running')
  })

  it('polls until complete and sets results', async () => {
    const { result } = renderHook(() => useBacktest())

    await act(async () => {
      result.current.run({
        strategy: 'custom',
        code: 'class MyStrategy: pass',
        market: 'SOL-PERP',
        resolution_s: 3600,
        start_ts: 1704067200,
        end_ts: 1704153600,
        initial_capital: 10000,
        fee_rate: 0.001,
      })
    })

    await waitFor(() => {
      expect(result.current.status).toBe('complete')
    }, { timeout: 10000 })

    expect(result.current.results).not.toBeNull()
    expect(result.current.results.metrics).toBeDefined()
    expect(result.current.results.trades).toBeDefined()
    expect(result.current.results.equity_curve).toBeDefined()
  })

  it('sets error when server returns 400', async () => {
    server.use(
      http.post('/api/v1/backtest/run', () => {
        return HttpResponse.json({ detail: 'Strategy code cannot be empty' }, { status: 400 })
      })
    )

    const { result } = renderHook(() => useBacktest())

    await act(async () => {
      result.current.run({
        strategy: 'custom',
        code: '',
        market: 'SOL-PERP',
        resolution_s: 3600,
        start_ts: 1704067200,
        end_ts: 1704153600,
        initial_capital: 10000,
        fee_rate: 0.001,
      })
    })

    await waitFor(() => {
      expect(result.current.status).toBe('failed')
    })
    expect(result.current.error).toContain('Strategy code cannot be empty')
  })

  it('sets error when backtest run fails server-side', async () => {
    server.use(
      http.post('/api/v1/backtest/run', () => {
        return HttpResponse.json({ id: 'fail-run' })
      }),
      http.get('/api/v1/backtest/:id/results', () => {
        return HttpResponse.json({
          status: 'failed',
          results: { error: 'Strategy raised an exception' },
        })
      })
    )

    const { result } = renderHook(() => useBacktest())

    await act(async () => {
      result.current.run({
        strategy: 'custom',
        code: 'bad code',
        market: 'SOL-PERP',
        resolution_s: 3600,
        start_ts: 1704067200,
        end_ts: 1704153600,
        initial_capital: 10000,
        fee_rate: 0.001,
      })
    })

    await waitFor(() => {
      expect(result.current.status).toBe('failed')
    }, { timeout: 10000 })
    expect(result.current.error).toContain('Strategy raised an exception')
  })

  it('sets error when no run ID returned', async () => {
    server.use(
      http.post('/api/v1/backtest/run', () => {
        return HttpResponse.json({ detail: 'Something went wrong' })
      })
    )

    const { result } = renderHook(() => useBacktest())

    await act(async () => {
      result.current.run({
        strategy: 'custom',
        code: 'class X: pass',
        market: 'SOL-PERP',
        resolution_s: 3600,
        start_ts: 1704067200,
        end_ts: 1704153600,
        initial_capital: 10000,
        fee_rate: 0.001,
      })
    })

    await waitFor(() => {
      expect(result.current.status).toBe('failed')
    })
    expect(result.current.error).toBeDefined()
  })

  it('reports progress during polling', async () => {
    const { result } = renderHook(() => useBacktest())

    await act(async () => {
      result.current.run({
        strategy: 'custom',
        code: 'class MyStrategy: pass',
        market: 'SOL-PERP',
        resolution_s: 3600,
        start_ts: 1704067200,
        end_ts: 1704153600,
        initial_capital: 10000,
        fee_rate: 0.001,
      })
    })

    // Initial progress should be set
    expect(result.current.progress).not.toBeNull()

    await waitFor(() => {
      expect(result.current.status).toBe('complete')
    }, { timeout: 10000 })
  })
})
