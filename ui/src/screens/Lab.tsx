// Screen — Strategy Lab (§13.2, §12). The centerpiece: launch a backtest either
// from a built-in TEMPLATE (picked from GET /templates, params auto-editable) or
// from your own SOURCE in the Monaco EDITOR. Both share one run-config (universe,
// date range, resolution, capital) and one code path: submit → poll status → load
// the result into the shared ResultPanel. A user-source run that fails validation
// comes back as verdict="invalid" and renders as a first-class report (never a blank
// screen); a funding-gap run comes back "rejected" (§6.4). Hyperliquid is the only
// executable venue in v1.

import { useEffect, useMemo, useRef, useState } from 'react'
import { apiGet, apiPost, ApiError } from '../api/client'
import type {
  BacktestResult,
  BacktestStatus,
  Range,
  SubmitResponse,
  Template,
  TemplatesResponse,
} from '../api/types'
import CodeEditor from '../components/CodeEditor'
import { ResultPanel } from '../components/ResultPanel'
import type { GranularityActions } from '../components/GranularityOptionsCard'
import { ErrorState, Loading } from '../components/states'

type Mode = 'template' | 'editor'
type ParamValue = string | boolean

const DEFAULT_UNIVERSE = 'BTC-PERP,ETH-PERP,SOL-PERP'
const VENUES = ['hyperliquid'] // the only executable venue in v1 (D28)
const RESOLUTIONS: Array<[number, string]> = [
  [300, '5m'],
  [900, '15m'],
  [3600, '1h'],
  [14400, '4h'],
  [86400, '1d'],
]

const STARTER_SOURCE = `from flint.strategy import Strategy
from flint.core.models import Signal


class MyStrategy(Strategy):
    """Minimal moving-average cross. on_candle returns a list[Signal]."""

    params = {"fast": 10, "slow": 30, "size_usd": 1000.0}

    def on_candle(self, candle, history, ctx):
        closes = [c.close for c in history]
        if len(closes) < self.params["slow"]:
            return []
        fast = sum(closes[-self.params["fast"]:]) / self.params["fast"]
        slow = sum(closes[-self.params["slow"]:]) / self.params["slow"]
        pos = ctx.position(candle.market, candle.venue)
        if fast > slow and pos is None:
            return [Signal.long(candle.market, candle.venue, size_usd=self.params["size_usd"])]
        if fast < slow and pos is not None:
            return [Signal.close(candle.market, candle.venue)]
        return []
`

function toInput(d: Date): string {
  // UTC datetime-local string (YYYY-MM-DDTHH:MM); round-trips as UTC via msFromInput.
  return d.toISOString().slice(0, 16)
}

function msFromInput(v: string): number {
  // datetime-local carries no zone; we treat the field as UTC (append Z) so the
  // window the user picks is the window the engine evaluates.
  return v ? new Date(`${v}:00Z`).getTime() : NaN
}

function parseUniverse(s: string): string[] {
  return s
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean)
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}

