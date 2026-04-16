import { useState, useCallback, useRef, useEffect } from 'react'

interface WalkForwardParams {
  code: string
  market: string
  resolution_s: number
  start_ts: number
  end_ts: number
  initial_capital: number
  fee_rate: number
  metric: string
  n_windows: number
  train_pct: number
  trials_per_window: number
}

interface WindowResult {
  window_idx: number
  train_start_ts: number
  train_end_ts: number
  test_start_ts: number
  test_end_ts: number
  best_params: Record<string, any>
  in_sample_sharpe: number
  in_sample_pnl: number
  out_of_sample_sharpe: number
  out_of_sample_pnl: number
  out_of_sample_trades: number
}

export interface WalkForwardResult {
  strategy_name: string
  metric: string
  n_windows: number
  windows: WindowResult[]
  avg_is_sharpe: number
  avg_oos_sharpe: number
  avg_is_pnl: number
  avg_oos_pnl: number
  overfitting_ratio: number
  parameter_stability: Record<string, number>
  market: string
  candles: number
}

export function useWalkForward() {
  const [status, setStatus] = useState<string>('idle')
  const [results, setResults] = useState<WalkForwardResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<{phase: string, pct: number, detail: string, elapsed_s: number} | null>(null)
  const cancelledRef = useRef(false)
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const errorCountRef = useRef(0)

  useEffect(() => () => {
    cancelledRef.current = true
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
  }, [])

  const run = useCallback(async (params: WalkForwardParams) => {
    cancelledRef.current = false
    errorCountRef.current = 0
    setStatus('running')
    setError(null)
    setResults(null)
    setProgress({ phase: 'init', pct: 0, detail: 'Starting walk-forward...', elapsed_s: 0 })

    try {
      const res = await fetch('/api/v1/optimize/walk-forward', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'Failed to start walk-forward analysis')
      }
      const data = await res.json()
      if (!data.id) throw new Error('No run ID')

      const startTime = Date.now()
      const poll = async () => {
        if (cancelledRef.current) return
        if ((Date.now() - startTime) / 1000 > 1800) {
          setError('Walk-forward timed out after 30 minutes')
          setStatus('failed')
          setProgress(null)
          return
        }
        try {
          const r = await fetch(`/api/v1/optimize/${data.id}/results`)
          const d = await r.json()
          if (d.progress) setProgress(d.progress)
          if (d.status === 'complete') {
            setResults(d.results)
            setStatus('complete')
          } else if (d.status === 'failed') {
            setError(d.results?.error || 'Walk-forward failed')
            setStatus('failed')
            setProgress(null)
          } else {
            pollTimerRef.current = setTimeout(poll, 1500)
          }
        } catch {
          errorCountRef.current++
          if (errorCountRef.current >= 20) {
            setError('Lost connection to server')
            setStatus('failed')
            setProgress(null)
            return
          }
          pollTimerRef.current = setTimeout(poll, 2000)
        }
      }
      pollTimerRef.current = setTimeout(poll, 1000)
    } catch (e: any) {
      setError(e.message)
      setStatus('failed')
      setProgress(null)
    }
  }, [])

  return { run, status, results, error, progress }
}
