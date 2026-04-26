import { useEffect, useState } from 'react'

import { useBackoffPoll } from './useBackoffPoll'
import { useHybridPoll } from './useHybridPoll'

interface PaperTickPayload {
  type: 'tick' | 'trade'
  ts?: number
  equity?: number
  cash?: number
  unrealized_pnl?: number
  total_trades?: number
}

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
  const { data, error, errorCount, nextRetryIn } = useBackoffPoll<Portfolio>(
    async (signal) => {
      const res = await fetch(`${API}/portfolio`, { signal })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return res.json()
    },
    { intervalMs: pollInterval },
  )

  const friendlyError =
    error && /Failed to fetch|NetworkError/i.test(error)
      ? 'Cannot connect to server \u2014 is flint serve running?'
      : error

  return { portfolio: data, error: friendlyError, errorCount, nextRetryIn }
}

export function useSessionStatus(sessionId: string | null, pollInterval = 2000) {
  // WS-primary, polling fallback. When `paper:{sessionId}` is open
  // and emitting ticks the snapshot poll drops to 30s; when the
  // socket dies polling re-arms at `pollInterval` so the panel
  // never goes blank.
  const hybrid = useHybridPoll<SessionStatus, PaperTickPayload>(
    `/ws/paper/${sessionId ?? ''}`,
    async (signal) => {
      const res = await fetch(`${API}/status/${sessionId}`, { signal })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return res.json()
    },
    {
      enabled: !!sessionId,
      slowIntervalMs: 30_000,
      fastIntervalMs: pollInterval,
    },
  )

  // Overlay live tick fields on the polled snapshot so the
  // equity/cash/total_trades widgets feel real-time when WS is up.
  if (hybrid.data && hybrid.wsData?.type === 'tick') {
    const tick = hybrid.wsData
    return {
      ...hybrid.data,
      equity: tick.equity ?? hybrid.data.equity,
      cash: tick.cash ?? hybrid.data.cash,
      unrealized_pnl: tick.unrealized_pnl ?? hybrid.data.unrealized_pnl,
      total_trades: tick.total_trades ?? hybrid.data.total_trades,
    }
  }
  return hybrid.data
}

export function useSessionTrades(sessionId: string | null) {
  const [trades, setTrades] = useState<any[]>([])

  useEffect(() => {
    if (!sessionId) return
    fetch(`${API}/trades/${sessionId}`)
      .then(r => r.json())
      .then(d => setTrades(d.trades || []))
      .catch((e) => { console.warn("[hooks/usePaperTrading.ts] fetch failed:", e) })
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
