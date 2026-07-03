// Screen 4 — Live monitor (§6.7, §12).
//
// Streams a paper session's monitor head over WS /paper/{id}/stream and renders it:
// open positions, funding accrual, liquidation distance, and the drift-attribution
// table (paper-vs-what the same engine would do — §6.7). Every block has a
// first-class empty/degraded state: no positions, no liq data, no drift all render
// as explicit notes, and a bad token / stream fault renders as an error, never a
// blank panel or a dead socket (§19.1).

import { useState } from 'react'
import { useLiveStream } from '../hooks/useLiveStream'
import { fmtNum, fmtPct, fmtUsd } from '../lib/format'

const STATUS_LABEL: Record<string, string> = {
  idle: 'idle',
  connecting: 'connecting…',
  open: 'live',
  closed: 'stream closed',
  error: 'error',
}

export default function LiveMonitor() {
  const [runId, setRunId] = useState('')
  const [submitted, setSubmitted] = useState('')
  const { snapshot, status, error } = useLiveStream(submitted)

  return (
    <div>
      <div className="flex items-center gap-2 border-b border-border p-4">
        <input
          aria-label="run id"
          placeholder="paper run id"
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
          connect
        </button>
        {submitted && (
          <span
            data-testid="ws-status"
            className={`rounded px-2 py-1 font-mono text-xs ${
              status === 'open' ? 'bg-gain/15 text-gain' : status === 'error' ? 'bg-loss/15 text-loss' : 'text-ghost'
            }`}
          >
            {STATUS_LABEL[status]}
          </span>
        )}
      </div>

      {!submitted && <div className="p-6 font-mono text-sm text-ghost">enter a paper run id to monitor it live.</div>}

      {error && (
        <div className="m-4 rounded border border-loss/50 bg-loss/5 p-4 font-mono text-sm text-loss" role="alert">
          stream error: {error}
        </div>
      )}

      {snapshot && (
        <div className="space-y-6 p-4">
          <header className="flex flex-wrap gap-6 font-mono text-sm">
            <div>
              <span className="text-ghost">status </span>
              <span className="text-terminal">{snapshot.status}</span>
            </div>
            <div>
              <span className="text-ghost">equity </span>
              <span className="text-terminal">{fmtUsd(snapshot.final_equity ?? null)}</span>
            </div>
            <div>
              <span className="text-ghost">funding accrued </span>
              <span className="text-terminal">{fmtUsd(snapshot.funding_accrued ?? null)}</span>
            </div>
          </header>

          {/* Positions. */}
          <section>
            <h3 className="mb-2 font-mono text-xs uppercase tracking-widest text-amber">positions</h3>
            {snapshot.positions.length === 0 ? (
              <div className="font-mono text-sm text-ghost">no open positions.</div>
            ) : (
              <table className="w-full font-mono text-sm">
                <thead>
                  <tr className="text-left text-ghost">
                    <th className="py-1">market</th>
                    <th>venue</th>
                    <th className="text-right">size</th>
                    <th className="text-right">entry</th>
                    <th className="text-right">mark</th>
                    <th className="text-right">uPnL</th>
                  </tr>
                </thead>
                <tbody>
                  {snapshot.positions.map((p, i) => (
                    <tr key={i} className="border-t border-border text-terminal">
                      <td className="py-1">{String(p.market ?? '—')}</td>
                      <td>{String(p.venue ?? '—')}</td>
                      <td className="text-right">{fmtNum(p.size as number)}</td>
                      <td className="text-right">{fmtNum(p.entry_price as number)}</td>
                      <td className="text-right">{fmtNum(p.mark_price as number)}</td>
                      <td className="text-right">{fmtUsd(p.unrealized_pnl as number)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          {/* Liquidation distance. */}
          <section>
            <h3 className="mb-2 font-mono text-xs uppercase tracking-widest text-amber">liquidation distance</h3>
            {Object.keys(snapshot.liq_distances_pct).length === 0 ? (
              <div className="font-mono text-sm text-ghost">no liquidation exposure.</div>
            ) : (
              <table className="font-mono text-sm">
                <tbody>
                  {Object.entries(snapshot.liq_distances_pct).map(([market, pct]) => (
                    <tr key={market}>
                      <td className="py-1 pr-6 text-ghost">{market}</td>
                      <td className={pct < 0.1 ? 'text-loss' : 'text-terminal'}>{fmtPct(pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          {/* Drift attribution — why paper diverges from the ideal fill (§6.7). */}
          <section>
            <h3 className="mb-2 font-mono text-xs uppercase tracking-widest text-amber">drift attribution</h3>
            {Object.keys(snapshot.drift).length === 0 ? (
              <div className="font-mono text-sm text-ghost">no drift recorded.</div>
            ) : (
              <table className="font-mono text-sm" data-testid="drift-table">
                <tbody>
                  {Object.entries(snapshot.drift).map(([k, v]) => (
                    <tr key={k} className="border-t border-border">
                      <td className="py-1 pr-6 text-ghost">{k}</td>
                      <td className="text-terminal">{typeof v === 'number' ? fmtNum(v) : String(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </div>
      )}
    </div>
  )
}
