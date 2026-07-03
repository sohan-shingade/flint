// Screen — Home / Dashboard (§12). The landing overview: a compact hero, status
// cards (run counts, template count, server health/version), and a recent-runs
// table that deep-links into RESULTS. Everything is read through the api client;
// counts are honest — a "rejected" run is one whose Run-Library note records the
// funding gate firing (services.runs persists `note="rejected: <code>"`), and the
// dashboard never invents a number it did not load.

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AsciiFire from '../components/AsciiFire'
import { apiGet, ApiError } from '../api/client'
import type { RunRow, SystemHealth, TemplatesResponse } from '../api/types'
import { ErrorState, Loading } from '../components/states'
import { fmtDate, fmtNum } from '../lib/format'

function isRejected(r: RunRow): boolean {
  return (r.note ?? '').toLowerCase().startsWith('rejected')
}

function sharpeOf(r: RunRow): number | null {
  const v = r.metrics?.sharpe
  return typeof v === 'number' ? v : null
}

function Card({ label, value, tone }: { label: string; value: string; tone?: 'amber' | 'gain' | 'loss' }) {
  const color = tone === 'amber' ? 'text-amber' : tone === 'gain' ? 'text-gain' : tone === 'loss' ? 'text-loss' : 'text-terminal'
  return (
    <div className="border border-border bg-surface/80 p-4">
      <div className="mb-1 text-[10px] uppercase tracking-[0.2em] text-ghost">{label}</div>
      <div className={`font-mono text-2xl tabular-nums ${color}`}>{value}</div>
    </div>
  )
}

