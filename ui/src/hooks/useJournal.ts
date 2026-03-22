import { useState, useCallback, useEffect } from 'react'

interface JournalRun {
  run_id: string
  strategy_name: string
  market: string
  resolution_s: number
  start_ts: number
  end_ts: number
  initial_capital: number
  created_at: number
  total_pnl: number
  win_rate: number
  sharpe_ratio: number
  max_drawdown: number
  total_trades: number
  total_fees: number
}

export function useJournal() {
  const [runs, setRuns] = useState<JournalRun[]>([])
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/v1/journal/runs?limit=50')
      if (!r.ok) { setRuns([]); setLoading(false); return }
      const d = await r.json()
      setRuns(d.runs || [])
    } catch {
      setRuns([])
    }
    setLoading(false)
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const deleteRun = useCallback(async (runId: string) => {
    await fetch(`/api/v1/journal/runs/${runId}`, { method: 'DELETE' })
    refresh()
  }, [refresh])

  return { runs, loading, refresh, deleteRun }
}
