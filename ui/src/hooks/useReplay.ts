/**
 * useReplay — fetch session metadata + replay state from the backend.
 *
 * D-6.4-replay slice 5 frontend. Wraps `/api/v1/replay/{id}/summary`
 * and `/api/v1/replay/{id}/state` so the time-travel page can scrub
 * through a session's history.
 *
 * Two separate fetches: the cheap summary polls every few seconds for
 * "did new events arrive?", and the heavier state fetch fires on
 * deliberate target_ts change. Errors don't auto-retry — replay is
 * a deliberate query, not a steady stream.
 */
import { useEffect, useState } from 'react'

export interface ReplaySummary {
  session_id: string
  event_count: number
  latest_seq: number | null
  snapshot_count: number
}

export interface ReplayPosition {
  market: string
  venue: string
  side: 'long' | 'short'
  size: number
  entry_price: number
}

export interface ReplayState {
  session_id: string
  target_ts: number
  cash: number
  initial_capital: number
  realized_pnl: number
  total_fees: number
  total_tx_costs: number
  total_funding: number
  total_borrow_paid: number
  fill_count: number
  liquidation_count: number
  order_submit_count: number
  order_cancel_count: number
  positions: Record<string, ReplayPosition>
}

export function useReplaySummary(sessionId: string, pollMs = 5000) {
  const [data, setData] = useState<ReplaySummary | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!sessionId) {
      setData(null)
      return
    }
    let active = true
    let timer: ReturnType<typeof setTimeout> | null = null

    const tick = async () => {
      try {
        const r = await fetch(`/api/v1/replay/${encodeURIComponent(sessionId)}/summary`)
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const json = await r.json()
        if (!active) return
        setData(json)
        setError(null)
      } catch (err: any) {
        if (!active) return
        setError(err?.message || String(err))
      }
      if (active) timer = setTimeout(tick, pollMs)
    }
    tick()
    return () => {
      active = false
      if (timer) clearTimeout(timer)
    }
  }, [sessionId, pollMs])

  return { data, error }
}

export function useReplayState(
  sessionId: string,
  targetTs: number | null,
  initialCapital: number = 10_000,
) {
  const [data, setData] = useState<ReplayState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!sessionId || targetTs == null) {
      setData(null)
      return
    }
    let active = true
    setLoading(true)

    const url = `/api/v1/replay/${encodeURIComponent(sessionId)}/state` +
      `?target_ts=${targetTs}&initial_capital=${initialCapital}`

    fetch(url)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(json => {
        if (!active) return
        setData(json)
        setError(null)
        setLoading(false)
      })
      .catch(err => {
        if (!active) return
        setError(err?.message || String(err))
        setLoading(false)
      })

    return () => { active = false }
  }, [sessionId, targetTs, initialCapital])

  return { data, error, loading }
}


export interface ReplayEvent {
  seq: number
  ts: number
  kind: string
  payload: Record<string, any>
}

export interface ReplayEventsPage {
  session_id: string
  since: number | null
  count: number
  has_more: boolean
  events: ReplayEvent[]
}


/** Fetch a page of events from `/api/v1/replay/{id}/events`. The
 *  hook re-fires when `since` or `limit` change. Used by the
 *  Replay page's tail panel. */
export function useReplayEvents(
  sessionId: string,
  since: number | null = null,
  limit: number = 200,
) {
  const [data, setData] = useState<ReplayEventsPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!sessionId) {
      setData(null)
      return
    }
    let active = true
    setLoading(true)

    const params = new URLSearchParams()
    if (since != null) params.set('since', String(since))
    params.set('limit', String(limit))
    const url = `/api/v1/replay/${encodeURIComponent(sessionId)}/events?${params}`

    fetch(url)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(json => {
        if (!active) return
        setData(json)
        setError(null)
        setLoading(false)
      })
      .catch(err => {
        if (!active) return
        setError(err?.message || String(err))
        setLoading(false)
      })
    return () => { active = false }
  }, [sessionId, since, limit])

  return { data, error, loading }
}