export default function Dashboard() {
  const [runs, setRuns] = useState<RunRow[] | null>(null)
  const [templates, setTemplates] = useState<TemplatesResponse | null>(null)
  const [health, setHealth] = useState<SystemHealth | null>(null)
  const [error, setError] = useState<ApiError | Error | null>(null)

  useEffect(() => {
    let live = true
    apiGet<{ runs: RunRow[] }>('/runs')
      .then((r) => live && setRuns(r.runs))
      .catch((e) => live && setError(e))
    apiGet<TemplatesResponse>('/templates')
      .then((t) => live && setTemplates(t))
      .catch(() => {})
    apiGet<SystemHealth>('/system/health')
      .then((h) => live && setHealth(h))
      .catch(() => {})
    return () => {
      live = false
    }
  }, [])

  if (error) return <ErrorState error={error} />
  if (!runs) return <Loading label="loading dashboard…" />

  const rejected = runs.filter(isRejected).length
  const ok = runs.length - rejected
  const recent = [...runs].sort((a, b) => b.created_ts - a.created_ts).slice(0, 8)

  return (
    <div className="space-y-6">
      {/* hero */}
      <div className="relative overflow-hidden border-b border-border pb-6">
        <div className="flex items-center gap-8">
          <div className="hidden shrink-0 md:block" style={{ marginBottom: -8 }}>
            <AsciiFire cols={44} rows={16} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="mb-1 flex items-baseline gap-4">
              <h1 className="text-4xl font-bold leading-none tracking-[0.25em] text-terminal">FLINT</h1>
              <span className="text-[10px] tracking-[0.3em] text-ghost">PERP / DEX STRATEGY LAB</span>
            </div>
            <p className="mb-4 text-sm tracking-[0.25em] text-amber/60">STRIKE ALPHA ON PERPS</p>
            <div className="flex flex-wrap items-center gap-6 text-[11px]">
              <div className="flex items-center gap-2">
                <span className={`h-1.5 w-1.5 rounded-full ${health?.ok ? 'bg-phosphor' : 'bg-loss'}`} />
                <span className={health?.ok ? 'text-phosphor' : 'text-loss'}>
                  {health?.ok ? 'ONLINE' : 'OFFLINE'}
                </span>
              </div>
              <div>
                <span className="text-ghost">VERSION </span>
                <span className="text-amber tabular-nums">{health?.version ?? '?.?.?'}</span>
              </div>
              <div>
                <span className="text-ghost">TEMPLATES </span>
                <span className="text-terminal tabular-nums">{templates?.templates.length ?? '—'}</span>
              </div>
              <div>
                <span className="text-ghost">VENUE </span>
                <span className="text-amber">HYPERLIQUID</span>
              </div>
            </div>
          </div>
          <div className="hidden shrink-0 flex-col gap-2 lg:flex">
            <Link
              to="/lab"
              className="bg-amber px-4 py-2 text-center text-[11px] font-semibold tracking-[0.15em] text-void transition-colors hover:bg-amber-dim"
            >
              OPEN LAB
            </Link>
            <Link
              to="/docs"
              className="border border-border px-4 py-2 text-center text-[11px] tracking-[0.15em] text-ghost transition-colors hover:border-border-bright hover:text-terminal"
            >
              READ DOCS
            </Link>
          </div>
        </div>
      </div>

      {/* status cards */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Card label="runs" value={String(runs.length)} tone="amber" />
        <Card label="ok" value={String(ok)} tone="gain" />
        <Card label="rejected (funding gate)" value={String(rejected)} tone={rejected > 0 ? 'loss' : undefined} />
        <Card label="templates" value={String(templates?.templates.length ?? '—')} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* recent runs */}
        <div className="border border-border bg-surface/60 lg:col-span-2">
          <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
            <span className="h-1.5 w-1.5 bg-amber/50" />
            <span className="text-[10px] tracking-[0.2em] text-ghost">RECENT RUNS</span>
          </div>
          {recent.length === 0 ? (
            <div className="p-6 font-mono text-sm text-ghost">no runs yet — open the LAB to run a backtest.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full font-mono text-[11px]">
                <thead>
                  <tr className="border-b border-border text-left text-[9px] tracking-[0.15em] text-ghost">
                    <th className="px-4 py-2">RUN</th>
                    <th className="px-4 py-2">STRATEGY</th>
                    <th className="px-4 py-2">VERDICT</th>
                    <th className="px-4 py-2 text-right">SHARPE</th>
                    <th className="px-4 py-2">CREATED</th>
                  </tr>
                </thead>
                <tbody>
                  {recent.map((r) => (
                    <tr
                      key={r.run_id}
                      data-testid={`recent-${r.run_id}`}
                      className="border-b border-border/20 hover:bg-amber-glow/50"
                    >
                      <td className="px-4 py-1.5">
                        <Link to={`/results?run=${encodeURIComponent(r.run_id)}`} className="text-amber/80 hover:text-amber">
                          {r.run_id.slice(0, 8)}
                        </Link>
                      </td>
                      <td className="px-4 py-1.5 text-terminal">{r.strategy}</td>
                      <td className={`px-4 py-1.5 ${isRejected(r) ? 'text-loss' : 'text-gain'}`}>
                        {isRejected(r) ? 'rejected' : 'ok'}
                      </td>
                      <td className="px-4 py-1.5 text-right tabular-nums text-terminal">{fmtNum(sharpeOf(r))}</td>
                      <td className="px-4 py-1.5 text-ghost">{fmtDate(r.created_ts)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* quick start */}
        <div className="space-y-3">
          <div className="border border-border bg-surface/60 p-4">
            <div className="mb-2 text-[10px] tracking-[0.2em] text-ghost">QUICK START</div>
            <div className="space-y-2 font-mono text-[11px] leading-relaxed">
              <div className="text-ghost"># serve the lab locally</div>
              <div>
                <span className="text-amber/60">$</span> <span className="text-terminal">flint serve</span>
              </div>
              <div className="pt-2 text-ghost">
                press <span className="text-amber">1–8</span> to switch pages · open the{' '}
                <Link to="/lab" className="text-amber hover:underline">
                  LAB
                </Link>{' '}
                to run a template or your own source · read the{' '}
                <Link to="/docs" className="text-amber hover:underline">
                  DOCS
                </Link>
                .
              </div>
            </div>
          </div>
          <Link to="/data" className="group block">
            <div className="border border-border bg-surface/60 p-4 transition-all hover:border-amber/20 hover:bg-panel">
              <div className="mb-1 text-[12px] font-medium text-terminal group-hover:text-amber">Data Explorer</div>
              <p className="text-[10px] leading-relaxed text-ghost">
                Check candle / funding / OI coverage per market × venue before a run.
              </p>
            </div>
          </Link>
          <Link to="/funding" className="group block">
            <div className="border border-border bg-surface/60 p-4 transition-all hover:border-amber/20 hover:bg-panel">
              <div className="mb-1 text-[12px] font-medium text-terminal group-hover:text-amber">Funding Lab</div>
              <p className="text-[10px] leading-relaxed text-ghost">
                Annualized carry across markets × venues, with cross-venue dislocation.
              </p>
            </div>
          </Link>
        </div>
      </div>
    </div>
  )
}
