import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'

import { useWebSocket } from '../../hooks/useWebSocket'

/** Minimal WebSocket stand-in with controllable lifecycle. */
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

describe('useWebSocket', () => {
  beforeEach(() => {
    MockSocket.instances = []
    ;(MockSocket as any).OPEN = 1
    ;(MockSocket as any).CLOSED = 3
    vi.stubGlobal('WebSocket', MockSocket)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('connects on mount when enabled', async () => {
    renderHook(() => useWebSocket('/ws/paper/abc'))
    expect(MockSocket.instances.length).toBe(1)
    expect(MockSocket.instances[0].url).toContain('/ws/paper/abc')
  })

  it('skips connection when enabled=false', () => {
    renderHook(() => useWebSocket('/ws/paper/abc', { enabled: false }))
    expect(MockSocket.instances.length).toBe(0)
  })

  it('parses incoming JSON and exposes data', async () => {
    const { result } = renderHook(() => useWebSocket<{ type: string; equity: number }>('/ws/paper/abc'))
    await act(async () => {
      MockSocket.instances[0].open()
      MockSocket.instances[0].message({ channel: 'paper:abc', seq: 0, type: 'tick', equity: 10_500 })
    })
    await waitFor(() => {
      expect(result.current.data?.equity).toBe(10_500)
    })
    expect(result.current.status).toBe('open')
    expect(result.current.lastSeq).toBe(0)
  })

  it('drops ping envelopes (no data update)', async () => {
    const { result } = renderHook(() => useWebSocket('/ws/paper/abc'))
    await act(async () => {
      MockSocket.instances[0].open()
      MockSocket.instances[0].message({ channel: 'paper:abc', seq: 0, type: 'ping', ts: 123 })
    })
    expect(result.current.data).toBeNull()
    // lastSeq is updated only when a non-ping message arrives — ping
    // tracking is internal (the ref behind ?since=) so we don't expose
    // it through the state field. Send a real tick to confirm.
    await act(async () => {
      MockSocket.instances[0].message({ channel: 'paper:abc', seq: 1, type: 'tick', equity: 1 })
    })
    await waitFor(() => {
      expect(result.current.lastSeq).toBe(1)
    })
  })

  it('appends ?since=<seq> on reconnect', async () => {
    const { result } = renderHook(() => useWebSocket('/ws/paper/abc'))
    await act(async () => {
      MockSocket.instances[0].open()
      MockSocket.instances[0].message({ channel: 'paper:abc', seq: 5, type: 'tick' })
      // Force a non-clean close so the hook reconnects.
      MockSocket.instances[0].close(1006, 'abnormal')
    })
    // Wait for a second connection attempt (back off ≥ 1s; advance time)
    await waitFor(() => {
      expect(MockSocket.instances.length).toBeGreaterThanOrEqual(2)
    }, { timeout: 5000 })
    expect(MockSocket.instances[1].url).toContain('since=5')
    expect(result.current.errorCount).toBeGreaterThan(0)
  })

  it('closes the socket on unmount', () => {
    const { unmount } = renderHook(() => useWebSocket('/ws/paper/abc'))
    MockSocket.instances[0].open()
    expect(MockSocket.instances[0].closed).toBe(false)
    unmount()
    expect(MockSocket.instances[0].closed).toBe(true)
  })
})
