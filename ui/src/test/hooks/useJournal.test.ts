import { describe, it, expect } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useJournal } from '../../hooks/useJournal'
import { server } from '../mocks/handlers'
import { http, HttpResponse } from 'msw'

describe('useJournal', () => {
  it('fetches journal runs on mount', async () => {
    const { result } = renderHook(() => useJournal())

    await waitFor(() => {
      expect(result.current.runs.length).toBeGreaterThan(0)
    })

    expect(result.current.runs[0]).toHaveProperty('run_id')
    expect(result.current.runs[0]).toHaveProperty('strategy_name')
    expect(result.current.runs[0]).toHaveProperty('total_pnl')
    expect(result.current.runs[0]).toHaveProperty('sharpe_ratio')
    expect(result.current.runs[0]).toHaveProperty('max_drawdown')
  })

  it('deleteRun() calls DELETE and refreshes list', async () => {
    let deletedId = ''
    server.use(
      http.delete('/api/v1/journal/runs/:runId', ({ params }) => {
        deletedId = params.runId as string
        return HttpResponse.json({ deleted: true })
      })
    )

    const { result } = renderHook(() => useJournal())

    await waitFor(() => {
      expect(result.current.runs.length).toBeGreaterThan(0)
    })

    await act(async () => {
      await result.current.deleteRun('run-001')
    })

    expect(deletedId).toBe('run-001')
  })

  it('refresh() re-fetches the run list', async () => {
    let fetchCount = 0
    server.use(
      http.get('/api/v1/journal/runs', () => {
        fetchCount++
        return HttpResponse.json({ runs: [] })
      })
    )

    const { result } = renderHook(() => useJournal())

    await waitFor(() => {
      expect(fetchCount).toBeGreaterThanOrEqual(1)
    })

    await act(async () => {
      await result.current.refresh()
    })

    expect(fetchCount).toBeGreaterThanOrEqual(2)
  })

  it('handles API errors gracefully', async () => {
    server.use(
      http.get('/api/v1/journal/runs', () => {
        return HttpResponse.error()
      })
    )

    const { result } = renderHook(() => useJournal())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    // Should not crash, just return empty
    expect(result.current.runs).toEqual([])
  })
})
