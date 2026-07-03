// Wire shapes returned by the 7.1 REST/WS surface (§12). These mirror the JSON
// the services assemble — hand-kept in sync with flint/services/*.py. Fields the
// services may omit are optional here so a screen renders what it has.

export interface Range {
  start_ms: number
  end_ms: number
}

// ---- backtest result (GET /backtests/{id}) — backtest._summary / _rejection ---

export interface Metrics {
  sharpe: number
  annualized_sharpe: number
  sortino: number
  annualized_sortino: number
  max_drawdown: number
  mean_bar_return: number
  n_returns: number
  annualization_factor: number
  evaluated_start_ts: number
  evaluated_end_ts: number
}

// ---- user-source validation (verdict === "invalid") — services.strategy_source ----
// A validation failure is a *completed* run whose verdict is "invalid": the sandbox
// or the static screen rejected the source. It is data an author revises against
// (line-precise), never a stack trace (§13.2, D25).
export interface ScreenViolation {
  line: number
  col: number
  code: string
  message: string
}

export interface LookaheadFinding {
  category: string
  message: string
  line: number | null
}

export interface SandboxError {
  type: string
  message: string
}

export interface ValidationReport {
  valid: boolean
  stage: string // "ok" | "screen" | "sandbox"
  screen_violations: ScreenViolation[]
  sandbox_error: SandboxError | null
  leak_detected: boolean
  lookahead: {
    summary: string
    findings: LookaheadFinding[]
    checks_run: string[]
    blind_spots: string[]
    dynamic_probe_ran: boolean
  }
}

export interface Cost {
  funding: number
  trading_pnl: number
  fees: number
  slippage: number
  funding_settlements: number
}

export interface InRunRejection {
  reason: string
  detail?: string
  ts?: number
  venue?: string
  market?: string
  action?: string
}

// A granularity-rejection option — one machine-readable way out of a tier gap
// (§B7). `action` discriminates the shape; extra fields ride alongside.
export interface RejectionOption {
  action: 'run_bars' | 'clip_to_coverage' | 'vendor_backfill' | 'record_forward'
  granularity?: string // run_bars: the tier to fall back to ("candles")
  effective_range?: Range // clip_to_coverage: the largest covered window
  vendor?: string // vendor_backfill: "tardis"
  requires_secret?: string // vendor_backfill: "TARDIS_API_KEY"
  available?: Record<string, Record<string, Range | null>> | null // vendor_backfill window(s)
  hint?: string // record_forward: the CLI invocation
}

// The structured funding-gap (or other scarcity) rejection — data, not an error.
// A funding_gap uses missing/available; a granularity_unavailable adds the
// per-leg per-kind `coverage` and the machine-readable `options` (§B7).
export interface RejectedPayload {
  code: string
  message: string
  missing: string[]
  available: Record<string, Range | null>
  hint: string
  granularity?: string // requested tier (granularity_unavailable)
  coverage?: Record<string, Record<string, Range[]>> // leg → kind → covered pieces
  options?: RejectionOption[]
}

// One [ts_ms, value] point of the equity curve. The wire ships time with each
// point so bar runs (regular spacing) and tick runs (irregular) share one shape.
export type EquityPoint = [number, number]

export interface BacktestResult {
  verdict: 'ok' | 'rejected' | 'invalid'
  run_id?: string
  validation?: ValidationReport // present only when verdict === "invalid"
  strategy?: string
  universe?: string[]
  venues?: string[]
  fill_mode?: string
  requested_range?: Range
  effective_range?: Range
  clipped?: boolean
  metrics?: Metrics
  deflated_sharpe?: number | null
  n_trials?: number
  win_rate?: number
  cost?: Cost | null
  equity_curve?: EquityPoint[]
  rejections?: InRunRejection[]
  fidelity_lines?: string[]
  notes?: string[]
  timing?: Record<string, number>
  // the resolved granularity tier + its per-leg provenance lines (§B7)
  granularity?: string
  granularity_detail?: string[]
  // present only when verdict === "rejected"
  rejected?: RejectedPayload
}

// ---- data coverage (GET /data/coverage) — services.data_coverage ------------

// One covered piece with the provenance that would serve it (§B7 ladder).
export interface CoveragePiece {
  start_ms: number
  end_ms: number
  source: string // hl_rest | tardis | recorder | local_cache | free_venue_provider
}

export interface Coverage {
  market: string
  venue: string
  coverage: {
    candles?: Range | null
    funding?: Range | null
    oi?: Range | null
    [kind: string]: Range | null | undefined
  }
  // per-kind covered pieces with provenance (drives the ladder's segment colors)
  detail?: Record<string, CoveragePiece[]>
  // one RangeSet per granularity tier (candles/ticks/book) — the coverage ladder
  tiers?: Record<string, Range[]>
}

// ---- funding lab (GET /lab/funding) — services.funding_lab -----------------

export interface FundingCell {
  venue: string
  market: string
  n: number
  mean_hourly: number
  annualized: number
  interval_s: number
  settlements_per_year: number
}

export interface Dislocation {
  ts: number
  venue: string
  dislocation_hourly: number
}

export interface FundingLab {
  markets: string[]
  venues: string[]
  rate_type: string | null
  requested_range: Range
  effective_range: Range
  cells: Record<string, FundingCell | null> // key `${venue}/${market}`
  dislocation: Record<string, Dislocation | null> // key market
  fidelity: string[]
}

// ---- run library (GET /runs, GET /runs/compare) — services.runs ------------

export interface RunRow {
  run_id: string
  strategy: string
  kind: string
  created_ts: number
  effective_start_ts: number | null
  effective_end_ts: number | null
  metrics: Record<string, number | null>
  seed: number | null
  engine_version: string
  provenance?: string
  note?: string
}

export interface RunComparison {
  run_ids: string[]
  warnings: string[]
  metrics: Record<string, (number | null)[]>
}

// ---- paper monitor (WS /paper/{id}/stream) — services.paper_snapshot -------

export interface PaperPosition {
  market?: string
  venue?: string
  size?: number
  entry_price?: number
  mark_price?: number
  unrealized_pnl?: number
  [k: string]: unknown
}

export interface PaperSnapshot {
  run_id: string
  kind: string
  status: string
  positions: PaperPosition[]
  funding_accrued?: number | null
  liq_distances_pct: Record<string, number>
  drift: Record<string, unknown>
  alerts: unknown[]
  final_equity?: number | null
}

// ---- template registry (GET /templates) — app.templates (§8.4) --------------

export interface Template {
  name: string
  category: string // "funding" | "basis" | "flow" | "technical" | "ml"
  summary: string
  is_ml: boolean
  params: Record<string, unknown> // declared knobs (name → default)
}

export interface TemplatesResponse {
  templates: Template[]
  executable_venues: string[]
}

// ---- system health (GET /system/health) — footer version + connection banner --

export interface SystemHealth {
  ok: boolean
  version: string
}

// ---- backtest submit + status (POST /backtests[/source], GET .../status) ------

export interface SubmitResponse {
  run_id: string
}

export interface BacktestStatus {
  run_id: string
  state: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  progress_pct: number
  queue_position: number
  error?: { code?: string; message?: string; detail?: string; hint?: string }
}
