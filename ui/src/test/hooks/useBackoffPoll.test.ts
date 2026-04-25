import { describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'

import { useBackoffPoll } from '../../hooks/useBackoffPoll'

// Use real timers — react-testing-library + fake timers + microtask
// flushing don't compose well, and the schedule we want to assert is
// short enough (≤2 s) that real time is fine.

describe('useBackoffPoll', () => {
  it('fetches once on mount and exposes the data', async () => {
    const fetcher = vi.fn(async () => ({ value: 42 }))
    const { result } = renderHook(() =>
      useBackoffPoll(fetcher, { intervalMs: 5000 })
    )

    await waitFor(() => {
      expect(result.current.data).toEqual({ value: 42 })
    })
    expect(result.current.error).toBeNull()
    expect(result.current.errorCount).toBe(0)
  })

  it('skips polling when enabled=false', () => {
    const fetcher = vi.fn(async () => ({ ok: true }))
    renderHook(() => useBackoffPoll(fetcher, { enabled: false }))
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('escalates errorCount on the first failure', async () => {
    const fetcher = vi.fn(async () => {
      throw new Error('boom')
    })
    const { result } = renderHook(() =>
      // huge intervalMs so the second attempt never fires during the test
      useBackoffPoll(fetcher, { intervalMs: 60_000 })
    )

    await waitFor(() => {
      expect(result.current.errorCount).toBe(1)
    })
    expect(result.current.lastError).toBe('boom')
    // first error → 1 s backoff slot
    expect(result.current.nextRetryIn).toBe(1000)
  })

  it('clears errorCount when a later request succeeds', async () => {
    let attempts = 0
    const fetcher = vi.fn(async () => {
      attempts += 1
      if (attempts === 1) throw new Error('fail-1')
      return { ok: true }
    })

    const { result } = renderHook(() =>
      // 1 s healthy interval; first call errors → 1 s backoff; second succeeds.
      useBackoffPoll(fetcher, { intervalMs: 1000 })
    )

    await waitFor(() => {
      expect(result.current.data).toEqual({ ok: true })
    }, { timeout: 5000 })
    expect(result.current.errorCount).toBe(0)
    expect(result.current.error).toBeNull()
  })

  it('aborts inflight fetch on unmount', async () => {
    let observedSignal: AbortSignal | null = null
    let callCount = 0
    const fetcher = vi.fn((signal: AbortSignal) => {
      observedSignal = signal
      callCount++
      return new Promise(() => { /* never resolves */ })
    })
    const { unmount } = renderHook(() => useBackoffPoll(fetcher))
    // Wait for the first fetch to be issued
    await waitFor(() => {
      expect(callCount).toBeGreaterThan(0)
    })
    unmount()
    expect(observedSignal?.aborted).toBe(true)
  })
})
