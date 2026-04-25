import { useEffect, useState } from 'react'

import { useBackoffPoll } from './useBackoffPoll'

interface EquityPoint { ts: number; equity: number; cash: number; unrealized_pnl: number }
interface LiveFill { fill_id: string; market: string; side: string; price: number; size: number; fee: number; ts: number; venue: string }
interface LiveSession { session_id: string; strategy: string; market: string; venue: string; status: string; started_at: number; stopped_at: number | null }

interface MonitorPayload {
  equity: EquityPoint[]
  fills: LiveFill[]
}

/**
 * Live session monitor — poll equity + fills together so the panel sees
 * a consistent snapshot. D-4.2-backoff-full migrates this off setInterval
 * onto useBackoffPoll so a flaky network doesn't hammer a dead server.
 */
export function useLiveMonitor(sessionId: string, pollInterval = 2000) {
  const { data, error, errorCount, nextRetryIn } = useBackoffPoll<MonitorPayload>(
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
    { enabled: !!sessionId, intervalMs: pollInterval },
  )

  const friendlyError =
    error && /Failed to fetch|NetworkError/i.test(error)
      ? 'Cannot connect to server — is flint serve running?'
      : error

  return {
    equity: data?.equity ?? [],
    fills: data?.fills ?? [],
    error: friendlyError ?? '',
    errorCount,
    nextRetryIn,
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
