/**
 * Hook for multi-regime backtesting.
 * Mirrors useBacktest.ts but posts to /api/v1/backtest/run-regimes.
 */
import { useState, useCallback, useRef, useEffect } from 'react'

interface RegimeBacktestParams {
  strategy: string
  code?: string
  market: string
  markets?: string[]
  resolution_s: number
  regime_ids: string[]
  initial_capital: number
  fee_rate: number
  params?: Record<string, number>
  margin_tracking?: boolean
  fill_model?: string
  slippage_bps?: number
}

interface Progress {
  phase: string
  pct: number
  detail: string
  elapsed_s: number
  candles: number
}

const MAX_POLL_TIME_S = 600 // 10 min (multiple regimes take longer)

export function useRegimeBacktest() {
  const [runId, setRunId] = useState<string | null>(null)
  const [status, setStatus] = useState<string>('idle')
  const [results, setResults] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const pollStartRef = useRef<number>(0)
  const pollErrorCountRef = useRef<number>(0)
  const cancelledRef = useRef(false)
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => () => {
    cancelledRef.current = true
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
  }, [])

  const run = useCallback(async (params: RegimeBacktestParams) => {
    setStatus('running')
    setError(null)
    setResults(null)
    setProgress({ phase: 'init', pct: 0, detail: 'Submitting regime test...', elapsed_s: 0, candles: 0 })
    pollStartRef.current = Date.now()
    pollErrorCountRef.current = 0
    cancelledRef.current = false

    try {
      const res = await fetch('/api/v1/backtest/run-regimes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      })

      if (!res.ok) {
        try {
          const errData = await res.json()
          throw new Error(errData.detail || errData.error || `Server error ${res.status}`)
        } catch (parseErr: any) {
          if (parseErr.message && !parseErr.message.startsWith('Server error')) throw parseErr
          throw new Error(`Server error ${res.status}`)
        }
      }

      const data = await res.json()
      if (data.error) throw new Error(data.error)
      if (!data.id) throw new Error(data.detail || 'No run ID returned')
      setRunId(data.id)

      const poll = async () => {
        if (cancelledRef.current) return
        const elapsed = (Date.now() - pollStartRef.current) / 1000
        if (elapsed > MAX_POLL_TIME_S) {
          setError(`Regime test timed out after ${MAX_POLL_TIME_S}s`)
          setStatus('failed')
          setProgress(null)
          return
        }

        try {
          const r = await fetch(`/api/v1/backtest/${data.id}/results`)
          if (!r.ok) throw new Error(`Poll error: ${r.status}`)
          const d = await r.json()
          pollErrorCountRef.current = 0

          if (d.progress) setProgress(d.progress)

          if (d.status === 'complete') {
            setResults(d.results)
            setStatus('complete')
            setProgress({
              phase: 'done', pct: 100, detail: 'Complete',
              elapsed_s: d.progress?.elapsed_s || 0,
              candles: d.progress?.candles || 0,
            })
          } else if (d.status === 'failed') {
            setError(d.results?.error || 'Regime test failed')
            setStatus('failed')
            setProgress(null)
          } else {
            pollTimerRef.current = setTimeout(poll, 500)
          }
        } catch {
          pollErrorCountRef.current++
          if (pollErrorCountRef.current >= 20) {
            setError('Lost connection to server')
            setStatus('failed')
            setProgress(null)
            return
          }
          pollTimerRef.current = setTimeout(poll, 2000)
        }
      }
      pollTimerRef.current = setTimeout(poll, 300)
    } catch (e: any) {
      setError(e.message || 'Failed to submit regime test')
      setStatus('failed')
      setProgress(null)
    }
  }, [])

  const reset = useCallback(() => {
    cancelledRef.current = true
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
    setRunId(null)
    setStatus('idle')
    setResults(null)
    setError(null)
    setProgress(null)
  }, [])

  return { run, reset, runId, status, results, error, progress }
}