export default function Lab() {
  const [mode, setMode] = useState<Mode>('template')

  // Templates (TEMPLATE mode).
  const [templates, setTemplates] = useState<Template[] | null>(null)
  const [templatesError, setTemplatesError] = useState<ApiError | Error | null>(null)
  const [selected, setSelected] = useState<string>('')
  const [paramValues, setParamValues] = useState<Record<string, ParamValue>>({})

  // Source (EDITOR mode).
  const [source, setSource] = useState(STARTER_SOURCE)

  // Shared run-config.
  const now = useMemo(() => new Date(), [])
  const [universe, setUniverse] = useState(DEFAULT_UNIVERSE)
  const [resolution, setResolution] = useState(3600)
  const [capital, setCapital] = useState('100000')
  const [startInput, setStartInput] = useState(() => toInput(new Date(now.getTime() - 30 * 86400_000)))
  const [endInput, setEndInput] = useState(() => toInput(now))

  // Run lifecycle.
  const [running, setRunning] = useState(false)
  const [phase, setPhase] = useState('')
  const [runError, setRunError] = useState<ApiError | Error | null>(null)
  const [result, setResult] = useState<BacktestResult | null>(null)
  const liveRef = useRef(true)

  useEffect(() => {
    liveRef.current = true
    return () => {
      liveRef.current = false
    }
  }, [])

  useEffect(() => {
    let live = true
    apiGet<TemplatesResponse>('/templates')
      .then((t) => {
        if (!live) return
        setTemplates(t.templates)
        if (t.templates.length > 0) selectTemplate(t.templates[0])
      })
      .catch((e) => live && setTemplatesError(e))
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function selectTemplate(t: Template) {
    setSelected(t.name)
    const values: Record<string, ParamValue> = {}
    for (const [k, v] of Object.entries(t.params)) {
      values[k] = typeof v === 'boolean' ? v : String(v)
    }
    setParamValues(values)
  }

  const grouped = useMemo(() => {
    const g: Record<string, Template[]> = {}
    for (const t of templates ?? []) (g[t.category] ??= []).push(t)
    return g
  }, [templates])

  const activeTemplate = useMemo(
    () => (templates ?? []).find((t) => t.name === selected) ?? null,
    [templates, selected],
  )

  function overridesFromParams(t: Template): Record<string, unknown> {
    const out: Record<string, unknown> = {}
    for (const [k, def] of Object.entries(t.params)) {
      const v = paramValues[k]
      if (typeof def === 'number') out[k] = v === '' || v === undefined ? def : Number(v)
      else if (typeof def === 'boolean') out[k] = Boolean(v)
      else out[k] = v ?? ''
    }
    return out
  }

  async function loadResult(runId: string): Promise<BacktestResult> {
    for (let i = 0; i < 600; i++) {
      const s = await apiGet<BacktestStatus>(`/backtests/${encodeURIComponent(runId)}/status`)
      if (s.state === 'done') return apiGet<BacktestResult>(`/backtests/${encodeURIComponent(runId)}`)
      if (s.state === 'failed' || s.state === 'cancelled') {
        const e = s.error ?? {}
        throw new ApiError(0, e.code ?? s.state, e.message ?? `run ${s.state}`, e.detail, e.hint)
      }
      if (liveRef.current) setPhase(s.state === 'queued' ? `queued (position ${s.queue_position})` : 'running…')
      await sleep(400)
    }
    throw new ApiError(0, 'timeout', 'the backtest did not finish in time', undefined, 'check the server logs')
  }

  // `patch` lets a granularity-rejection option resubmit the same run at a lower
  // tier (run at bars) or over the clipped window — the ways out of a tier gap
  // are one-click re-runs, not a manual reconfigure (§B7).
  async function run(patch?: { granularity?: string; range?: Range }) {
    setRunError(null)
    setResult(null)

    const start_ms = patch?.range?.start_ms ?? msFromInput(startInput)
    const end_ms = patch?.range?.end_ms ?? msFromInput(endInput)
    const uni = parseUniverse(universe)
    if (Number.isNaN(start_ms) || Number.isNaN(end_ms) || end_ms <= start_ms) {
      setRunError(new Error('pick a valid date range (end after start)'))
      return
    }
    if (uni.length === 0) {
      setRunError(new Error('enter at least one market (e.g. BTC-PERP)'))
      return
    }

    const base = {
      universe: uni,
      venues: VENUES,
      range: { start_ms, end_ms },
      resolution_s: resolution,
      initial_capital: capital,
      granularity: patch?.granularity ?? 'auto',
    }

    setRunning(true)
    setPhase('submitting…')
    try {
      let runId: string
      if (mode === 'template') {
        if (!activeTemplate) throw new Error('select a template first')
        const res = await apiPost<SubmitResponse>('/backtests', {
          ...base,
          strategy: activeTemplate.name,
          overrides: overridesFromParams(activeTemplate),
        })
        runId = res.run_id
      } else {
        const res = await apiPost<SubmitResponse>('/backtests/source', { ...base, source })
        runId = res.run_id
      }
      const r = await loadResult(runId)
      if (liveRef.current) setResult(r)
    } catch (e) {
      if (liveRef.current) setRunError(e as ApiError | Error)
    } finally {
      if (liveRef.current) {
        setRunning(false)
        setPhase('')
      }
    }
  }

  // Backfill via Tardis (a granularity-gap "way out") fires the pull_data job for
  // the tick kind over the vendor window (or the current range), then invites a
  // re-run once coverage lands. Needs TARDIS_API_KEY server-side to actually pull.
  async function backfill(window: Range | null) {
    const uni = parseUniverse(universe)
    const range = window ?? { start_ms: msFromInput(startInput), end_ms: msFromInput(endInput) }
    if (uni.length === 0) return
    setPhase('backfilling via tardis…')
    try {
      await apiPost('/data/pull', { market: uni[0], venues: VENUES, kind: 'trades', range })
      setPhase('backfill submitted — re-run to pick up new coverage')
    } catch (e) {
      if (liveRef.current) setRunError(e as ApiError | Error)
    }
  }

  const granularityActions: GranularityActions = {
    onRunAtBars: (g) => run({ granularity: g }),
    onClip: (range) => run({ granularity: result?.rejected?.granularity, range }),
    onBackfill: backfill,
  }

  const tab = (m: Mode, label: string) => (
    <button
      onClick={() => setMode(m)}
      className={`border-b-2 px-4 py-2 font-mono text-xs uppercase tracking-widest transition-colors ${
        mode === m ? 'border-amber text-amber' : 'border-transparent text-ghost hover:text-terminal'
      }`}
    >
      {label}
    </button>
  )

  const fieldCls =
    'w-full rounded border border-border bg-void px-3 py-1.5 font-mono text-sm text-terminal outline-none focus:border-amber'

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-1 border-b border-border">
        {tab('template', 'template')}
        {tab('editor', 'editor')}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        {/* left — the source of the run (template picker or editor) */}
        <div className="min-w-0 space-y-4">
          {mode === 'template' ? (
            <div className="space-y-4">
              {templatesError && <ErrorState error={templatesError} />}
              {!templates && !templatesError && <Loading label="loading templates…" />}
              {templates && (
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div className="space-y-3">
                    {Object.entries(grouped).map(([cat, list]) => (
                      <div key={cat}>
                        <div className="mb-1 font-mono text-[10px] uppercase tracking-[0.2em] text-ghost">{cat}</div>
                        <div className="space-y-1">
                          {list.map((t) => (
                            <button
                              key={t.name}
                              data-testid={`tmpl-${t.name}`}
                              onClick={() => selectTemplate(t)}
                              className={`block w-full border px-3 py-2 text-left font-mono text-xs transition-colors ${
                                selected === t.name
                                  ? 'border-amber/50 bg-amber-glow text-amber'
                                  : 'border-border text-terminal hover:border-border-bright'
                              }`}
                            >
                              {t.name}
                              {t.is_ml ? <span className="ml-1 text-[9px] text-ghost">(ml)</span> : null}
                            </button>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* param editor for the selected template */}
                  <div className="border border-border bg-surface/60 p-3">
                    {activeTemplate ? (
                      <>
                        <div className="mb-1 font-mono text-sm text-terminal">{activeTemplate.name}</div>
                        <p className="mb-3 font-mono text-xs text-ghost">{activeTemplate.summary}</p>
                        {Object.keys(activeTemplate.params).length === 0 ? (
                          <div className="font-mono text-xs text-ghost">no parameters.</div>
                        ) : (
                          <div className="space-y-2">
                            {Object.entries(activeTemplate.params).map(([k, def]) => (
                              <label key={k} className="flex items-center justify-between gap-3">
                                <span className="font-mono text-xs text-ghost">{k}</span>
                                {typeof def === 'boolean' ? (
                                  <input
                                    type="checkbox"
                                    aria-label={k}
                                    checked={Boolean(paramValues[k])}
                                    onChange={(e) => setParamValues((p) => ({ ...p, [k]: e.target.checked }))}
                                  />
                                ) : (
                                  <input
                                    aria-label={k}
                                    type={typeof def === 'number' ? 'number' : 'text'}
                                    value={String(paramValues[k] ?? '')}
                                    onChange={(e) => setParamValues((p) => ({ ...p, [k]: e.target.value }))}
                                    className="w-32 rounded border border-border bg-void px-2 py-1 text-right font-mono text-xs text-terminal outline-none focus:border-amber"
                                  />
                                )}
                              </label>
                            ))}
                          </div>
                        )}
                      </>
                    ) : (
                      <div className="font-mono text-xs text-ghost">select a template.</div>
                    )}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="h-[440px] overflow-hidden rounded border border-border">
              <CodeEditor value={source} onChange={setSource} height="440px" />
            </div>
          )}
        </div>

        {/* right — run config */}
        <aside className="space-y-3">
          <div className="space-y-3 border border-border bg-surface/60 p-3">
            <label className="block">
              <span className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-ghost">universe</span>
              <input aria-label="universe" value={universe} onChange={(e) => setUniverse(e.target.value)} className={fieldCls} />
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-ghost">start (UTC)</span>
                <input
                  aria-label="start"
                  type="datetime-local"
                  value={startInput}
                  onChange={(e) => setStartInput(e.target.value)}
                  className={fieldCls}
                />
              </label>
              <label className="block">
                <span className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-ghost">end (UTC)</span>
                <input
                  aria-label="end"
                  type="datetime-local"
                  value={endInput}
                  onChange={(e) => setEndInput(e.target.value)}
                  className={fieldCls}
                />
              </label>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-ghost">resolution</span>
                <select
                  aria-label="resolution"
                  value={resolution}
                  onChange={(e) => setResolution(Number(e.target.value))}
                  className={fieldCls}
                >
                  {RESOLUTIONS.map(([s, label]) => (
                    <option key={s} value={s}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-ghost">capital</span>
                <input aria-label="capital" value={capital} onChange={(e) => setCapital(e.target.value)} className={fieldCls} />
              </label>
            </div>
            <div className="font-mono text-[10px] text-ghost">venue: hyperliquid (executable)</div>
          </div>

          <button
            onClick={() => run()}
            disabled={running}
            className="w-full rounded border border-amber/50 bg-amber/10 px-4 py-2 font-mono text-sm uppercase tracking-widest text-amber transition-colors hover:bg-amber/20 disabled:opacity-40"
          >
            {running ? phase || 'running…' : 'run backtest'}
          </button>
        </aside>
      </div>

      {/* result */}
      {running && !result && <Loading label={phase || 'running…'} />}
      {runError && <ErrorState error={runError} />}
      {result && (
        <div className="border-t border-border pt-4">
          <ResultPanel result={result} granularityActions={granularityActions} />
        </div>
      )}
    </div>
  )
}
