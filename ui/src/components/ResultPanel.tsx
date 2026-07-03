// The shared backtest result renderer (§11.1) — used by RESULTS (Tearsheet) and the
// LAB. One component so a run reads the same however it was launched. It dispatches
// on verdict: an "invalid" user-source run shows the ValidationPanel, a funding-gap
// "rejected" run shows the RejectedState (data, not an error — §6.4/§19.1), and an
// "ok" run shows the honest tearsheet: metrics grid, equity + underwater curves,
// cost attribution (funding settled + cumulative, carry-forward h), fill-fidelity
// tiers, and any in-run rejections — nothing hidden (D28).

import type { BacktestResult } from '../api/types'
import { MetricsCard } from './MetricsCard'
import { EquityCurve } from './EquityCurve'
import { DrawdownChart } from './DrawdownChart'
import { ValidationPanel } from './ValidationPanel'
import { RejectedState } from './states'
import { fmtUsd } from '../lib/format'

export function ResultPanel({ result }: { result: BacktestResult }) {
  if (result.verdict === 'invalid' && result.validation) {
    return <ValidationPanel report={result.validation} />
  }
  if (result.verdict === 'rejected' && result.rejected) {
    return <RejectedState rejected={result.rejected} />
  }

  const cost = result.cost
  const equity = result.equity_curve ?? []

  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-2 font-mono text-xs uppercase tracking-widest text-amber">performance</h3>
        <MetricsCard result={result} />
      </section>

      <section>
        <h3 className="mb-2 font-mono text-xs uppercase tracking-widest text-amber">equity</h3>
        <div className="rounded border border-border bg-panel p-2">
          <EquityCurve series={equity} />
        </div>
      </section>

      <section>
        <h3 className="mb-2 font-mono text-xs uppercase tracking-widest text-amber">drawdown</h3>
        <div className="rounded border border-border bg-panel p-2">
          <DrawdownChart series={equity} />
        </div>
      </section>

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

      {result.fidelity_lines && result.fidelity_lines.length > 0 && (
        <section>
          <h3 className="mb-2 font-mono text-xs uppercase tracking-widest text-amber">fill fidelity</h3>
          <ul className="space-y-1 font-mono text-sm">
            {result.fidelity_lines.map((line, i) => (
              <li key={i} className="text-ghost">
                {line}
              </li>
            ))}
          </ul>
        </section>
      )}

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
