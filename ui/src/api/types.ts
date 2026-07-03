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

// The structured funding-gap (or other scarcity) rejection — data, not an error.
export interface RejectedPayload {
  code: string
  message: string
  missing: string[]
  available: Record<string, Range | null>
  hint: string
}

export interface BacktestResult {
  verdict: 'ok' | 'rejected'
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
  equity_curve?: number[]
  rejections?: InRunRejection[]
  fidelity_lines?: string[]
  notes?: string[]
  timing?: Record<string, number>
  // present only when verdict === "rejected"
  rejected?: RejectedPayload
}

// ---- data coverage (GET /data/coverage) — services.data_coverage ------------

export interface Coverage {
  market: string
  venue: string
  coverage: {
    candles?: Range | null
    funding?: Range | null
    oi?: Range | null
  }
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
