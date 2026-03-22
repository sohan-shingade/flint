import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import AsciiFire from '../components/AsciiFire'

interface MarketData {
  market: string
  resolution_s: number
  candle_count: number
  first_ts: number
  last_ts: number
}

export default function Dashboard() {
  const [markets, setMarkets] = useState<MarketData[]>([])
  const [health, setHealth] = useState<string>('...')
  const navigate = useNavigate()

  useEffect(() => {
    fetch('/api/v1/health')
      .then((r) => r.json())
      .then(() => setHealth('ONLINE'))
      .catch(() => setHealth('OFFLINE'))

    fetch('/api/v1/data/markets')
      .then((r) => r.json())
      .then((d) => setMarkets(d.markets || []))
      .catch(() => {})
  }, [])

  // Keyboard nav — numbers actually work now
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === '2') navigate('/backtest')
      if (e.key === '3') navigate('/data')
      if (e.key === '4') navigate('/docs')
      if (e.key === '5') navigate('/mev')
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [navigate])

  const fmtDate = (ts: number) =>
    new Date(ts * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })

  const totalCandles = markets.reduce((s, m) => s + m.candle_count, 0)
  const perpMarkets = markets.filter(m => m.market.includes('-PERP'))
  const spotMarkets = markets.filter(m => !m.market.includes('-PERP'))

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
