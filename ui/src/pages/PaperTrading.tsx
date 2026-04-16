import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import {
  usePaperPortfolio,
  useSessionStatus,
  useSessionTrades,
  stopSession,
  killSession,
} from '../hooks/usePaperTrading'
import EquityCurve from '../components/EquityCurve'

/* ── helpers ─────────────────────────────────────────────── */

const fmt = (v: number | undefined | null, dec = 2) =>
  v != null ? v.toFixed(dec) : '-'

const fmtUsd = (v: number | undefined | null) =>
  v != null ? `$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '-'

function StatusDot({ status }: { status: string }) {
  const color =
    status === 'live'
      ? 'bg-phosphor'
      : status === 'replaying'
        ? 'bg-amber'
        : 'bg-loss'
  return <span className={`w-1.5 h-1.5 rounded-full inline-block shrink-0 ${color}`} />
}

function statusLabel(status: string) {
  const map: Record<string, string> = {
    live: 'LIVE',
    replaying: 'REPLAY',
    stopped: 'STOPPED',
    risk_stopped: 'RISK_STOP',
  }
  return map[status] ?? status.toUpperCase()
}

function statusTextColor(status: string) {
  if (status === 'live') return 'text-phosphor'
  if (status === 'replaying') return 'text-amber'
  return 'text-loss'
}

/* ── sidebar strategy card ───────────────────────────────── */

interface SidebarCardProps {
  session: {
    session_id: string
    strategy_name: string
    market: string
    venue?: string
    equity: number
    pnl: number
    status: string
    initial_capital?: number
  }
  selected: boolean
  onClick: () => void
}

function SidebarCard({ session, selected, onClick }: SidebarCardProps) {
  const pnlPositive = session.pnl >= 0
  const capital = session.initial_capital || 10000
  const returnPct = capital > 0 ? ((session.equity - capital) / capital * 100) : 0
  const returnPositive = returnPct >= 0

  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-3 border-b border-border transition-colors ${
        selected
          ? 'bg-amber-glow border-l-2 border-l-amber'
          : 'hover:bg-surface/60 border-l-2 border-l-transparent'
      }`}
    >
      <div className="flex items-center gap-2 mb-1">
        <StatusDot status={session.status} />
        <span className="text-xs font-semibold text-white/90 truncate flex-1">
          {session.strategy_name}
        </span>
      </div>
      <div className="flex items-center justify-between pl-3.5">
        <span className="text-[10px] text-ghost tracking-wider">
          {session.market}{session.venue && session.venue !== 'drift' ? ` @ ${session.venue}` : ''}
        </span>
        <span className={`text-[11px] font-medium tabular-nums ${pnlPositive ? 'text-phosphor' : 'text-loss'}`}>
          {pnlPositive ? '+' : ''}{fmtUsd(session.pnl)}
        </span>
      </div>
      <div className="flex items-center justify-between pl-3.5 mt-0.5">
        <span className={`text-[9px] tracking-[0.15em] ${statusTextColor(session.status)}`}>
          {statusLabel(session.status)}
        </span>
        <span className={`text-[9px] tabular-nums ${returnPositive ? 'text-phosphor/70' : 'text-loss/70'}`}>
          {returnPositive ? '+' : ''}{fmt(returnPct)}%
        </span>
      </div>
    </button>
  )
}

/* ── session detail panel ────────────────────────────────── */

