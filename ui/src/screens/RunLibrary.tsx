// Screen 5 — Run library + two-run diff (§11.2, §12, carry-forward f).
//
// Lists the tenant's runs (GET /runs) in a sortable table, and diffs any two of
// them (GET /runs/compare). The diff renders runlib's honesty warnings prominently —
// most importantly the DIFFERENT EFFECTIVE RANGE warning: two Sharpes measured over
// different windows are not the same measurement, and the compare view says so
// rather than letting the numbers imply otherwise (§6.3).

import { useEffect, useMemo, useState } from 'react'
import { apiGet, ApiError, encodeMulti } from '../api/client'
import type { RunComparison, RunRow } from '../api/types'
import { ErrorState, Empty, Loading } from '../components/states'
import { fmtDate, fmtNum, fmtRange } from '../lib/format'

type SortKey = 'created_ts' | 'strategy' | 'sharpe'

function sharpeOf(r: RunRow): number | null {
  const v = r.metrics?.sharpe
  return typeof v === 'number' ? v : null
}

export default function RunLibrary() {
  const [runs, setRuns] = useState<RunRow[] | null>(null)
  const [error, setError] = useState<ApiError | Error | null>(null)
  const [sortKey, setSortKey] = useState<SortKey>('created_ts')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [selected, setSelected] = useState<string[]>([])
  const [cmp, setCmp] = useState<RunComparison | null>(null)
  const [cmpError, setCmpError] = useState<ApiError | Error | null>(null)

  useEffect(() => {
    let live = true
    apiGet<{ runs: RunRow[] }>('/runs')
      .then((r) => live && setRuns(r.runs))
      .catch((e) => live && setError(e))
    return () => {
      live = false
    }
  }, [])

  const sorted = useMemo(() => {
    if (!runs) return []
    const val = (r: RunRow): number | string | null =>
      sortKey === 'strategy' ? r.strategy : sortKey === 'sharpe' ? sharpeOf(r) : r.created_ts
    const dir = sortDir === 'asc' ? 1 : -1
    return [...runs].sort((a, b) => {
      const va = val(a)
      const vb = val(b)
      if (va === null) return 1
      if (vb === null) return -1
      return va < vb ? -dir : va > vb ? dir : 0
    })
  }, [runs, sortKey, sortDir])

  function toggleSort(key: SortKey) {
    if (key === sortKey) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  function toggleSelect(runId: string) {
    setSelected((cur) =>
      cur.includes(runId) ? cur.filter((x) => x !== runId) : cur.length >= 2 ? [cur[1], runId] : [...cur, runId],
    )
    setCmp(null)
    setCmpError(null)
  }

  function runCompare() {
    setCmp(null)
    setCmpError(null)
    apiGet<RunComparison>(`/runs/compare${encodeMulti({ run_id: selected })}`)
      .then(setCmp)
      .catch(setCmpError)
  }

  const arrow = (key: SortKey) => (key === sortKey ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '')

  if (error) return <ErrorState error={error} />
  if (!runs) return <Loading label="loading run library…" />
  if (runs.length === 0) return <Empty message="no runs yet — run a backtest to populate the library." />

  return (
    <div className="p-4">
      <div className="mb-3 flex items-center gap-3">
        <button
          onClick={runCompare}
          disabled={selected.length !== 2}
          className="rounded border border-amber/50 bg-amber/10 px-4 py-1.5 font-mono text-sm text-amber disabled:opacity-40"
        >
          compare 2
        </button>
        <span className="font-mono text-xs text-ghost">{selected.length}/2 selected</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full font-mono text-sm">
          <thead>
            <tr className="text-left text-ghost">
              <th className="py-1"></th>
              <th className="cursor-pointer py-1 hover:text-terminal" onClick={() => toggleSort('strategy')}>
                strategy{arrow('strategy')}
              </th>
              <th>run</th>
              <th>kind</th>
              <th className="cursor-pointer hover:text-terminal" onClick={() => toggleSort('created_ts')}>
                created{arrow('created_ts')}
              </th>
              <th>effective range</th>
              <th className="cursor-pointer text-right hover:text-terminal" onClick={() => toggleSort('sharpe')}>
                sharpe{arrow('sharpe')}
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.run_id} data-testid={`run-${r.run_id}`} className="border-t border-border text-terminal">
                <td className="py-1">
                  <input
                    type="checkbox"
                    aria-label={`select ${r.run_id}`}
                    checked={selected.includes(r.run_id)}
                    onChange={() => toggleSelect(r.run_id)}
                  />
                </td>
                <td>{r.strategy}</td>
                <td className="text-ghost">{r.run_id.slice(0, 8)}</td>
                <td className="text-ghost">{r.kind}</td>
                <td className="text-ghost">{fmtDate(r.created_ts)}</td>
                <td className="text-ghost">
                  {fmtRange(
                    r.effective_start_ts !== null && r.effective_end_ts !== null
                      ? { start_ms: r.effective_start_ts, end_ms: r.effective_end_ts }
                      : null,
                  )}
                </td>
                <td className="text-right">{fmtNum(sharpeOf(r))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {cmpError && <ErrorState error={cmpError} />}

      {cmp && (
        <section className="mt-6" data-testid="comparison">
          <h3 className="mb-2 font-mono text-xs uppercase tracking-widest text-amber">comparison</h3>

          {cmp.warnings.length > 0 && (
            <div className="mb-3 rounded border border-amber/50 bg-amber-glow p-3 font-mono text-sm" role="alert">
              <div className="mb-1 text-xs uppercase tracking-wide text-amber">honesty warnings</div>
              <ul className="space-y-0.5 text-terminal">
                {cmp.warnings.map((w, i) => (
                  <li key={i} className="text-amber">
                    ! {w}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <table className="font-mono text-sm">
            <thead>
              <tr className="text-left text-ghost">
                <th className="py-1 pr-6">metric</th>
                {cmp.run_ids.map((id) => (
                  <th key={id} className="pr-6">
                    {id.slice(0, 8)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.entries(cmp.metrics).map(([metric, row]) => (
                <tr key={metric} className="border-t border-border">
                  <td className="py-1 pr-6 text-ghost">{metric}</td>
                  {row.map((v, i) => (
                    <td key={i} className="pr-6 text-terminal">
                      {v === null ? 'n/a' : fmtNum(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  )
}
