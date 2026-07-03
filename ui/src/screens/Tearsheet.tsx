// Screen 1 — Results / Tearsheet (§11.1, §12).
//
// Reads GET /api/v1/backtests/{run_id} and renders the honest tearsheet:
//   · raw Sharpe ALWAYS shown alongside the Deflated Sharpe and the trial count
//     (carry-forward i — a single un-tuned run has DSR n/a with N trials, never a
//     hidden or invented number), plus the annualization factor and effective range.
//   · cost attribution, with the funding line labeled "funding (settled, cumulative)"
//     (carry-forward h) and the settlement count beside it.
//   · fill-fidelity tiers (per-segment fidelity_lines).
//   · the equity curve.
// A "rejected" verdict (funding gap) is a completed run — it renders as a first-class
// RejectedState (missing legs + available ranges + fix), not an error (§19.1).

import { useEffect, useState } from 'react'
import { apiGet, ApiError } from '../api/client'
import type { BacktestResult } from '../api/types'
import { EquitySpark } from '../components/EquitySpark'
import { ErrorState, Loading, RejectedState } from '../components/states'
import { fmtNum, fmtPct, fmtRange, fmtUsd } from '../lib/format'

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded border border-border bg-panel p-3">
      <div className="text-xs uppercase tracking-wide text-ghost">{label}</div>
      <div className="mt-1 font-mono text-lg text-terminal">{value}</div>
      {sub ? <div className="mt-0.5 font-mono text-xs text-ghost">{sub}</div> : null}
    </div>
  )
}

export function TearsheetView({ result }: { result: BacktestResult }) {
  if (result.verdict === 'rejected' && result.rejected) {
    return (
      <div>
        <h2 className="px-4 pt-4 font-display text-xl text-terminal">{result.strategy ?? 'backtest'}</h2>
        <RejectedState rejected={result.rejected} />
      </div>
    )
  }

  const m = result.metrics
  const cost = result.cost
  // DSR is honest: a single un-tuned run carries deflated_sharpe=null → "n/a (N trials)".
  const dsr =
    result.deflated_sharpe === null || result.deflated_sharpe === undefined
      ? `n/a (${result.n_trials ?? 0} trials)`
      : fmtNum(result.deflated_sharpe)

  return (
    <div className="space-y-6 p-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-display text-xl text-terminal">{result.strategy ?? 'backtest'}</h2>
        <div className="font-mono text-xs text-ghost">
          {(result.universe ?? []).join(', ')} · {(result.venues ?? []).join(', ')}
          {result.clipped ? <span className="ml-2 text-amber">clipped to coverage</span> : null}
        </div>
      </header>

      {/* Performance — raw Sharpe always present next to DSR + trial count (carry-forward i). */}
      <section>
        <h3 className="mb-2 font-mono text-xs uppercase tracking-widest text-amber">performance</h3>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          <Stat label="sharpe (raw)" value={fmtNum(m?.sharpe)} sub={`annualized ${fmtNum(m?.annualized_sharpe)}`} />
          <Stat label="deflated sharpe" value={dsr} />
          <Stat label="sortino" value={fmtNum(m?.sortino)} sub={`annualized ${fmtNum(m?.annualized_sortino)}`} />
          <Stat label="max drawdown" value={fmtPct(m?.max_drawdown)} />
          <Stat label="win rate" value={fmtPct(result.win_rate)} />
          <Stat label="annualization ×" value={fmtNum(m?.annualization_factor)} sub={`${m?.n_returns ?? 0} bars`} />
          <Stat label="effective range" value={fmtRange(result.effective_range)} />
          <Stat label="evaluated" value={fmtRange({ start_ms: m?.evaluated_start_ts ?? 0, end_ms: m?.evaluated_end_ts ?? 0 })} />
        </div>
      </section>

      {/* Cost attribution — funding is settled + cumulative (carry-forward h). */}
      {cost && (
        <section>
          <h3 className="mb-2 font-mono text-xs uppercase tracking-widest text-amber">cost attribution</h3>
          <table className="w-full font-mono text-sm">
            <tbody>
              <tr className="border-b border-border">
                <td className="py-1 text-ghost">funding (settled, cumulative)</td>
                <td className="py-1 text-right text-terminal">{fmtUsd(cost.funding)}</td>
                <td className="py-1 pl-4 text-right text-ghost">{cost.funding_settlements} settlements</td>
              </tr>
              <tr className="border-b border-border">
                <td className="py-1 text-ghost">trading pnl</td>
                <td className="py-1 text-right text-terminal">{fmtUsd(cost.trading_pnl)}</td>
                <td />
              </tr>
              <tr className="border-b border-border">
                <td className="py-1 text-ghost">fees</td>
                <td className="py-1 text-right text-terminal">{fmtUsd(cost.fees)}</td>
                <td />
              </tr>
              <tr>
                <td className="py-1 text-ghost">slippage</td>
                <td className="py-1 text-right text-terminal">{fmtUsd(cost.slippage)}</td>
                <td />
              </tr>
            </tbody>
          </table>
        </section>
      )}

      {/* Equity curve. */}
      <section>
        <h3 className="mb-2 font-mono text-xs uppercase tracking-widest text-amber">equity</h3>
        <div className="rounded border border-border bg-panel p-2">
          <EquitySpark series={result.equity_curve ?? []} />
        </div>
      </section>

      {/* Fill-fidelity tiers — how trustworthy each segment's fills are (§11.1). */}
      {result.fidelity_lines && result.fidelity_lines.length > 0 && (
        <section>
          <h3 className="mb-2 font-mono text-xs uppercase tracking-widest text-amber">fill fidelity</h3>
          <ul className="space-y-1 font-mono text-sm text-terminal">
            {result.fidelity_lines.map((line, i) => (
              <li key={i} className="text-ghost">
                {line}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* In-run rejections — non-executable venue signals etc., displayed not hidden (D28). */}
      {result.rejections && result.rejections.length > 0 && (
        <section>
          <h3 className="mb-2 font-mono text-xs uppercase tracking-widest text-amber">in-run rejections</h3>
          <ul className="space-y-1 font-mono text-sm">
            {result.rejections.map((r, i) => (
              <li key={i} className="text-loss">
                {r.reason}
                {r.market ? ` · ${r.market}` : ''}
                {r.venue ? ` @ ${r.venue}` : ''}
                {r.detail ? <span className="text-ghost"> — {r.detail}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      )}

      {result.notes && result.notes.length > 0 && (
        <section>
          <ul className="space-y-0.5 font-mono text-xs text-ghost">
            {result.notes.map((note, i) => (
              <li key={i}>· {note}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

export default function Tearsheet() {
  const [runId, setRunId] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [error, setError] = useState<ApiError | Error | null>(null)
  const [loading, setLoading] = useState(false)

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

  return (
    <div>
      <div className="flex items-center gap-2 border-b border-border p-4">
        <input
          aria-label="run id"
          placeholder="run id"
          value={runId}
          onChange={(e) => setRunId(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && setSubmitted(runId.trim())}
          className="flex-1 rounded border border-border bg-void px-3 py-1.5 font-mono text-sm text-terminal outline-none focus:border-amber"
        />
        <button
          onClick={() => setSubmitted(runId.trim())}
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
