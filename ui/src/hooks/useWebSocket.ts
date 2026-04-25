/**
 * useWebSocket<T> — typed WebSocket hook with reconnect backoff.
 *
 * D-4.3-websocket (Wave 3). Built on the same exponential-backoff
 * schedule as `useBackoffPoll` (1s → 2s → 5s → 10s → 30s) so the
 * UI surfaces a consistent "retry in Xs" story regardless of which
 * transport the underlying hook uses.
 *
 * Replay-on-reconnect: the server tags every broadcast with a
 * monotonic `seq`; this hook tracks the highest seq it has seen and
 * passes `?since=<seq>` on the next connection attempt so missed
 * events are replayed.
 *
 * Heartbeat: server pings every ~10s. Client tolerates 30s of
 * silence before forcing a reconnect — bounds the worst-case stale
 * data window when an intermediate proxy silently drops the socket.
 *
 * Usage:
 *   const { data, status, errorCount } = useWebSocket<EquityTick>(
 *     `/ws/paper/${sessionId}`,
 *     { enabled: !!sessionId },
 *   )
 */
import { useEffect, useRef, useState } from 'react'

const BACKOFF_SCHEDULE_MS = [1000, 2000, 5000, 10000, 30000] as const
const HEARTBEAT_TIMEOUT_MS = 30_000

export type WebSocketStatus = 'connecting' | 'open' | 'closed' | 'error'

export interface UseWebSocketOptions {
  /** Skip the effect entirely while false (e.g. no session id yet). */
  enabled?: boolean
  /** Treat this many seconds without any message as a stale socket. */
  heartbeatTimeoutMs?: number
  /** Custom protocol(s) for the WebSocket constructor. */
  protocols?: string | string[]
  /** Optional callback when a fresh connection opens (post-replay). */
  onOpen?: () => void
}

export interface UseWebSocketState<T> {
  data: T | null
  status: WebSocketStatus
  /** Latest seq seen across the open + reconnect lifecycle. */
  lastSeq: number | null
  /** Consecutive failed connect attempts; 0 once any open succeeds. */
  errorCount: number
  /** Last error message, or null. */
  lastError: string | null
}

function nextDelay(errorCount: number): number {
  if (errorCount <= 0) return BACKOFF_SCHEDULE_MS[0]
  const idx = Math.min(errorCount - 1, BACKOFF_SCHEDULE_MS.length - 1)
  return BACKOFF_SCHEDULE_MS[idx]
}

/** Build a `ws(s)://` URL from a relative path. SSR-safe stub when
 *  `window` isn't defined. */
function _absoluteWsUrl(path: string): string {
  if (typeof window === 'undefined') return path
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  // path may already be absolute (`ws://...`) — leave alone.
  if (path.startsWith('ws://') || path.startsWith('wss://')) return path
  return `${proto}//${host}${path.startsWith('/') ? path : '/' + path}`
}

export function useWebSocket<T = any>(
  path: string,
  opts: UseWebSocketOptions = {},
): UseWebSocketState<T> {
  const {
    enabled = true,
    heartbeatTimeoutMs = HEARTBEAT_TIMEOUT_MS,
    protocols,
    onOpen,
  } = opts

  const [state, setState] = useState<UseWebSocketState<T>>({
    data: null,
    status: 'closed',
    lastSeq: null,
    errorCount: 0,
    lastError: null,
  })

  // Stable refs so the effect doesn't re-fire on every render.
  const onOpenRef = useRef(onOpen)
  onOpenRef.current = onOpen
  // The latest seq survives reconnects so the next attempt can replay
  // from where we left off.
  const lastSeqRef = useRef<number | null>(null)

  useEffect(() => {
    if (!enabled) return

    let active = true
    let consecutiveErrors = 0
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let heartbeatTimer: ReturnType<typeof setTimeout> | null = null

    const armHeartbeatTimer = () => {
      if (heartbeatTimer) clearTimeout(heartbeatTimer)
      heartbeatTimer = setTimeout(() => {
        // Stale socket — close and let onclose schedule a reconnect.
        if (ws && ws.readyState === WebSocket.OPEN) ws.close(4000, 'stale')
      }, heartbeatTimeoutMs)
    }

    const connect = () => {
      if (!active) return
      setState((s) => ({ ...s, status: 'connecting' }))

      const seq = lastSeqRef.current
      const sep = path.includes('?') ? '&' : '?'
      const fullPath = seq != null ? `${path}${sep}since=${seq}` : path
      const url = _absoluteWsUrl(fullPath)

      try {
        ws = protocols !== undefined ? new WebSocket(url, protocols) : new WebSocket(url)
      } catch (err: any) {
        consecutiveErrors += 1
        if (active) {
          setState((s) => ({
            ...s,
            status: 'error',
            errorCount: consecutiveErrors,
            lastError: err?.message || String(err),
          }))
          reconnectTimer = setTimeout(connect, nextDelay(consecutiveErrors))
        }
        return
      }

      ws.onopen = () => {
        if (!active) return
        consecutiveErrors = 0
        armHeartbeatTimer()
        setState((s) => ({
          ...s, status: 'open', errorCount: 0, lastError: null,
        }))
        try { onOpenRef.current?.() } catch { /* ignore */ }
      }

      ws.onmessage = (evt) => {
        if (!active) return
        armHeartbeatTimer()
        let parsed: any
        try { parsed = JSON.parse(evt.data) }
        catch { return }
        // Track per-channel seq (server stamps every message)
        if (typeof parsed.seq === 'number') {
          lastSeqRef.current = parsed.seq
        }
        // Don't bubble heartbeat pings up to the consumer
        if (parsed.type === 'ping') return
        setState((s) => ({
          ...s,
          data: parsed as T,
          lastSeq: typeof parsed.seq === 'number' ? parsed.seq : s.lastSeq,
        }))
      }

      ws.onerror = () => {
        // onclose will fire next; keep the error state-shape minimal here.
        if (!active) return
        setState((s) => ({ ...s, status: 'error', lastError: 'socket error' }))
      }

      ws.onclose = (evt) => {
        if (!active) return
        if (heartbeatTimer) clearTimeout(heartbeatTimer)
        // Treat "successful close after at-least-one-msg" as healthy
        // and back off only on errors. evt.code 1000 = normal close;
        // 4000 = our stale-heartbeat code.
        const wasOpen = ws?.readyState === WebSocket.CLOSED && evt.code === 1000
        if (!wasOpen) {
          consecutiveErrors += 1
        }
        setState((s) => ({
          ...s,
          status: 'closed',
          errorCount: consecutiveErrors,
          lastError: evt.reason || s.lastError,
        }))
        if (active) {
          reconnectTimer = setTimeout(connect, nextDelay(consecutiveErrors))
        }
      }
    }

    connect()

    return () => {
      active = false
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (heartbeatTimer) clearTimeout(heartbeatTimer)
      if (ws && ws.readyState <= WebSocket.OPEN) {
        ws.close(1000, 'unmount')
      }
    }
  }, [enabled, path, heartbeatTimeoutMs, protocols])

  return state
}
