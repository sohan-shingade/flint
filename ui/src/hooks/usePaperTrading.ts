import { useState, useEffect } from 'react'

const API = '/api/v1/paper'

interface StrategySession {
  session_id: string
  strategy_name: string
  market: string
  equity: number
  pnl: number
  status: string
}

interface Portfolio {
  total_equity: number
  total_pnl: number
  total_initial_capital: number
  active_sessions: number
  total_sessions: number
  per_strategy: StrategySession[]
}

interface SessionStatus {
  session_id: string
  phase: string
  equity: number
  cash: number
  unrealized_pnl: number
  realized_pnl: number
  positions: any[]
  pending_orders: any[]
  total_trades: number
  total_fees: number
  initial_capital: number
  risk_status: any
  equity_curve: any[]
  metrics: any
  strategy: string
  market: string
  status: string
  pnl: number
}

export function usePaperPortfolio(pollInterval = 2000) {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    const poll = async () => {
      try {
        const res = await fetch(`${API}/portfolio`)
        if (res.ok && active) {
          setPortfolio(await res.json())
          setError(null)
        }
      } catch (e) {
        if (active) setError(String(e))
      }
    }
    poll()
    const id = setInterval(poll, pollInterval)
    return () => { active = false; clearInterval(id) }
  }, [pollInterval])

  return { portfolio, error }
}

export function useSessionStatus(sessionId: string | null, pollInterval = 2000) {
  const [status, setStatus] = useState<SessionStatus | null>(null)

  useEffect(() => {
    if (!sessionId) return
    let active = true
    const poll = async () => {
      try {
        const res = await fetch(`${API}/status/${sessionId}`)
        if (res.ok && active) setStatus(await res.json())
      } catch {}
    }
    poll()
    const id = setInterval(poll, pollInterval)
    return () => { active = false; clearInterval(id) }
  }, [sessionId, pollInterval])

  return status
}

export function useSessionTrades(sessionId: string | null) {
  const [trades, setTrades] = useState<any[]>([])

  useEffect(() => {
    if (!sessionId) return
    fetch(`${API}/trades/${sessionId}`)
      .then(r => r.json())
      .then(d => setTrades(d.trades || []))
      .catch(() => {})
  }, [sessionId])

  return trades
}

export async function deployStrategy(params: {
  strategy_code: string
  strategy_name: string
  strategy_params: Record<string, any>
  market: string
  initial_capital: number
  replay_start_ts: number
  risk_config: Record<string, any>
}) {
  const res = await fetch(`${API}/deploy`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  return res.json()
}

export async function stopSession(sessionId: string) {
  const res = await fetch(`${API}/stop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId }),
  })
  return res.json()
}

export async function killSession(sessionId: string) {
  const res = await fetch(`${API}/kill`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId }),
  })
  return res.json()
}
