import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'

import { useHybridPoll } from '../../hooks/useHybridPoll'

class MockSocket {
  static instances: MockSocket[] = []
  url: string
  readyState: number = 0
  onopen: ((evt: any) => void) | null = null
  onmessage: ((evt: any) => void) | null = null
  onerror: ((evt: any) => void) | null = null
  onclose: ((evt: any) => void) | null = null
  closed = false

  constructor(url: string) {
    this.url = url
    MockSocket.instances.push(this)
  }
  open() {
    this.readyState = 1
    this.onopen?.({})
  }
  message(data: any) {
    this.onmessage?.({ data: typeof data === 'string' ? data : JSON.stringify(data) })
  }
  close(code = 1000, reason = '') {
    if (this.closed) return
    this.closed = true
    this.readyState = 3
    this.onclose?.({ code, reason })
  }
}

describe('useHybridPoll', () => {
  beforeEach(() => {
    MockSocket.instances = []
    ;(MockSocket as any).OPEN = 1
    ;(MockSocket as any).CLOSED = 3
    vi.stubGlobal('WebSocket', MockSocket)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns polled snapshot data', async () => {
    const fetcher = vi.fn(async () => ({ snapshot: 'v1' }))
    const { result } = renderHook(() =>
      useHybridPoll<{ snapshot: string }, any>(
        '/ws/test',
        fetcher,
        { fastIntervalMs: 60_000, slowIntervalMs: 60_000 },
      )
    )
    await waitFor(() => {
      expect(result.current.data).toEqual({ snapshot: 'v1' })
    })
  })

  it('skips both transports when enabled=false', () => {
    const fetcher = vi.fn(async () => ({ snapshot: 'v1' }))
    renderHook(() =>
      useHybridPoll('/ws/test', fetcher, { enabled: false })
    )
    expect(MockSocket.instances.length).toBe(0)
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('reports wsHealthy=true after first non-ping message', async () => {
    const fetcher = vi.fn(async () => ({ snapshot: 'v1' }))
    const { result } = renderHook(() =>
      useHybridPoll<{ snapshot: string }, { type: string; v: number }>(
        '/ws/test',
        fetcher,
        { fastIntervalMs: 60_000, slowIntervalMs: 60_000 },
      )
    )
    await act(async () => {
      MockSocket.instances[0].open()
      MockSocket.instances[0].message({ channel: 'test', seq: 0, type: 'tick', v: 1 })
    })
    await waitFor(() => {
      expect(result.current.wsHealthy).toBe(true)
    })
    expect(result.current.wsData).toMatchObject({ type: 'tick', v: 1 })
  })

  it('wsHealthy false when only pings have arrived', async () => {
    const fetcher = vi.fn(async () => ({ snapshot: 'v1' }))
    const { result } = renderHook(() =>
      useHybridPoll('/ws/test', fetcher, { fastIntervalMs: 60_000, slowIntervalMs: 60_000 })
    )
    await act(async () => {
      MockSocket.instances[0].open()
      MockSocket.instances[0].message({ channel: 'test', seq: 0, type: 'ping' })
    })
    // ping doesn't bump lastSeq, so wsHealthy stays false
    expect(result.current.wsHealthy).toBe(false)
  })

  it('wsHealthy drops after socket disconnect', async () => {
    const fetcher = vi.fn(async () => ({ snapshot: 'v1' }))
    const { result } = renderHook(() =>
      useHybridPoll('/ws/test', fetcher, { fastIntervalMs: 60_000, slowIntervalMs: 60_000 })
    )
    await act(async () => {
      MockSocket.instances[0].open()
      MockSocket.instances[0].message({ channel: 'test', seq: 0, type: 'tick' })
    })
    await waitFor(() => {
      expect(result.current.wsHealthy).toBe(true)
    })
    await act(async () => {
      MockSocket.instances[0].close(1006, 'abnormal')
    })
    await waitFor(() => {
      expect(result.current.wsHealthy).toBe(false)
    })
  })

  it('exposes poll-side errors to the consumer', async () => {
    const fetcher = vi.fn(async () => {
      throw new Error('rest down')
    })
    const { result } = renderHook(() =>
      useHybridPoll('/ws/test', fetcher, { fastIntervalMs: 60_000, slowIntervalMs: 60_000 })
    )
    await waitFor(() => {
      expect(result.current.error).toBe('rest down')
      expect(result.current.errorCount).toBeGreaterThan(0)
    })
  })
})
