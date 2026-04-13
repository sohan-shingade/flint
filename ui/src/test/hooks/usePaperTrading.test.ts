import { describe, it, expect } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import {
  usePaperPortfolio,
  useSessionStatus,
  useSessionTrades,
  stopSession,
  killSession,
} from '../../hooks/usePaperTrading'
import { server } from '../mocks/handlers'
import { http, HttpResponse } from 'msw'

describe('usePaperPortfolio', () => {
  it('fetches portfolio on mount', async () => {
    const { result } = renderHook(() => usePaperPortfolio(60000)) // long interval to avoid re-poll

    await waitFor(() => {
      expect(result.current.portfolio).not.toBeNull()
    })

    expect(result.current.portfolio!.per_strategy).toHaveLength(2)
    expect(result.current.portfolio!.active_sessions).toBe(1)
    expect(result.current.error).toBeNull()
  })

  it('sets error on network failure', async () => {
    server.use(
      http.get('/api/v1/paper/portfolio', () => {
        return HttpResponse.error()
      })
    )

    const { result } = renderHook(() => usePaperPortfolio(60000))

    await waitFor(() => {
      expect(result.current.error).not.toBeNull()
    })
  })
})

describe('useSessionStatus', () => {
  it('fetches session status when sessionId is provided', async () => {
    const { result } = renderHook(() => useSessionStatus('sess-001', 60000))

    await waitFor(() => {
      expect(result.current).not.toBeNull()
    })

    expect(result.current!.session_id).toBe('sess-001')
    expect(result.current!.equity).toBe(10500.0)
    expect(result.current!.positions).toHaveLength(1)
  })

  it('does not fetch when sessionId is null', () => {
    const { result } = renderHook(() => useSessionStatus(null, 60000))
    expect(result.current).toBeNull()
  })
})

describe('useSessionTrades', () => {
  it('fetches trades for a session', async () => {
    const { result } = renderHook(() => useSessionTrades('sess-001'))

    await waitFor(() => {
      expect(result.current.length).toBeGreaterThan(0)
    })

    expect(result.current[0]).toHaveProperty('entry_ts')
    expect(result.current[0]).toHaveProperty('side')
    expect(result.current[0]).toHaveProperty('pnl')
  })

  it('returns empty array when sessionId is null', () => {
    const { result } = renderHook(() => useSessionTrades(null))
    expect(result.current).toEqual([])
  })
})

describe('stopSession', () => {
  it('POSTs to /api/v1/paper/stop with session_id', async () => {
    const result = await stopSession('sess-001')
    expect(result.stopped).toBe(true)
  })
})

describe('killSession', () => {
  it('POSTs to /api/v1/paper/kill with session_id', async () => {
    const result = await killSession('sess-001')
    expect(result.killed).toBe(true)
  })
})
