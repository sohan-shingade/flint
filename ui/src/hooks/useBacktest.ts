import { useState, useCallback } from 'react'

interface BacktestParams {
  strategy: string
  code?: string
  market: string
  resolution_s: number
  start_ts: number
  end_ts: number
  initial_capital: number
  fee_rate: number
  params?: Record<string, number>
}

export function useBacktest() {
  const [runId, setRunId] = useState<string | null>(null)
  const [status, setStatus] = useState<string>('idle')
  const [results, setResults] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)

  const run = useCallback(async (params: BacktestParams) => {
    setStatus('running')
    setError(null)
    setResults(null)

    try {
      const res = await fetch('/api/v1/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      })
      const data = await res.json()
      setRunId(data.id)

      // Poll for results
      const poll = async () => {
        const r = await fetch(`/api/v1/backtest/${data.id}/results`)
        const d = await r.json()
        if (d.status === 'complete') {
          setResults(d.results)
          setStatus('complete')
        } else if (d.status === 'failed') {
          setError(d.results?.error || 'Backtest failed')
          setStatus('failed')
        } else {
          setTimeout(poll, 1000)
        }
      }
      setTimeout(poll, 500)
    } catch (e: any) {
      setError(e.message)
      setStatus('failed')
    }
  }, [])

  return { run, runId, status, results, error }
}
