/**
 * useHybridPoll<T> — WebSocket-primary, polling fallback.
 *
 * Pure-WS would be cleaner code but a single WS bug = dead UI. So
 * this composes `useWebSocket` (fast liveness) with `useBackoffPoll`
 * (snapshot refresh) and adapts the polling cadence to WS health:
 *
 *   • WS healthy → poll slowly (30s default) — the WS feed delivers
 *     ticks; polling exists only to refresh the heavier snapshot
 *     payload (positions, equity_curve, metrics) that doesn't ride
 *     the tick channel.
 *   • WS dead/silent → poll fast (2s default) — full polling re-arms
 *     so the page stays live even when the socket is gone.
 *
 * The hook returns the polled snapshot (the canonical full payload)
 * + the latest WS message + a `wsHealthy` flag the page can show in
 * a connection badge. The page is responsible for overlaying live
 * WS fields onto the snapshot — this hook doesn't merge for you,
 * because WS payload shape is per-channel and the merge is page
 * concern.
 */
import { useMemo } from 'react'

import { useBackoffPoll } from './useBackoffPoll'
import { useWebSocket } from './useWebSocket'

export interface UseHybridPollOptions {
  enabled?: boolean
  /** Slow interval when WS is healthy. Default 30_000 ms. */
  slowIntervalMs?: number
  /** Fast interval when WS is dead. Default 2_000 ms. */
  fastIntervalMs?: number
}

export interface UseHybridPollState<TPoll, TWs> {
  /** Latest snapshot from the polled REST endpoint. */
  data: TPoll | null
  /** Latest WS event payload. Null until the first ws message arrives. */
  wsData: TWs | null
  /** True when the WS is open AND has emitted at least one message. */
  wsHealthy: boolean
  /** Poll-side error state, surfaced for ConnectionBanner-style UX. */
  error: string | null
  errorCount: number
  /** ms until next poll attempt. */
  nextRetryIn: number
}

export function useHybridPoll<TPoll = unknown, TWs = unknown>(
  wsPath: string,
  fetcher: (signal: AbortSignal) => Promise<TPoll>,
  opts: UseHybridPollOptions = {},
): UseHybridPollState<TPoll, TWs> {
  const {
    enabled = true,
    slowIntervalMs = 30_000,
    fastIntervalMs = 2_000,
  } = opts

  const ws = useWebSocket<TWs>(wsPath, { enabled })
  // Healthy = open AND we've received at least one real message
  // (lastSeq becomes non-null after the first non-ping). errorCount
  // > 0 means we just bounced; stay in "fast" mode until we reconnect
  // and see a tick.
  const wsHealthy = ws.status === 'open' && ws.lastSeq !== null && ws.errorCount === 0

  const intervalMs = useMemo(
    () => (wsHealthy ? slowIntervalMs : fastIntervalMs),
    [wsHealthy, slowIntervalMs, fastIntervalMs],
  )

  const poll = useBackoffPoll<TPoll>(fetcher, { enabled, intervalMs })

  return {
    data: poll.data,
    wsData: ws.data,
    wsHealthy,
    error: poll.error,
    errorCount: poll.errorCount,
    nextRetryIn: poll.nextRetryIn,
  }
}
