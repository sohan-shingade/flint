import { useState, useCallback, useRef } from 'react'

interface BacktestParams {
  strategy: string
  code?: string
  market: string
  markets?: string[]
  resolution_s: number
  start_ts: number
  end_ts: number
  initial_capital: number
  fee_rate: number
  params?: Record<string, number>
}

interface Progress {
  phase: string
  pct: number
  detail: string
  elapsed_s: number
  candles: number
}

const MAX_POLL_TIME_S = 300 // 5 minute timeout

export function useBacktest() {
  const [runId, setRunId] = useState<string | null>(null)
  const [status, setStatus] = useState<string>('idle')
  const [results, setResults] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const pollStartRef = useRef<number>(0)
  const pollErrorCountRef = useRef<number>(0)

  const run = useCallback(async (params: BacktestParams) => {
    setStatus('running')
    setError(null)
    setResults(null)
    setProgress({ phase: 'init', pct: 0, detail: 'Submitting...', elapsed_s: 0, candles: 0 })
    pollStartRef.current = Date.now()
    pollErrorCountRef.current = 0

    try {
      const res = await fetch('/api/v1/backtest/run', {
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
      if (data.error) {
        throw new Error(data.error)
      }
      if (!data.id) {
        throw new Error(data.detail || 'No run ID returned — check server logs')
      }
      setRunId(data.id)

      const poll = async () => {
        // Timeout check
        const elapsed = (Date.now() - pollStartRef.current) / 1000
        if (elapsed > MAX_POLL_TIME_S) {
          setError(`Backtest timed out after ${MAX_POLL_TIME_S}s`)
          setStatus('failed')
          setProgress(null)
          return
        }

        try {
          const r = await fetch(`/api/v1/backtest/${data.id}/results`)
          if (!r.ok) {
            throw new Error(`Poll error: ${r.status}`)
          }
          const d = await r.json()

          pollErrorCountRef.current = 0  // reset on successful poll

          if (d.progress) {
            setProgress(d.progress)
          }

          if (d.status === 'complete') {
            setResults(d.results)
            setStatus('complete')
            setProgress({
              phase: 'done', pct: 100, detail: 'Complete',
              elapsed_s: d.progress?.elapsed_s || 0,
              candles: d.progress?.candles || 0,
            })
          } else if (d.status === 'failed') {
            setError(d.results?.error || 'Backtest failed')
            setStatus('failed')
            setProgress(null)
          } else {
            setTimeout(poll, 500)
          }
        } catch (pollErr: any) {
          pollErrorCountRef.current++
          if (pollErrorCountRef.current >= 20) {
            setError('Lost connection to server — check if flint serve is running')
            setStatus('failed')
            setProgress(null)
            return
          }
          setTimeout(poll, 2000)
        }
      }
      setTimeout(poll, 300)
    } catch (e: any) {
      setError(e.message || 'Failed to submit backtest')
      setStatus('failed')
      setProgress(null)
    }
  }, [])

  return { run, runId, status, results, error, progress }
}
