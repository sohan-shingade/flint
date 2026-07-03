import { useEffect, useState } from 'react'

import { useHybridPoll } from './useHybridPoll'

interface EquityPoint { ts: number; equity: number; cash: number; unrealized_pnl: number }
interface LiveFill { fill_id: string; order_id?: string; market: string; side: string; price: number; size: number; fee: number; ts: number; venue: string }
interface LiveSession { session_id: string; strategy: string; market: string; venue: string; status: string; started_at: number; stopped_at: number | null }

interface MonitorPayload {
  equity: EquityPoint[]
  fills: LiveFill[]
}

interface LiveWsEvent {
  type: 'tick' | 'fill'
  ts?: number
  equity?: number
  cash?: number
  unrealized_pnl?: number
  // fill fields
  fill_id?: string
  market?: string
  side?: string
  price?: number
  size?: number
  fee?: number
  venue?: string
}

/**
 * Live session monitor — WS-primary, polling fallback. The fills tape
 * + equity sparkline want every tick; `/ws/live/{sessionId}` does the
 * fast path. Polling re-arms at `pollInterval` only if the socket
 * dies, otherwise it drops to 30s for snapshot refresh.
 */
export function useLiveMonitor(sessionId: string, pollInterval = 2000) {
  const hybrid = useHybridPoll<MonitorPayload, LiveWsEvent>(
    `/ws/live/${sessionId}`,
    async (signal) => {
      const [eqRes, fillRes] = await Promise.all([
        fetch(`/api/v1/live/equity?session_id=${sessionId}`, { signal }),
        fetch(`/api/v1/live/fills?session_id=${sessionId}`, { signal }),
      ])
      if (!eqRes.ok) throw new Error(`equity HTTP ${eqRes.status}`)
      if (!fillRes.ok) throw new Error(`fills HTTP ${fillRes.status}`)
      const eqData = await eqRes.json()
      const fillData = await fillRes.json()
      return {
        equity: eqData.equity ?? [],
        fills: fillData.fills ?? [],
      }
    },
    { enabled: !!sessionId, slowIntervalMs: 30_000, fastIntervalMs: pollInterval },
  )

  // Merge WS fill events into the polled fills list — dedupe by
  // (fill_id, ts) so a fill that lands on both transports doesn't
  // duplicate. WS arrives first; the next poll catches up.
  const [wsFills, setWsFills] = useState<LiveFill[]>([])
  useEffect(() => {
    if (hybrid.wsData?.type === 'fill' && hybrid.wsData.fill_id) {
      const f = hybrid.wsData
      setWsFills((prev) => {
        const key = `${f.fill_id}-${f.ts}`
        if (prev.some((x) => `${x.fill_id}-${x.ts}` === key)) return prev
        return [...prev, {
          fill_id: f.fill_id!, market: f.market!, side: f.side!,
          price: f.price!, size: f.size!, fee: f.fee ?? 0,
          ts: f.ts!, venue: f.venue ?? '',
        }]
      })
    }
  }, [hybrid.wsData])

  // De-duplicate against polled fills
  const polledFills = hybrid.data?.fills ?? []
  const polledKeys = new Set(polledFills.map((f) => `${f.fill_id}-${f.ts}`))
  const mergedFills = [
    ...polledFills,
    ...wsFills.filter((f) => !polledKeys.has(`${f.fill_id}-${f.ts}`)),
  ].sort((a, b) => b.ts - a.ts)

  // Equity overlay: append the latest WS tick to the polled curve
  // (display only; next poll picks it up canonically).
  const baseEquity = hybrid.data?.equity ?? []
  const equity = hybrid.wsData?.type === 'tick' && hybrid.wsData.ts != null
    ? [
        ...baseEquity,
        {
          ts: hybrid.wsData.ts,
          equity: hybrid.wsData.equity ?? 0,
          cash: hybrid.wsData.cash ?? 0,
          unrealized_pnl: hybrid.wsData.unrealized_pnl ?? 0,
        },
      ]
    : baseEquity

  const friendlyError =
    hybrid.error && /Failed to fetch|NetworkError/i.test(hybrid.error)
      ? 'Cannot connect to server — is flint serve running?'
      : hybrid.error

  return {
    equity,
    fills: mergedFills,
    error: friendlyError ?? '',
    errorCount: hybrid.errorCount,
    nextRetryIn: hybrid.nextRetryIn,
    wsHealthy: hybrid.wsHealthy,
  }
}

export function useLiveSessions() {
  const [sessions, setSessions] = useState<LiveSession[]>([])
  useEffect(() => {
    fetch('/api/v1/live/sessions').then(r => r.json()).then(d => {
      if (d.sessions) setSessions(d.sessions)
    }).catch((e) => { console.warn("[hooks/useLiveMonitor.ts] fetch failed:", e) })
  }, [])
  return sessions
}
