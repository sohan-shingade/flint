import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import AsciiFire from '../components/AsciiFire'

interface MarketData {
  market: string
  resolution_s: number
  candle_count: number
  first_ts: number
  last_ts: number
}

interface JournalRun {
  run_id: string
  strategy_name: string
  market: string
  total_pnl: number
  total_return_pct: number
  win_rate: number
  sharpe_ratio: number
  max_drawdown: number
  total_trades: number
  total_fees: number
  created_at: string
}

interface StrategyRow {
  strategy_name: string
  avg_sharpe: number
  avg_pnl: number
  total_runs: number
  avg_win_rate: number
  best_sharpe: number
  avg_drawdown: number
}

type SortField = 'avg_sharpe' | 'avg_pnl' | 'best_sharpe' | 'avg_win_rate' | 'avg_drawdown' | 'total_runs'

export default function Dashboard() {
  const [markets, setMarkets] = useState<MarketData[]>([])
  const [health, setHealth] = useState<string>('...')
  const [journalRuns, setJournalRuns] = useState<JournalRun[]>([])
  const [sortField, setSortField] = useState<SortField>('avg_sharpe')
  useEffect(() => {
    fetch('/api/v1/health')
      .then((r) => r.json())
      .then(() => setHealth('ONLINE'))
      .catch(() => setHealth('OFFLINE'))

    fetch('/api/v1/data/markets')
      .then((r) => r.json())
      .then((d) => setMarkets(d.markets || []))
      .catch((e) => { console.warn("[pages/Dashboard.tsx] fetch failed:", e) })

    fetch('/api/v1/journal/runs?limit=200')
      .then((r) => r.json())
      .then((d) => setJournalRuns(d.runs || d || []))
      .catch((e) => { console.warn("[pages/Dashboard.tsx] fetch failed:", e) })
  }, [])

  // Keyboard nav handled globally by App.tsx

  const fmtDate = (ts: number) =>
    new Date(ts * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })

  const totalCandles = markets.reduce((s, m) => s + m.candle_count, 0)
  const perpMarkets = markets.filter(m => m.market.includes('-PERP'))
  const spotMarkets = markets.filter(m => !m.market.includes('-PERP'))

  const leaderboard = useMemo<StrategyRow[]>(() => {
    if (journalRuns.length === 0) return []
    const grouped = new Map<string, JournalRun[]>()
    for (const run of journalRuns) {
      const name = run.strategy_name || 'unnamed'
      if (!grouped.has(name)) grouped.set(name, [])
      grouped.get(name)!.push(run)
    }
    const rows: StrategyRow[] = []
    for (const [name, runs] of grouped) {
      const avg = (arr: number[]) => arr.reduce((a, b) => a + b, 0) / arr.length
      rows.push({
        strategy_name: name,
        avg_sharpe: avg(runs.map(r => r.sharpe_ratio ?? 0)),
        avg_pnl: avg(runs.map(r => r.total_pnl ?? 0)),
        total_runs: runs.length,
        avg_win_rate: avg(runs.map(r => r.win_rate ?? 0)),
        best_sharpe: Math.max(...runs.map(r => r.sharpe_ratio ?? 0)),
        avg_drawdown: avg(runs.map(r => r.max_drawdown ?? 0)),
      })
    }
    const desc: SortField[] = ['avg_sharpe', 'avg_pnl', 'best_sharpe', 'avg_win_rate', 'total_runs']
    const sign = desc.includes(sortField) ? -1 : 1
    rows.sort((a, b) => sign * (a[sortField] - b[sortField]))
    return rows
  }, [journalRuns, sortField])

  return (
    <div>
      {/* ── compact hero: fire + title + stats in one band ── */}
      <div className="-mx-6 -mt-8 px-6 border-b border-border relative overflow-hidden">
        <div className="flex items-center gap-8 py-6 max-w-[1400px] mx-auto">
          {/* fire — compact, left-aligned */}
          <div className="shrink-0 hidden md:block" style={{ marginBottom: -8 }}>
            <AsciiFire cols={48} rows={18} />
          </div>

          {/* title + quick stats */}
          <div className="flex-1 min-w-0">
            <div className="flex items-baseline gap-4 mb-1">
              <h1 className="font-[var(--font-display)] text-4xl text-white/90 italic leading-none">Flint</h1>
              <span className="text-[10px] text-ghost tracking-[0.3em]">SOLANA TRADING ENGINE</span>
            </div>
            <p className="font-[var(--font-display)] italic text-sm text-amber/50 mb-4">
              Strike alpha on Solana
            </p>

            {/* inline stats row */}
            <div className="flex items-center gap-6 text-[11px]">
              <div className="flex items-center gap-2">
                <span className={`w-1.5 h-1.5 rounded-full ${health === 'ONLINE' ? 'bg-phosphor' : 'bg-loss'}`} />
                <span className={health === 'ONLINE' ? 'text-phosphor' : 'text-loss'}>{health}</span>
              </div>
              <div>
                <span className="text-ghost">MARKETS </span>
                <span className="text-amber tabular-nums">{markets.length}</span>
              </div>
              <div>
                <span className="text-ghost">CANDLES </span>
                <span className="text-white/80 tabular-nums">{totalCandles.toLocaleString()}</span>
              </div>
              <div>
                <span className="text-ghost">PROTOCOL </span>
                <span className="text-amber">DRIFT</span>
              </div>
            </div>
          </div>

          {/* quick actions — right side */}
          <div className="shrink-0 hidden lg:flex flex-col gap-2">
            <Link
              to="/backtest"
              className="px-4 py-2 bg-amber text-void text-[11px] font-semibold tracking-[0.15em] hover:bg-amber-dim transition-colors text-center"
            >
              OPEN LAB
            </Link>
            <Link
              to="/data"
              className="px-4 py-2 border border-border text-[11px] text-ghost tracking-[0.15em] hover:text-terminal hover:border-border-bright transition-colors text-center"
            >
              DATA EXPLORER
            </Link>
          </div>
        </div>
      </div>

      {/* ── project intro ── */}
      <div className="mt-8 border border-border bg-surface/80 p-6">
        <h2 className="font-[var(--font-display)] italic text-xl text-white/90 mb-3">
          What is Flint?
        </h2>
        <p className="text-[13px] text-terminal leading-relaxed mb-3">
          Flint is a <span className="text-white/90">local-first algorithmic trading platform</span> built
          for Solana. Write strategies in Python, backtest against real market data, optimize with Optuna,
          and paper trade on Drift — all from your machine with zero cloud dependencies.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 mt-4">
          <div className="border border-border/60 bg-panel/50 p-3">
            <div className="text-amber text-[11px] tracking-wider font-medium mb-1">BACKTEST</div>
            <p className="text-[11px] text-terminal/80 leading-relaxed">
              Realistic fills, slippage, and fee models. Stop-loss, take-profit, limit orders.
            </p>
          </div>
          <div className="border border-border/60 bg-panel/50 p-3">
            <div className="text-amber text-[11px] tracking-wider font-medium mb-1">OPTIMIZE</div>
            <p className="text-[11px] text-terminal/80 leading-relaxed">
              Bayesian hyperparameter search via Optuna. Walk-forward analysis and Monte Carlo CI.
            </p>
          </div>
          <div className="border border-border/60 bg-panel/50 p-3">
            <div className="text-amber text-[11px] tracking-wider font-medium mb-1">PAPER TRADE</div>
            <p className="text-[11px] text-terminal/80 leading-relaxed">
              Same strategy code runs live against real Drift prices. No changes needed.
            </p>
          </div>
          <div className="border border-border/60 bg-panel/50 p-3">
            <div className="text-amber text-[11px] tracking-wider font-medium mb-1">FREE DATA</div>
            <p className="text-[11px] text-terminal/80 leading-relaxed">
              OHLCV for 48 markets auto-downloaded from Drift. Stored locally in DuckDB.
            </p>
          </div>
        </div>
      </div>

      {/* ── main content: 2-column layout ── */}
      <div className="mt-6 grid grid-cols-1 lg:grid-cols-3 gap-4">

        {/* LEFT column (2/3) — data + quick start */}
        <div className="lg:col-span-2 space-y-4">

          {/* quick start — updated CLI commands */}
          <div className="border border-border bg-surface/80">
            <div className="px-4 py-2 border-b border-border flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-loss/60" />
              <span className="w-2 h-2 rounded-full bg-amber/60" />
              <span className="w-2 h-2 rounded-full bg-gain/60" />
              <span className="text-[10px] text-ghost/40 tracking-wider ml-2">quickstart</span>
            </div>
            <div className="p-4 text-[11px] leading-relaxed font-mono">
              <div className="text-ghost mb-2"># Get started in 3 commands</div>
              <div className="mb-1">
                <span className="text-amber/60">$</span>{' '}
                <span className="text-terminal">pip install -e .</span>
              </div>
              <div className="mb-1">
                <span className="text-amber/60">$</span>{' '}
                <span className="text-terminal">flint init</span>
                <span className="text-ghost/60 ml-4"># backfills data + runs sample backtest</span>
              </div>
              <div className="mb-1">
                <span className="text-amber/60">$</span>{' '}
                <span className="text-terminal">flint serve</span>
                <span className="text-ghost/60 ml-4"># starts this UI</span>
              </div>
              <div className="mt-3 pt-2 border-t border-border/30 text-ghost/60 text-[10px]">
                flint backtest strategy.py &middot; flint optimize &middot; flint data download &middot; flint live --paper
              </div>
            </div>
          </div>

          {/* data coverage table */}
          {markets.length > 0 && (
            <div className="border border-border bg-surface/60">
              <div className="px-4 py-2.5 border-b border-border flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="w-1.5 h-1.5 bg-amber/50" />
                  <span className="text-[10px] text-ghost tracking-[0.2em]">DATA.COVERAGE</span>
                </div>
                <div className="flex items-center gap-4 text-[10px] text-ghost/40">
                  <span>{perpMarkets.length} perp</span>
                  {spotMarkets.length > 0 && <span>{spotMarkets.length} spot</span>}
                </div>
              </div>
              <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-surface">
                    <tr className="text-left text-ghost border-b border-border text-[9px] tracking-[0.15em]">
                      <th className="py-2 px-4">MARKET</th>
                      <th className="py-2 px-4">RES</th>
                      <th className="py-2 px-4 text-right">RECORDS</th>
                      <th className="py-2 px-4">RANGE</th>
                    </tr>
                  </thead>
                  <tbody>
                    {markets.map((m, i) => (
                      <tr key={i} className="border-b border-border/20 hover:bg-amber-glow/50 transition-colors">
                        <td className="py-1.5 px-4 text-amber/80 font-medium text-[11px]">{m.market}</td>
                        <td className="py-1.5 px-4 text-ghost text-[10px]">
                          {m.resolution_s >= 3600 ? `${m.resolution_s / 3600}h` : `${m.resolution_s / 60}m`}
                        </td>
                        <td className="py-1.5 px-4 text-right text-white/80 tabular-nums text-[11px]">{m.candle_count.toLocaleString()}</td>
                        <td className="py-1.5 px-4 text-ghost text-[10px]">
                          {fmtDate(m.first_ts)} — {fmtDate(m.last_ts)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* strategy leaderboard */}
          {leaderboard.length > 0 && (
            <div className="border border-border bg-surface/60">
              <div className="px-4 py-2.5 border-b border-border flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="w-1.5 h-1.5 bg-amber/50" />
                  <span className="text-[10px] text-ghost/50 tracking-[0.2em]">STRATEGY LEADERBOARD</span>
                </div>
                <div className="flex items-center gap-1">
                  {([
                    ['avg_sharpe', 'SHARPE'],
                    ['avg_pnl', 'PNL'],
                    ['best_sharpe', 'BEST'],
                    ['avg_win_rate', 'WIN%'],
                    ['avg_drawdown', 'DD'],
                    ['total_runs', 'RUNS'],
                  ] as [SortField, string][]).map(([field, label]) => (
                    <button
                      key={field}
                      onClick={() => setSortField(field)}
                      className={`text-[9px] px-2 py-1 border transition-colors ${
                        sortField === field
                          ? 'border-amber/40 text-amber'
                          : 'border-border text-ghost/40 hover:text-amber'
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-[11px] font-mono">
                  <thead>
                    <tr className="text-left text-[9px] text-ghost/50 tracking-wider border-b border-border">
                      <th className="py-2 px-4">#</th>
                      <th className="py-2 px-4">STRATEGY</th>
                      <th className="py-2 px-4 text-right">AVG SHARPE</th>
                      <th className="py-2 px-4 text-right">AVG PNL</th>
                      <th className="py-2 px-4 text-right">BEST SHARPE</th>
                      <th className="py-2 px-4 text-right">WIN RATE</th>
                      <th className="py-2 px-4 text-right">AVG MAX DD</th>
                      <th className="py-2 px-4 text-right">RUNS</th>
                    </tr>
                  </thead>
                  <tbody>
                    {leaderboard.map((row, i) => (
                      <tr key={row.strategy_name} className="border-b border-border/20 hover:bg-amber-glow/50 transition-colors">
                        <td className="py-1.5 px-4 text-ghost/40">{i + 1}</td>
                        <td className="py-1.5 px-4 text-amber/80 font-medium">{row.strategy_name}</td>
                        <td className={`py-1.5 px-4 text-right tabular-nums ${row.avg_sharpe > 1 ? 'text-gain' : 'text-white/80'}`}>
                          {row.avg_sharpe.toFixed(2)}
                        </td>
                        <td className={`py-1.5 px-4 text-right tabular-nums ${row.avg_pnl >= 0 ? 'text-gain' : 'text-loss'}`}>
                          {row.avg_pnl >= 0 ? '+' : ''}{row.avg_pnl.toFixed(2)}
                        </td>
                        <td className={`py-1.5 px-4 text-right tabular-nums ${row.best_sharpe > 1 ? 'text-gain' : 'text-white/80'}`}>
                          {row.best_sharpe.toFixed(2)}
                        </td>
                        <td className="py-1.5 px-4 text-right tabular-nums text-white/80">
                          {(row.avg_win_rate * 100).toFixed(1)}%
                        </td>
                        <td className="py-1.5 px-4 text-right tabular-nums text-loss/70">
                          {(row.avg_drawdown * 100).toFixed(1)}%
                        </td>
                        <td className="py-1.5 px-4 text-right tabular-nums text-ghost">
                          {row.total_runs}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        {/* RIGHT column (1/3) — navigation cards */}
        <div className="space-y-3">
          <Link to="/backtest" className="block group">
            <div className="border border-border bg-surface/60 p-4 hover:bg-panel hover:border-amber/20 transition-all">
              <div className="flex items-center justify-between mb-1.5">
                <h3 className="text-[12px] text-white/80 font-medium group-hover:text-amber transition-colors">Strategy Lab</h3>
                <span className="text-[9px] text-ghost/30 tracking-wider">[2]</span>
              </div>
              <p className="text-[10px] text-ghost leading-relaxed">
                Write, backtest, and optimize strategies. Monaco editor, 8 templates, Monte Carlo CI.
              </p>
            </div>
          </Link>

          <Link to="/data" className="block group">
            <div className="border border-border bg-surface/60 p-4 hover:bg-panel hover:border-amber/20 transition-all">
              <div className="flex items-center justify-between mb-1.5">
                <h3 className="text-[12px] text-white/80 font-medium group-hover:text-amber transition-colors">Data Explorer</h3>
                <span className="text-[9px] text-ghost/30 tracking-wider">[3]</span>
              </div>
              <p className="text-[10px] text-ghost leading-relaxed">
                Browse OHLCV candles. Free data from Drift S3. Auto-backfill on demand.
              </p>
            </div>
          </Link>

          <Link to="/docs" className="block group">
            <div className="border border-border bg-surface/60 p-4 hover:bg-panel hover:border-amber/20 transition-all">
              <div className="flex items-center justify-between mb-1.5">
                <h3 className="text-[12px] text-white/80 font-medium group-hover:text-amber transition-colors">Documentation</h3>
                <span className="text-[9px] text-ghost/30 tracking-wider">[4]</span>
              </div>
              <p className="text-[10px] text-ghost leading-relaxed">
                Strategy API, CLI commands, providers, architecture guide.
              </p>
            </div>
          </Link>

          <Link to="/mev" className="block group">
            <div className="border border-border bg-surface/60 p-4 hover:bg-panel hover:border-amber/20 transition-all">
              <div className="flex items-center justify-between mb-1.5">
                <h3 className="text-[12px] text-white/80 font-medium group-hover:text-amber transition-colors">MEV Scanner</h3>
                <span className="text-[9px] text-ghost/30 tracking-wider">[5]</span>
              </div>
              <p className="text-[10px] text-ghost leading-relaxed">
                Arb detection, liquidation scanning, JIT opportunities on Drift.
              </p>
            </div>
          </Link>
        </div>
      </div>

      <div className="h-8" />
    </div>
  )
}
