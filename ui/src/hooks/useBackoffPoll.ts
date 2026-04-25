/**
 * useBackoffPoll<T> — generic polling hook with exponential backoff.
 *
 * D-4.2-backoff-full (Wave 1): replaces ad-hoc setInterval/setTimeout
 * loops scattered across useLiveMonitor, usePaperTrading, etc. with one
 * primitive that handles:
 *   - happy-path interval polling
 *   - exponential backoff on consecutive errors (1s → 2s → 5s → 10s → 30s)
 *   - AbortController cleanup so an unmounted component never sets
 *     state on a resolved fetch
 *   - errorCount / lastError / nextRetryIn surfaced for ConnectionBanner
 *
 * Usage:
 *   const { data, error, errorCount, nextRetryIn } = useBackoffPoll<Status>(
 *     async (signal) => {
 *       const r = await fetch(`/api/v1/paper/${id}/status`, { signal })
 *       if (!r.ok) throw new Error(`HTTP ${r.status}`)
 *       return r.json()
 *     },
 *     { enabled: !!id, intervalMs: 2000 }
 *   )
 */
import { useEffect, useRef, useState } from 'react'

const BACKOFF_SCHEDULE_MS = [1000, 2000, 5000, 10000, 30000] as const

export interface BackoffPollOptions {
  /** Polling interval when healthy. Defaults to 2000 ms. */
  intervalMs?: number
  /** Skip the effect entirely while false (e.g. no session id yet). */
  enabled?: boolean
  /** Callback invoked when an error first occurs (for telemetry). */
  onError?: (err: unknown) => void
}

export interface BackoffPollState<T> {
  data: T | null
  error: string | null
  /** Consecutive failures since the last success. Resets to 0 on success. */
  errorCount: number
  lastError: string | null
  /** ms until the next retry attempt. 0 when healthy or first attempt. */
  nextRetryIn: number
  /** True while a fetch is in flight. */
  loading: boolean
}

function schedule(errorCount: number, healthyMs: number): number {
  if (errorCount === 0) return healthyMs
  const idx = Math.min(errorCount - 1, BACKOFF_SCHEDULE_MS.length - 1)
  return BACKOFF_SCHEDULE_MS[idx]
}

export function useBackoffPoll<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  opts: BackoffPollOptions = {},
): BackoffPollState<T> {
  const { intervalMs = 2000, enabled = true, onError } = opts

  const [state, setState] = useState<BackoffPollState<T>>({
    data: null,
    error: null,
    errorCount: 0,
    lastError: null,
    nextRetryIn: 0,
    loading: false,
  })

  // Stable refs so the effect doesn't re-fire on every render.
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher
  const onErrorRef = useRef(onError)
  onErrorRef.current = onError

  useEffect(() => {
    if (!enabled) return

    let timer: ReturnType<typeof setTimeout> | null = null
    const ac = new AbortController()
    let active = true
    let consecutiveErrors = 0

    const tick = async () => {
      if (!active) return
      setState((s) => ({ ...s, loading: true }))
      try {
        const data = await fetcherRef.current(ac.signal)
        if (!active) return
        consecutiveErrors = 0
        setState({
          data,
          error: null,
          errorCount: 0,
          lastError: null,
          nextRetryIn: intervalMs,
          loading: false,
        })
      } catch (err: any) {
        if (!active) return
        // AbortError fires on unmount — don't surface it.
        if (err?.name === 'AbortError') return
        consecutiveErrors += 1
        const next = schedule(consecutiveErrors, intervalMs)
        const msg = err?.message || String(err)
        setState((s) => ({
          ...s,
          error: msg,
          lastError: msg,
          errorCount: consecutiveErrors,
          nextRetryIn: next,
          loading: false,
        }))
        try { onErrorRef.current?.(err) } catch { /* ignore */ }
      }

      if (!active) return
      const delay = schedule(consecutiveErrors, intervalMs)
      timer = setTimeout(tick, delay)
    }

    tick()

    return () => {
      active = false
      if (timer) clearTimeout(timer)
      ac.abort()
    }
  }, [enabled, intervalMs])

  return state
}
