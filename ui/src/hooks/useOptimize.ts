import { useState, useCallback, useRef, useEffect } from 'react'

interface OptimizeParams {
  code: string
  market: string
  resolution_s: number
  start_ts: number
  end_ts: number
  initial_capital: number
  fee_rate: number
  metric: string
  trials: number
}

interface Trial {
  params: Record<string, any>
  metric_value: number
  total_pnl: number
  sharpe_ratio: number
  max_drawdown: number
  win_rate: number
  total_trades: number
}

interface OptResult {
  best_params: Record<string, any>
  best_value: number
  metric: string
  n_trials: number
  trials: Trial[]
  strategy_name: string
  market: string
  candles: number
}

export function useOptimize() {
  const [status, setStatus] = useState<string>('idle')
  const [results, setResults] = useState<OptResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<{phase: string, pct: number, detail: string, elapsed_s: number} | null>(null)
  const cancelledRef = useRef(false)
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const errorCountRef = useRef(0)

  useEffect(() => () => {
    cancelledRef.current = true
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
  }, [])

  const run = useCallback(async (params: OptimizeParams) => {
    cancelledRef.current = false
    errorCountRef.current = 0
    setStatus('running')
    setError(null)
    setResults(null)
    setProgress({ phase: 'init', pct: 0, detail: 'Submitting...', elapsed_s: 0 })

    try {
      const res = await fetch('/api/v1/optimize/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'Failed to start optimization')
      }
      const data = await res.json()
      if (!data.id) throw new Error('No run ID')

      const startTime = Date.now()
      const poll = async () => {
        if (cancelledRef.current) return
        // 30 minute timeout for optimization
        if ((Date.now() - startTime) / 1000 > 1800) {
          setError('Optimization timed out after 30 minutes')
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
            setError(d.results?.error || 'Optimization failed')
            setStatus('failed')
            setProgress(null)
          } else {
            pollTimerRef.current = setTimeout(poll, 1000)
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
      pollTimerRef.current = setTimeout(poll, 500)
    } catch (e: any) {
      setError(e.message)
      setStatus('failed')
      setProgress(null)
    }
  }, [])

  return { run, status, results, error, progress }
}
