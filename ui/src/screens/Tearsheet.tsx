// Screen — Results / Tearsheet (§11.1, §12).
//
// Loads GET /api/v1/backtests/{run_id} and renders the honest result through the
// shared ResultPanel: raw Sharpe always beside the Deflated Sharpe + trial count,
// cost attribution with funding settled + cumulative, fill-fidelity tiers, and the
// equity + underwater curves. A "rejected" (funding gap) or "invalid" (user-source
// validation) verdict is a *completed* run and renders first-class, not as an error
// (§19.1). Dashboard and the Run Library deep-link here via ?run=<id>.

import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { apiGet, ApiError } from '../api/client'
import type { BacktestResult } from '../api/types'
import { ResultPanel } from '../components/ResultPanel'
import { ErrorState, Loading } from '../components/states'

export function TearsheetView({ result }: { result: BacktestResult }) {
  return (
    <div className="space-y-6 p-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-display text-xl text-terminal">{result.strategy ?? 'backtest'}</h2>
        {result.verdict === 'ok' && (
          <div className="font-mono text-xs text-ghost">
            {(result.universe ?? []).join(', ')} · {(result.venues ?? []).join(', ')}
            {result.clipped ? <span className="ml-2 text-amber">clipped to coverage</span> : null}
          </div>
        )}
      </header>
      <ResultPanel result={result} />
    </div>
  )
}

export default function Tearsheet() {
  const [params, setParams] = useSearchParams()
  const deepLink = params.get('run') ?? ''
  const [runId, setRunId] = useState(deepLink)
  const [submitted, setSubmitted] = useState(deepLink)
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [error, setError] = useState<ApiError | Error | null>(null)
  const [loading, setLoading] = useState(false)

  // Keep the URL and the loaded run in sync so a deep-link is shareable and the
  // back button restores the right run.
  useEffect(() => {
    if (deepLink && deepLink !== submitted) {
      setRunId(deepLink)
      setSubmitted(deepLink)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deepLink])

  useEffect(() => {
    if (!submitted) return
    let live = true
    setLoading(true)
    setError(null)
    setResult(null)
    apiGet<BacktestResult>(`/backtests/${encodeURIComponent(submitted)}`)
      .then((r) => {
        if (live) setResult(r)
      })
      .catch((e) => {
        if (live) setError(e)
      })
      .finally(() => {
        if (live) setLoading(false)
      })
    return () => {
      live = false
    }
  }, [submitted])

  function load(id: string) {
    const trimmed = id.trim()
    if (!trimmed) return
    setSubmitted(trimmed)
    setParams({ run: trimmed }, { replace: true })
  }

  return (
    <div>
      <div className="flex items-center gap-2 border-b border-border p-4">
        <input
          aria-label="run id"
          placeholder="run id"
          value={runId}
          onChange={(e) => setRunId(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && load(runId)}
          className="flex-1 rounded border border-border bg-void px-3 py-1.5 font-mono text-sm text-terminal outline-none focus:border-amber"
        />
        <button
          onClick={() => load(runId)}
          disabled={!runId.trim()}
          className="rounded border border-amber/50 bg-amber/10 px-4 py-1.5 font-mono text-sm text-amber disabled:opacity-40"
        >
          load
        </button>
      </div>

      {!submitted && <div className="p-6 font-mono text-sm text-ghost">enter a run id to view its tearsheet.</div>}
      {loading && <Loading label="loading tearsheet…" />}
      {error && <ErrorState error={error} />}
      {result && !loading && <TearsheetView result={result} />}
    </div>
  )
}
