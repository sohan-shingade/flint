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
        <span className="text-[10px] text-ghost tracking-wider">{session.market}</span>
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
    fetch(`/api/v1/data/ohlcv?market=${status.market}&resolution=3600&start_ts=${startTs}&end_ts=${endTs}`)
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
        </div>
      </div>

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
