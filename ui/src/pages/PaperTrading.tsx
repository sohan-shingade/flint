import { useState } from 'react'
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
  }
  selected: boolean
  onClick: () => void
}

function SidebarCard({ session, selected, onClick }: SidebarCardProps) {
  const pnlPositive = session.pnl >= 0

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
      <div className="pl-3.5 mt-0.5">
        <span className={`text-[9px] tracking-[0.15em] ${statusTextColor(session.status)}`}>
          {statusLabel(session.status)}
        </span>
      </div>
    </button>
  )
}

/* ── portfolio overview (no session selected) ────────────── */

interface PortfolioViewProps {
  portfolio: {
    total_equity: number
    total_pnl: number
    total_initial_capital: number
    active_sessions: number
    total_sessions: number
    per_strategy: any[]
  }
}

function PortfolioView({ portfolio }: PortfolioViewProps) {
  const totalReturn =
    portfolio.total_initial_capital > 0
      ? ((portfolio.total_pnl / portfolio.total_initial_capital) * 100)
      : 0
  const pnlPositive = portfolio.total_pnl >= 0

  return (
    <div className="space-y-6">
      {/* total account value hero */}
      <div className="bg-surface/60 border border-border p-6 text-center">
        <div className="text-[9px] text-ghost/60 tracking-[0.25em] mb-2">TOTAL ACCOUNT VALUE</div>
        <div className="text-4xl font-bold text-white/90 tabular-nums">
          {fmtUsd(portfolio.total_equity)}
        </div>
        <div className={`text-lg font-medium tabular-nums mt-1 ${pnlPositive ? 'text-phosphor' : 'text-loss'}`}>
          {pnlPositive ? '+' : ''}{fmtUsd(portfolio.total_pnl)}
        </div>
      </div>

      {/* metrics row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-px bg-border">
        {[
          {
            label: 'TOTAL.EQUITY',
            value: fmtUsd(portfolio.total_equity),
            accent: undefined as boolean | undefined,
          },
          {
            label: 'TOTAL.PNL',
            value: (pnlPositive ? '+' : '') + fmtUsd(portfolio.total_pnl),
            accent: pnlPositive,
          },
          {
            label: 'RETURN',
            value: `${totalReturn >= 0 ? '+' : ''}${fmt(totalReturn)}%`,
            accent: totalReturn >= 0,
          },
          {
            label: 'SESSIONS',
            value: `${portfolio.active_sessions} / ${portfolio.total_sessions}`,
            accent: undefined,
          },
        ].map((item, i) => (
          <div
            key={item.label}
            className="bg-surface p-4 hover:bg-panel transition-colors"
            style={{ animation: `fadeUp 0.3s ease ${i * 0.05}s both` }}
          >
            <div className="text-[9px] text-ghost/60 tracking-[0.2em] mb-2">{item.label}</div>
            <div
              className={`text-xl font-bold tabular-nums ${
                item.accent === undefined
                  ? 'text-white/80'
                  : item.accent
                    ? 'text-phosphor'
                    : 'text-loss'
              }`}
            >
              {item.value}
            </div>
          </div>
        ))}
      </div>

      {/* portfolio equity chart */}
      <div className="border border-border bg-surface/60 p-4">
        <div className="flex items-center gap-2 mb-3">
          <span className="w-2 h-2 bg-amber/60" />
          <span className="text-[10px] text-ghost tracking-[0.2em]">PORTFOLIO.EQUITY</span>
        </div>
        <div className="text-[10px] text-ghost/40 tracking-wider text-center py-8">
          Equity chart updates as strategies trade
        </div>
      </div>

      {/* prompt */}
      <div className="border border-border bg-surface/40 p-8 text-center">
        <p className="text-ghost/60 text-sm mb-4">
          Select a strategy from the sidebar to view details, or deploy one from BacktestLab
        </p>
        <Link
          to="/backtest"
          className="inline-flex items-center gap-2 px-5 py-2 bg-amber text-void text-xs font-semibold tracking-[0.15em] hover:bg-amber-dim transition-colors"
        >
          {'>'} DEPLOY_FROM_LAB
        </Link>
      </div>

      {/* per-strategy table */}
      {portfolio.per_strategy.length > 0 && (
        <div className="border border-border bg-surface/60 backdrop-blur">
          <div className="px-4 py-2.5 border-b border-border flex items-center gap-2">
            <span className="w-2 h-2 bg-phosphor/60" />
            <span className="text-[10px] text-ghost tracking-[0.2em]">ALL.STRATEGIES</span>
          </div>
          <div className="divide-y divide-border">
            {portfolio.per_strategy.map((s: any) => (
              <div key={s.session_id} className="px-4 py-3 flex items-center gap-4 hover:bg-amber-glow transition-colors">
                <StatusDot status={s.status} />
                <span className="text-xs text-white/80 font-medium w-40 truncate">{s.strategy_name}</span>
                <span className="text-[10px] text-ghost w-24">{s.market}</span>
                <span className="text-xs tabular-nums text-white/60 w-28">{fmtUsd(s.equity)}</span>
                <span className={`text-xs tabular-nums font-medium ml-auto ${s.pnl >= 0 ? 'text-phosphor' : 'text-loss'}`}>
                  {s.pnl >= 0 ? '+' : ''}{fmtUsd(s.pnl)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
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

  if (!status) {
    return (
      <div className="flex items-center justify-center h-40">
        <span className="text-ghost/40 text-xs tracking-widest animate-pulse">LOADING SESSION...</span>
      </div>
    )
  }

  const pnlPositive = (status.pnl ?? 0) >= 0
  const upnlPositive = (status.unrealized_pnl ?? 0) >= 0

  const metrics: { label: string; value: string; accent: boolean | undefined }[] = [
    { label: 'EQUITY', value: fmtUsd(status.equity), accent: undefined },
    { label: 'REALIZED.PNL', value: (pnlPositive ? '+' : '') + fmtUsd(status.pnl), accent: pnlPositive },
    { label: 'UNREALIZED.PNL', value: (upnlPositive ? '+' : '') + fmtUsd(status.unrealized_pnl), accent: upnlPositive },
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
        <div className="flex items-baseline gap-3">
          <h2 className="font-[var(--font-display)] text-xl text-white/90 italic">
            {status.strategy}
          </h2>
          <span className="text-[10px] text-ghost tracking-[0.2em]">// {status.market}</span>
          <span className={`text-[10px] tracking-[0.15em] ${statusTextColor(status.status ?? status.phase)}`}>
            {statusLabel(status.status ?? status.phase)}
          </span>
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

      {/* equity curve */}
      {status.equity_curve && status.equity_curve.length > 0 && (
        <div className="border border-border bg-surface/60 p-4">
          <div className="flex items-center gap-2 mb-3">
            <span className="w-2 h-2 bg-amber/60" />
            <span className="text-[10px] text-ghost tracking-[0.2em]">EQUITY.CURVE</span>
          </div>
          <EquityCurve
            equity={status.equity_curve.map((e: any) => [e.ts, e.equity] as [number, number])}
            height={250}
          />
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
                <span className="text-white/70 tabular-nums w-24">{fmtUsd(pos.notional ?? pos.size)}</span>
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
                  {['TIME', 'SIDE', 'PRICE', 'SIZE', 'FEE', 'PNL'].map(h => (
                    <th key={h} className="px-3 py-2 text-left text-[9px] text-ghost/50 tracking-[0.2em] font-normal">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {trades.slice().reverse().map((t: any, i: number) => (
                  <tr key={i} className="hover:bg-amber-glow transition-colors">
                    <td className="px-3 py-2 text-ghost/60 tabular-nums">
                      {t.ts ? new Date(t.ts * 1000).toLocaleTimeString('en-US', { hour12: false }) : '-'}
                    </td>
                    <td className={`px-3 py-2 font-semibold ${t.side === 'buy' || t.side === 'long' ? 'text-phosphor' : 'text-loss'}`}>
                      {t.side?.toUpperCase()}
                    </td>
                    <td className="px-3 py-2 text-white/70 tabular-nums">{fmtUsd(t.price ?? t.fill_price)}</td>
                    <td className="px-3 py-2 text-white/70 tabular-nums">{fmt(t.size ?? t.quantity, 4)}</td>
                    <td className="px-3 py-2 text-ghost/60 tabular-nums">{fmtUsd(t.fee)}</td>
                    <td className={`px-3 py-2 tabular-nums font-medium ${(t.pnl ?? 0) >= 0 ? 'text-phosphor' : 'text-loss'}`}>
                      {t.pnl != null ? ((t.pnl >= 0 ? '+' : '') + fmtUsd(t.pnl)) : '-'}
                    </td>
                  </tr>
                ))}
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

  // Deselect if the selected session disappears
  const sessionExists = sessions.some((s: any) => s.session_id === selectedSession)
  if (selectedSession && !sessionExists && sessions.length > 0) {
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
                selected={selectedSession === s.session_id}
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
        ) : selectedSession ? (
          <SessionDetail
            key={selectedSession}
            sessionId={selectedSession}
            onStop={() => setSelectedSession(null)}
            onKill={() => setSelectedSession(null)}
          />
        ) : (
          <PortfolioView portfolio={portfolio} />
        )}
      </main>
    </div>
  )
}
