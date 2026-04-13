import { describe, it, expect } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useOptimize } from '../../hooks/useOptimize'
import { server, resetPollCounters } from '../mocks/handlers'
import { http, HttpResponse } from 'msw'

describe('useOptimize', () => {
  beforeEach(() => {
    resetPollCounters()
  })

  it('starts in idle state', () => {
    const { result } = renderHook(() => useOptimize())
    expect(result.current.status).toBe('idle')
    expect(result.current.results).toBeNull()
    expect(result.current.error).toBeNull()
  })

  it('run() transitions to running and POSTs to optimize endpoint', async () => {
    const { result } = renderHook(() => useOptimize())

    await act(async () => {
      result.current.run({
        code: 'class MyStrat: pass',
        market: 'SOL-PERP',
        resolution_s: 3600,
        start_ts: 1704067200,
        end_ts: 1704153600,
        initial_capital: 10000,
        fee_rate: 0.001,
        metric: 'sharpe_ratio',
        trials: 50,
      })
    })

    expect(result.current.status).toBe('running')
  })

  it('polls until complete and returns optimization results', async () => {
    const { result } = renderHook(() => useOptimize())

    await act(async () => {
      result.current.run({
        code: 'class MyStrat: pass',
        market: 'SOL-PERP',
        resolution_s: 3600,
        start_ts: 1704067200,
        end_ts: 1704153600,
        initial_capital: 10000,
        fee_rate: 0.001,
        metric: 'sharpe_ratio',
        trials: 50,
      })
    })

    await waitFor(() => {
      expect(result.current.status).toBe('complete')
    }, { timeout: 10000 })

    expect(result.current.results).not.toBeNull()
    expect(result.current.results!.best_params).toBeDefined()
    expect(result.current.results!.best_value).toBeDefined()
    expect(result.current.results!.n_trials).toBe(50)
  })

  it('sets error on server failure', async () => {
    server.use(
      http.post('/api/v1/optimize/run', () => {
        return HttpResponse.json({ detail: 'No parameters to optimize' }, { status: 400 })
      })
    )

    const { result } = renderHook(() => useOptimize())

    await act(async () => {
      result.current.run({
        code: 'class MyStrat: pass',
        market: 'SOL-PERP',
        resolution_s: 3600,
        start_ts: 1704067200,
        end_ts: 1704153600,
        initial_capital: 10000,
        fee_rate: 0.001,
        metric: 'sharpe_ratio',
        trials: 50,
      })
    })

    await waitFor(() => {
      expect(result.current.status).toBe('failed')
    })
    expect(result.current.error).toContain('No parameters to optimize')
  })
})