function SessionDetail({ sessionId, onStop, onKill }: {
  sessionId: string
  onStop: () => void
  onKill: () => void
}) {
  const status = useSessionStatus(sessionId)
  const trades = useSessionTrades(sessionId)
  const [confirming, setConfirming] = useState<'stop' | 'kill' | null>(null)
  const [parityResult, setParityResult] = useState<any>(null)
  const [parityLoading, setParityLoading] = useState(false)

  async function handleParityTest() {
    if (!status?.market || !status?.strategy) return
    setParityLoading(true)
    setParityResult(null)
    try {
      const r = await fetch('/api/v1/backtest/parity', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          market: status.market,
          strategy: status.strategy,
          start_ts: eqHistory[0]?.ts || 0,
          end_ts: eqHistory[eqHistory.length - 1]?.ts || Math.floor(Date.now() / 1000),
          capital: eqHistory[0]?.equity || 10000,
          fee_rate: 0.001,
        }),
      })
      const d = await r.json()
      setParityResult(d)
    } catch {
      setParityResult({ error: 'Parity test failed' })
    } finally {
      setParityLoading(false)
    }
  }

  // Fetch equity history from DB (has replay + live data)
  const [eqHistory, setEqHistory] = useState<any[]>([])
  useEffect(() => {
    if (!sessionId) return
    fetch(`/api/v1/paper/${sessionId}/equity-history`)
      .then(r => r.json())
      .then(d => setEqHistory(d.equity_curve || []))
      .catch(() => {})
  }, [sessionId])

  // Fetch candle data for buy-and-hold baseline
  const [buyHoldData, setBuyHoldData] = useState<[number, number][]>([])
  useEffect(() => {
    if (!status?.market || eqHistory.length === 0) return
    const startTs = eqHistory[0]?.ts || 0
    const endTs = eqHistory[eqHistory.length - 1]?.ts || 0
    if (!startTs) return
    fetch(`/api/v1/data/ohlcv?market=${status.market}&resolution_s=3600&start_ts=${startTs}&end_ts=${endTs}`)
      .then(r => r.json())
      .then(d => {
        const candles = d.candles || []
        if (candles.length === 0) return
        // Compute buy-and-hold: invest initial_capital at first close, track value
        const initialPrice = candles[0].close
        const initialCapital = eqHistory[0]?.equity || 10000
        const bh: [number, number][] = candles.map((c: any) => [
          c.ts,
          initialCapital * (c.close / initialPrice),
        ])
        setBuyHoldData(bh)
      })
      .catch(() => {})
  }, [status?.market, eqHistory])

  if (!status) {
    return (
      <div className="flex items-center justify-center h-40">
        <span className="text-ghost/40 text-xs tracking-widest animate-pulse">LOADING SESSION...</span>
      </div>
    )
  }

  const realizedPnl = status.realized_pnl ?? 0
  const unrealizedPnl = status.unrealized_pnl ?? 0
  const rpnlPositive = realizedPnl >= 0
  const upnlPositive = unrealizedPnl >= 0

  // Use equity history from dedicated endpoint if available, fall back to status.equity_curve
  const equityCurve = eqHistory.length > 0 ? eqHistory : (status.equity_curve || [])

  const metrics: { label: string; value: string; accent: boolean | undefined }[] = [
    { label: 'EQUITY', value: fmtUsd(status.equity), accent: undefined },
    { label: 'REALIZED.PNL', value: (rpnlPositive ? '+' : '') + fmtUsd(realizedPnl), accent: rpnlPositive },
    { label: 'UNREALIZED.PNL', value: (upnlPositive ? '+' : '') + fmtUsd(unrealizedPnl), accent: upnlPositive },
    { label: 'TOTAL.TRADES', value: String(status.total_trades ?? 0), accent: undefined },
    { label: 'TOTAL.FEES', value: fmtUsd(status.total_fees), accent: undefined },
    { label: 'STATUS', value: statusLabel(status.status ?? status.phase), accent: undefined },
  ]

  // Append margin metrics when available
  if ((status as any).leverage != null) {
    metrics.push({ label: 'LEVERAGE', value: `${fmt((status as any).leverage, 1)}x`, accent: undefined })
  }
  if ((status as any).free_margin != null) {
    metrics.push({ label: 'FREE.MARGIN', value: fmtUsd((status as any).free_margin), accent: undefined })
  }
  if ((status as any).margin_used != null) {
    metrics.push({ label: 'MARGIN.USED', value: fmtUsd((status as any).margin_used), accent: undefined })
  }

  async function handleStop() {
    if (confirming !== 'stop') { setConfirming('stop'); return }
    await stopSession(sessionId)
    setConfirming(null)
    onStop()
  }

  async function handleKill() {
    if (confirming !== 'kill') { setConfirming('kill'); return }
    await killSession(sessionId)
    setConfirming(null)
    onKill()
  }

  return (
    <div className="space-y-6" style={{ animation: 'fadeUp 0.25s ease' }}>
      {/* header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-baseline gap-3">
            <h2 className="font-[var(--font-display)] text-xl text-white/90 italic">
              {status.strategy}
            </h2>
            <span className="text-[10px] text-ghost tracking-[0.2em]">// {status.market}</span>
            <span className={`text-[10px] tracking-[0.15em] ${statusTextColor(status.status ?? status.phase)}`}>
              {statusLabel(status.status ?? status.phase)}
            </span>
          </div>
          {/* deployment info */}
          <div className="text-[10px] text-ghost/50 tracking-wide mt-1">
            Deployed {new Date(((status as any).started_at ?? 0) * 1000).toLocaleDateString('en-US', {
              month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit',
              timeZone: 'UTC',
            })} UTC
            {(() => {
              const liveStart = eqHistory.find(e => !e.is_replay)
              if (liveStart) {
                return ` \u00b7 Live since ${new Date(liveStart.ts * 1000).toLocaleDateString('en-US', {
                  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC',
                })} UTC`
              }
              return ''
            })()}
          </div>
        </div>
        {/* action buttons */}
        <div className="flex items-center gap-2">
          <button
            onClick={handleStop}
            className={`px-4 py-1.5 text-[10px] tracking-[0.15em] border transition-colors ${
              confirming === 'stop'
                ? 'bg-amber text-void border-amber'
                : 'border-border text-ghost hover:border-amber hover:text-amber'
            }`}
          >
            {confirming === 'stop' ? 'CONFIRM STOP?' : 'STOP'}
          </button>
          <button
            onClick={handleKill}
            className={`px-4 py-1.5 text-[10px] tracking-[0.15em] border transition-colors ${
              confirming === 'kill'
                ? 'bg-loss text-void border-loss'
                : 'border-border text-ghost hover:border-loss hover:text-loss'
            }`}
          >
            {confirming === 'kill' ? 'CONFIRM KILL?' : 'KILL'}
          </button>
          {confirming && (
            <button
              onClick={() => setConfirming(null)}
              className="px-3 py-1.5 text-[10px] text-ghost/50 hover:text-ghost tracking-[0.1em]"
            >
              cancel
            </button>
          )}
          <button
            onClick={handleParityTest}
            disabled={parityLoading || eqHistory.length === 0}
            className="px-4 py-1.5 text-[10px] tracking-[0.15em] border border-terminal/40 text-terminal hover:bg-terminal/10 disabled:border-border disabled:text-ghost/30 transition-colors"
          >
            {parityLoading ? 'TESTING...' : 'PARITY TEST'}
          </button>
        </div>
      </div>

      {/* parity test results */}
      {parityResult && !parityResult.error && (
        <div className="border border-terminal/30 bg-surface/60 p-4" style={{ animation: 'fadeUp 0.3s ease' }}>
          <div className="flex items-baseline gap-3 mb-3">
            <span className="font-[var(--font-display)] text-base text-white/90 italic">Parity Report</span>
            <span className={`text-[10px] tracking-[0.15em] ${parityResult.passed ? 'text-gain' : 'text-loss'}`}>
              {parityResult.passed ? 'PASS' : 'DIVERGED'}
            </span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <div className="bg-void/50 px-2 py-1.5 border border-border/50">
              <div className="text-[8px] text-ghost/50 tracking-wider">PNL DIVERGENCE</div>
              <div className={`text-[12px] font-mono ${Math.abs(parityResult.pnl_divergence_pct || 0) < 2 ? 'text-gain' : 'text-loss'}`}>
                {fmt(parityResult.pnl_divergence_pct)}%
              </div>
            </div>
            <div className="bg-void/50 px-2 py-1.5 border border-border/50">
              <div className="text-[8px] text-ghost/50 tracking-wider">FILL PRICE MAE</div>
              <div className="text-[12px] font-mono text-terminal">${fmt(parityResult.fill_price_mae, 4)}</div>
            </div>
            <div className="bg-void/50 px-2 py-1.5 border border-border/50">
              <div className="text-[8px] text-ghost/50 tracking-wider">EQUITY CORRELATION</div>
              <div className={`text-[12px] font-mono ${(parityResult.equity_correlation || 0) > 0.95 ? 'text-gain' : 'text-amber'}`}>
                {fmt(parityResult.equity_correlation, 4)}
              </div>
            </div>
            <div className="bg-void/50 px-2 py-1.5 border border-border/50">
              <div className="text-[8px] text-ghost/50 tracking-wider">SIGNAL TIMING</div>
              <div className="text-[12px] font-mono text-terminal">{fmt(parityResult.signal_timing_match_pct)}%</div>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2 mt-2">
            <div className="bg-void/50 px-2 py-1.5 border border-border/50">
              <div className="text-[8px] text-ghost/50 tracking-wider">BACKTEST PNL</div>
              <div className={`text-[12px] font-mono ${(parityResult.backtest_pnl || 0) >= 0 ? 'text-gain' : 'text-loss'}`}>
                ${fmt(parityResult.backtest_pnl)}
              </div>
            </div>
            <div className="bg-void/50 px-2 py-1.5 border border-border/50">
              <div className="text-[8px] text-ghost/50 tracking-wider">PAPER PNL</div>
              <div className={`text-[12px] font-mono ${(parityResult.paper_pnl || 0) >= 0 ? 'text-gain' : 'text-loss'}`}>
                ${fmt(parityResult.paper_pnl)}
              </div>
            </div>
          </div>
        </div>
      )}
      {parityResult?.error && (
        <div className="border border-loss/30 bg-loss/5 px-3 py-2 text-loss text-[10px]">
          <span className="text-loss/60 mr-1">[PARITY]</span>{parityResult.error}
        </div>
      )}

      {/* metrics grid */}
      <div className="grid grid-cols-3 md:grid-cols-6 gap-px bg-border">
        {metrics.map((m, i) => (
          <div
            key={m.label}
            className="bg-surface p-3 hover:bg-panel transition-colors"
            style={{ animation: `fadeUp 0.3s ease ${i * 0.04}s both` }}
          >
            <div className="text-[9px] text-ghost/60 tracking-[0.2em] mb-1">{m.label}</div>
            <div
              className={`text-sm font-medium tabular-nums ${
                m.accent === undefined
                  ? 'text-white/80'
                  : m.accent
                    ? 'text-phosphor'
                    : 'text-loss'
              }`}
            >
              {m.value}
            </div>
          </div>
        ))}
      </div>

      {/* equity curve with buy-and-hold baseline */}
      {equityCurve.length > 0 && (
        <div className="border border-border bg-surface/60 p-4">
          <div className="flex items-center gap-2 mb-3">
            <span className="w-2 h-2 bg-amber/60" />
            <span className="text-[10px] text-ghost tracking-[0.2em]">EQUITY.CURVE</span>
          </div>
          <EquityCurve
            equity={equityCurve.map((e: any) => [e.ts, e.equity] as [number, number])}
            buyHold={buyHoldData.length > 0 ? buyHoldData : undefined}
            trades={trades.flatMap((t: any) => {
              const side: 'long' | 'short' = t.side === 'long' ? 'long' : 'short'
              const markers: { ts: number; type: 'entry' | 'exit'; side: 'long' | 'short' }[] = [
                { ts: t.entry_ts, type: 'entry', side },
              ]
              if (t.exit_ts) markers.push({ ts: t.exit_ts, type: 'exit', side })
              return markers
            })}
            height={280}
          />
          <div className="flex items-center gap-4 mt-2 text-[9px] text-ghost/50 flex-wrap">
            <span className="flex items-center gap-1">
              <span className="w-3 h-px bg-amber inline-block"></span> Strategy
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-px bg-ghost/30 inline-block" style={{borderTop: '1px dashed'}}></span> Buy &amp; Hold ({status.market})
            </span>
            {trades.length > 0 && (
              <>
                <span className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-phosphor inline-block"></span> Long entry
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-loss inline-block"></span> Short entry
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-purple-400 inline-block"></span> Exit
                </span>
              </>
            )}
            {eqHistory.some(e => e.is_replay) && eqHistory.some(e => !e.is_replay) && (
              <span className="text-ghost/30">| dashed = replay · solid = live</span>
            )}
          </div>
        </div>
      )}

      {/* positions */}
      {status.positions && status.positions.length > 0 && (
        <div className="border border-border bg-surface/60">
          <div className="px-4 py-2.5 border-b border-border flex items-center gap-2">
            <span className="w-2 h-2 bg-amber/60" />
            <span className="text-[10px] text-ghost tracking-[0.2em]">OPEN.POSITIONS</span>
          </div>
          <div className="divide-y divide-border">
            {status.positions.map((pos: any, i: number) => (
              <div key={i} className="px-4 py-2.5 flex items-center gap-4 text-xs">
                <span className="text-amber w-28 truncate">{pos.market ?? pos.symbol ?? '-'}</span>
                <span className={`w-12 text-center text-[10px] tracking-wider ${pos.side === 'long' ? 'text-phosphor' : 'text-loss'}`}>
                  {pos.side?.toUpperCase()}
                </span>
                <span className="text-white/70 tabular-nums w-20">{fmtUsd(pos.entry_price)}</span>
                <span className="text-ghost/60 tabular-nums w-16">{fmt(pos.size, 4)}</span>
                <span className={`tabular-nums ml-auto font-medium ${(pos.unrealized_pnl ?? 0) >= 0 ? 'text-phosphor' : 'text-loss'}`}>
                  {(pos.unrealized_pnl ?? 0) >= 0 ? '+' : ''}{fmtUsd(pos.unrealized_pnl)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* trade history */}
      <div className="border border-border bg-surface/60">
        <div className="px-4 py-2.5 border-b border-border flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 bg-phosphor/60" />
            <span className="text-[10px] text-ghost tracking-[0.2em]">TRADE.HISTORY</span>
          </div>
          <span className="text-[10px] text-ghost/50">{trades.length} fills</span>
        </div>
        {trades.length === 0 ? (
          <div className="px-4 py-6 text-center text-[10px] text-ghost/40 tracking-wider">
            NO TRADES YET
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[11px] font-mono">
              <thead>
                <tr className="border-b border-border">
                  {['ENTRY', 'EXIT', 'SIDE', 'ENTRY $', 'EXIT $', 'SIZE', 'PNL'].map(h => (
                    <th key={h} className="px-3 py-2 text-left text-[9px] text-ghost/50 tracking-[0.2em] font-normal">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {trades.slice().reverse().map((t: any, i: number) => {
                  const fmtTime = (ts: number | undefined) =>
                    ts ? new Date(ts * 1000).toLocaleString('en-US', {
                      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
                      hour12: false, timeZone: 'UTC',
                    }) : '-'
                  return (
                    <tr key={i} className="hover:bg-amber-glow transition-colors">
                      <td className="px-3 py-2 text-ghost/60 tabular-nums whitespace-nowrap">
                        {fmtTime(t.entry_ts)}
                      </td>
                      <td className="px-3 py-2 text-ghost/60 tabular-nums whitespace-nowrap">
                        {fmtTime(t.exit_ts)}
                      </td>
                      <td className={`px-3 py-2 font-semibold ${t.side === 'buy' || t.side === 'long' ? 'text-phosphor' : 'text-loss'}`}>
                        {t.side?.toUpperCase()}
                      </td>
                      <td className="px-3 py-2 text-white/70 tabular-nums">{fmtUsd(t.entry_price)}</td>
                      <td className="px-3 py-2 text-white/70 tabular-nums">{fmtUsd(t.exit_price)}</td>
                      <td className="px-3 py-2 text-white/70 tabular-nums">{fmt(t.size ?? t.quantity, 4)}</td>
                      <td className={`px-3 py-2 tabular-nums font-medium ${(t.pnl ?? 0) >= 0 ? 'text-phosphor' : 'text-loss'}`}>
                        {t.pnl != null ? ((t.pnl >= 0 ? '+' : '') + fmtUsd(t.pnl)) : '-'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

/* ── empty state ─────────────────────────────────────────── */

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-24 gap-4">
      <div className="text-[10px] text-ghost/30 tracking-[0.4em] mb-2">NO.SESSIONS</div>
      <p className="text-sm text-ghost/50 text-center max-w-sm">
        No paper trading sessions running. Deploy a strategy from BacktestLab to get started.
      </p>
      <Link
        to="/backtest"
        className="mt-2 px-6 py-2 bg-amber text-void text-xs font-semibold tracking-[0.15em] hover:bg-amber-dim transition-colors"
      >
        {'>'} DEPLOY_FROM_LAB
      </Link>
    </div>
  )
}

/* ── deployment panel ────────────────────────────────────── */

interface StrategyMeta {
  name: string
  display_name: string
  description: string
  type?: string
  venues?: string[]
  needs_funding?: boolean
  params?: Record<string, any>
}

function DeployPanel() {
  const [isOpen, setIsOpen] = useState(false)
  const [strategies, setStrategies] = useState<StrategyMeta[]>([])
  const [markets, setMarkets] = useState<string[]>([])
  const [strategy, setStrategy] = useState('')
  const [market, setMarket] = useState('')
  const [venue, setVenue] = useState('paper')
  const [capital, setCapital] = useState('10000')
  const [deploying, setDeploying] = useState(false)
  const [deployMsg, setDeployMsg] = useState('')

  useEffect(() => {
    if (!isOpen) return
    fetch('/api/v1/strategies').then(r => r.json()).then(d => {
      const strats = d.strategies || []
      setStrategies(strats)
      if (strats.length > 0 && !strategy) setStrategy(strats[0].name)
    }).catch(() => {})
    fetch('/api/v1/data/markets').then(r => r.json()).then(d => {
      const raw = d.markets || []
      const mkts = raw.map((m: any) => typeof m === 'string' ? m : m.market)
      setMarkets(mkts)
      if (mkts.length > 0 && !market) setMarket(mkts[0])
    }).catch(() => {})
  }, [isOpen])

  const selectedStrat = strategies.find(s => s.name === strategy)
  const stratType = selectedStrat?.type || 'single_venue'
  const isMultiVenue = stratType === 'multi_venue'
  const isMonitor = stratType === 'monitor'

  async function handleDeploy() {
    setDeploying(true)
    setDeployMsg('')
    try {
      const res = await fetch('/api/v1/paper/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy, market, venue, initial_capital: parseFloat(capital) }),
      })
      const data = await res.json()
      if (res.ok) {
        setDeployMsg(`Deployed: ${data.session_id || 'ok'}`)
        setIsOpen(false)
      } else {
        setDeployMsg(`Error: ${data.detail || 'deploy failed'}`)
      }
    } catch (e) {
      setDeployMsg(`Error: ${String(e)}`)
    }
    setDeploying(false)
  }

  return (
    <div className="border border-border bg-surface/60 mb-6">
      <button
        onClick={() => setIsOpen(o => !o)}
        className="w-full px-4 py-2.5 flex items-center gap-2 hover:bg-amber-glow transition-colors"
      >
        <span className={`w-2 h-2 ${isOpen ? 'bg-amber' : 'bg-amber/40'} transition-colors`} />
        <span className="text-[10px] text-ghost tracking-[0.2em]">DEPLOY.STRATEGY</span>
        <span className="ml-auto text-[10px] text-ghost/40">{isOpen ? '▲ collapse' : '▼ expand'}</span>
      </button>
      {isOpen && (
        <div className="border-t border-border p-4 space-y-4" style={{ animation: 'fadeUp 0.2s ease' }}>
          <div className="flex flex-wrap gap-4">
            <div className="flex flex-col gap-1">
              <label className="text-[9px] text-ghost/50 tracking-[0.2em]">STRATEGY</label>
              <select
                value={strategy}
                onChange={e => setStrategy(e.target.value)}
                className="bg-panel border border-border text-terminal text-xs px-3 py-1.5 focus:outline-none focus:border-amber w-48"
              >
                {strategies.length === 0 && <option value="">Loading...</option>}
                {strategies.map(s => <option key={s.name} value={s.name}>{s.display_name || s.name}</option>)}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[9px] text-ghost/50 tracking-[0.2em]">MARKET</label>
              <select
                value={market}
                onChange={e => setMarket(e.target.value)}
                className="bg-panel border border-border text-terminal text-xs px-3 py-1.5 focus:outline-none focus:border-amber w-40"
              >
                {markets.length === 0 && <option value="">Loading...</option>}
                {markets.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
            {/* Venue selector — adaptive based on strategy type */}
            {!isMonitor && (
              <div className="flex flex-col gap-1">
                <label className="text-[9px] text-ghost/50 tracking-[0.2em]">VENUE</label>
                {isMultiVenue ? (
                  <div className="text-[10px] text-terminal px-3 py-1.5 border border-border bg-panel">
                    {(selectedStrat?.venues || ['drift', 'hyperliquid']).map(v => v.toUpperCase()).join(' + ')}
                    <div className="text-[8px] text-ghost/40 mt-0.5">Fixed by strategy</div>
                  </div>
                ) : (
                  <select value={venue} onChange={e => setVenue(e.target.value)}
                    className="bg-panel border border-border text-terminal text-xs px-3 py-1.5 focus:outline-none focus:border-amber w-36">
                    {['paper', 'drift', 'hyperliquid', 'binance', 'okx', 'bybit'].map(v =>
                      <option key={v} value={v}>{v}</option>
                    )}
                  </select>
                )}
              </div>
            )}
            <div className="flex flex-col gap-1">
              <label className="text-[9px] text-ghost/50 tracking-[0.2em]">CAPITAL ($)</label>
              <input
                type="number"
                value={capital}
                onChange={e => setCapital(e.target.value)}
                min="100"
                className="bg-panel border border-border text-terminal text-xs px-3 py-1.5 focus:outline-none focus:border-amber w-28 tabular-nums"
              />
            </div>
          </div>
          <div className="flex items-center gap-4">
            <button
              onClick={handleDeploy}
              disabled={deploying || !strategy || !market}
              className="px-6 py-2 bg-amber text-void text-xs font-semibold tracking-[0.15em] hover:bg-amber-dim disabled:bg-border disabled:text-ghost transition-all"
            >
              {deploying ? '> DEPLOYING...' : '> DEPLOY'}
            </button>
            {deployMsg && (
              <span className={`text-[10px] tracking-wide ${deployMsg.startsWith('Error') ? 'text-loss' : 'text-phosphor'}`}>
                {deployMsg}
              </span>
            )}
          </div>
          {/* Strategy info */}
          {selectedStrat && (
            <div className="text-[9px] text-ghost/60 space-y-1">
              <div>{selectedStrat.description}</div>
              {isMultiVenue && (
                <div className="text-amber/70">Multi-venue strategy — trades on {(selectedStrat.venues || []).join(' + ')}</div>
              )}
              {isMonitor && (
                <div className="text-amber/70">Monitor only — no orders placed</div>
              )}
              {selectedStrat.needs_funding && (
                <div className="text-amber/70">↳ Requires funding rate data — download in Data Explorer</div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ── main page ───────────────────────────────────────────── */

export default function PaperTrading() {
  const { portfolio, error } = usePaperPortfolio()
  const [selectedSession, setSelectedSession] = useState<string | null>(null)

  const sessions = portfolio?.per_strategy ?? []

  // Auto-select first live session when portfolio loads
  const liveStrategies = sessions.filter((s: any) => s.status === 'live')
  const activeSession = selectedSession || (liveStrategies.length > 0 ? liveStrategies[0].session_id : (sessions.length > 0 ? sessions[0].session_id : null))

  // Deselect if the selected session disappears
  const sessionExists = sessions.some((s: any) => s.session_id === activeSession)
  if (activeSession && !sessionExists && sessions.length > 0) {
    setSelectedSession(null)
  }

  return (
    <div className="flex gap-0 -mx-6 min-h-[calc(100vh-10rem)]">
      {/* ── left sidebar ── */}
      <aside className="w-64 shrink-0 border-r border-border bg-surface/30 backdrop-blur flex flex-col">
        {/* sidebar header */}
        <div className="px-4 py-3 border-b border-border flex items-center justify-between">
          <span className="text-[10px] text-ghost tracking-[0.25em]">STRATEGIES</span>
          <span className="text-[10px] text-amber tabular-nums">{sessions.length}</span>
        </div>

        {/* session list */}
        <div className="flex-1 overflow-y-auto">
          {sessions.length === 0 ? (
            <div className="px-4 py-6 text-center text-[10px] text-ghost/30 tracking-wider">
              NO SESSIONS
            </div>
          ) : (
            sessions.map((s: any) => (
              <SidebarCard
                key={s.session_id}
                session={s}
                selected={activeSession === s.session_id}
                onClick={() => setSelectedSession(s.session_id)}
              />
            ))
          )}
        </div>

        {/* sidebar footer */}
        <div className="px-4 py-3 border-t border-border">
          <Link
            to="/backtest"
            className="flex items-center gap-1 text-[10px] text-ghost/50 hover:text-amber tracking-[0.15em] transition-colors"
          >
            <span>Deploy from BacktestLab</span>
            <span className="text-ghost/30">→</span>
          </Link>
        </div>
      </aside>

      {/* ── main panel ── */}
      <main className="flex-1 px-6 py-6 overflow-y-auto">
        {/* deployment panel */}
        <DeployPanel />

        {/* page title */}
        <div className="flex items-baseline gap-4 mb-6">
          <h1 className="font-[var(--font-display)] text-2xl text-white/90 italic">Paper</h1>
          <span className="text-[10px] text-ghost tracking-[0.2em]">// PAPER TRADING ENGINE</span>
          {error && (
            <span className="text-[10px] text-loss tracking-wider ml-auto">
              API ERROR: {error}
            </span>
          )}
        </div>

        {/* content */}
        {!portfolio ? (
          <div className="flex items-center justify-center py-24">
            <span className="text-ghost/40 text-xs tracking-widest animate-pulse">CONNECTING...</span>
          </div>
        ) : sessions.length === 0 ? (
          <EmptyState />
        ) : activeSession ? (
          <SessionDetail
            key={activeSession}
            sessionId={activeSession}
            onStop={() => setSelectedSession(null)}
            onKill={() => setSelectedSession(null)}
          />
        ) : (
          <EmptyState />
        )}
      </main>
    </div>
  )
}
