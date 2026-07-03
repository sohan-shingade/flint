// The performance metrics grid for a result (§11.1). Honest by construction: the
// raw Sharpe is ALWAYS shown next to the Deflated Sharpe and the trial count — a
// single un-tuned run reads "n/a (N trials)", never a hidden or invented DSR
// (carry-forward i) — with the annualization factor and the evaluated window beside
// them so a number is never divorced from the window it was measured over.

import type { BacktestResult } from '../api/types'
import { fmtNum, fmtPct, fmtRange } from '../lib/format'

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded border border-border bg-panel p-3">
      <div className="text-xs uppercase tracking-wide text-ghost">{label}</div>
      <div className="mt-1 font-mono text-lg text-terminal">{value}</div>
      {sub ? <div className="mt-0.5 font-mono text-xs text-ghost">{sub}</div> : null}
    </div>
  )
}

export function MetricsCard({ result }: { result: BacktestResult }) {
  const m = result.metrics
  const dsr =
    result.deflated_sharpe === null || result.deflated_sharpe === undefined
      ? `n/a (${result.n_trials ?? 0} trials)`
      : fmtNum(result.deflated_sharpe)

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      <Stat label="sharpe (raw)" value={fmtNum(m?.sharpe)} sub={`annualized ${fmtNum(m?.annualized_sharpe)}`} />
      <Stat label="deflated sharpe" value={dsr} />
      <Stat label="sortino" value={fmtNum(m?.sortino)} sub={`annualized ${fmtNum(m?.annualized_sortino)}`} />
      <Stat label="max drawdown" value={fmtPct(m?.max_drawdown)} />
      <Stat label="win rate" value={fmtPct(result.win_rate)} />
      <Stat label="annualization ×" value={fmtNum(m?.annualization_factor)} sub={`${m?.n_returns ?? 0} bars`} />
      <Stat label="effective range" value={fmtRange(result.effective_range)} />
      <Stat
        label="evaluated"
        value={fmtRange({ start_ms: m?.evaluated_start_ts ?? 0, end_ms: m?.evaluated_end_ts ?? 0 })}
      />
    </div>
  )
}
