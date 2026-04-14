import { useState, useEffect } from 'react'

interface EquityPoint { ts: number; equity: number; cash: number; unrealized_pnl: number }
interface LiveFill { fill_id: string; market: string; side: string; price: number; size: number; fee: number; ts: number; venue: string }
interface LiveSession { session_id: string; strategy: string; market: string; venue: string; status: string; started_at: number; stopped_at: number | null }

export function useLiveMonitor(sessionId: string, pollInterval = 2000) {
  const [equity, setEquity] = useState<EquityPoint[]>([])
  const [fills, setFills] = useState<LiveFill[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    if (!sessionId) return
    let active = true
    const poll = async () => {
      try {
        const [eqRes, fillRes] = await Promise.all([
          fetch(`/api/v1/live/equity?session_id=${sessionId}`),
          fetch(`/api/v1/live/fills?session_id=${sessionId}`),
        ])
        if (!active) return
        const eqData = await eqRes.json()
        const fillData = await fillRes.json()
        if (eqData.equity) setEquity(eqData.equity)
        if (fillData.fills) setFills(fillData.fills)
      } catch (e) {
        if (active) {
          const msg = String(e)
          if (msg.includes('Failed to fetch') || msg.includes('NetworkError')) {
            setError('Cannot connect to server \u2014 is flint serve running?')
          } else {
            setError(msg)
          }
        }
      }
    }
    poll()
    const id = setInterval(poll, pollInterval)
    return () => { active = false; clearInterval(id) }
  }, [sessionId, pollInterval])

  return { equity, fills, error }
}

export function useLiveSessions() {
  const [sessions, setSessions] = useState<LiveSession[]>([])
  useEffect(() => {
    fetch('/api/v1/live/sessions').then(r => r.json()).then(d => {
      if (d.sessions) setSessions(d.sessions)
    }).catch(() => {})
  }, [])
  return sessions
}
