import { useEffect, useState } from 'react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { useLiveMonitor, useLiveSessions } from '../hooks/useLiveMonitor'
import { useWebSocket } from '../hooks/useWebSocket'

/** D-4.3-websocket: payload shape LiveExecutionContext broadcasts. */
interface LiveWsFill {
  type: 'fill' | 'ping'
  order_id?: string
  market?: string
  venue?: string
  side?: string
  price?: number
  size?: number
  fee?: number
  ts?: number
}

/* ── helpers ─────────────────────────────────────────────── */

const fmt = (v: number | undefined | null, dec = 2) =>
  v != null ? v.toFixed(dec) : '-'

const fmtUsd = (v: number | undefined | null) =>
  v != null ? `$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '-'

const fmtTime = (ts: number) =>
  new Date(ts * 1000).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    hour12: false, timeZone: 'UTC',
  })

/* ── metric tile ─────────────────────────────────────────── */

function MetricTile({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="bg-surface p-3 hover:bg-panel transition-colors">
      <div className="text-[9px] text-ghost/60 tracking-[0.2em] mb-1">{label}</div>
      <div className={`text-sm font-medium tabular-nums ${
        accent === undefined ? 'text-white/80' : accent ? 'text-phosphor' : 'text-loss'
      }`}>
        {value}
      </div>
    </div>
  )
}

/* ── equity chart tooltip ────────────────────────────────── */

function EqTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div className="bg-panel border border-border px-3 py-2 text-[11px] font-mono space-y-0.5">
      <div className="text-ghost/60">{fmtTime(d.ts)}</div>
      <div className="text-amber">equity: {fmtUsd(d.equity)}</div>
      <div className="text-ghost/70">cash: {fmtUsd(d.cash)}</div>
      <div className={d.unrealized_pnl >= 0 ? 'text-phosphor' : 'text-loss'}>
        upnl: {d.unrealized_pnl >= 0 ? '+' : ''}{fmtUsd(d.unrealized_pnl)}
      </div>
    </div>
  )
}

/* ── main page ───────────────────────────────────────────── */

export default function LiveMonitor() {
  const sessions = useLiveSessions()
  const [selectedId, setSelectedId] = useState('')

  const activeId = selectedId || (sessions.length > 0 ? sessions[0].session_id : '')
  const { equity, fills: polledFills, error } = useLiveMonitor(activeId)

  // D-4.3-websocket: subscribe to /ws/live/{id} for real-time fill
  // events. Merge into the polled fills list, deduping by order_id +
  // ts so the same fill doesn't appear twice when poll + WS race.
  const ws = useWebSocket<LiveWsFill>(`/ws/live/${activeId}`, {
    enabled: !!activeId,
  })
  const [wsFills, setWsFills] = useState<any[]>([])
  useEffect(() => {
    if (ws.data?.type === 'fill') {
      setWsFills((prev) => [...prev, ws.data as any])
    }
  }, [ws.data])
  // Reset WS-fill buffer when the selected session changes.
  useEffect(() => {
    setWsFills([])
  }, [activeId])

  const fills = (() => {
    if (wsFills.length === 0) return polledFills
    const seen = new Set(polledFills.map((f) => `${f.order_id}|${f.ts}`))
    const merged = [...polledFills]
    for (const f of wsFills) {
      const key = `${f.order_id}|${f.ts}`
      if (!seen.has(key)) {
        merged.push(f)
        seen.add(key)
      }
    }
    return merged.sort((a, b) => a.ts - b.ts)
  })()

  const activeSession = sessions.find(s => s.session_id === activeId)

  // Derive metrics from latest equity point
  const latest = equity.length > 0 ? equity[equity.length - 1] : null
  const first = equity.length > 0 ? equity[0] : null
  const drawdown = latest && first
    ? Math.max(0, ((first.equity - Math.min(...equity.map(e => e.equity))) / first.equity) * 100)
    : 0
  const pnl = latest && first ? latest.equity - first.equity : 0

  const openPositions: { market: string; size: number; upnl: number }[] = []

  return (
    <div className="space-y-6">
      {/* header */}
      <div className="flex items-baseline gap-4">
        <h1 className="font-[var(--font-display)] text-2xl text-white/90 italic">Live</h1>
        <span className="text-[10px] text-ghost tracking-[0.2em]">// LIVE TRADING MONITOR</span>
        {/* D-4.3-websocket: per-session WS health */}
        {activeId && (
          <span
            className={`text-[10px] tracking-[0.15em] ${
              ws.status === 'open' ? 'text-gain' : ws.status === 'connecting' ? 'text-amber' : 'text-ghost/40'
            }`}
            title={`WebSocket ${ws.status}${ws.errorCount > 0 ? ` (${ws.errorCount} errors)` : ''}`}
          >
            {ws.status === 'open' ? 'WS LIVE' : ws.status === 'connecting' ? 'WS CONNECTING' : 'WS OFFLINE'}
          </span>
        )}
        {error && <span className="text-[10px] text-loss ml-auto">ERR: {error}</span>}
      </div>

      {/* session selector */}
      <div className="border border-border bg-surface/60">
        <div className="px-4 py-2.5 border-b border-border flex items-center gap-2">
          <span className="w-2 h-2 bg-amber/60" />
          <span className="text-[10px] text-ghost tracking-[0.2em]">SESSION.SELECTOR</span>
          <span className="ml-auto text-[10px] text-ghost/40 tabular-nums">{sessions.length} sessions</span>
        </div>
        <div className="p-4">
          {sessions.length === 0 ? (
            <div className="text-[10px] text-ghost/40 tracking-wider py-4 text-center">
              NO ACTIVE LIVE SESSIONS
            </div>
          ) : (
            <select
              value={activeId}
              onChange={e => setSelectedId(e.target.value)}
              className="bg-panel border border-border text-terminal text-xs px-3 py-1.5 focus:outline-none focus:border-amber w-full max-w-md"
            >
              {sessions.map(s => (
                <option key={s.session_id} value={s.session_id}>
                  {s.strategy} / {s.market} / {s.venue} — {s.status.toUpperCase()}
                </option>
              ))}
            </select>
          )}
          {activeSession && (
            <div className="mt-2 flex items-center gap-4 text-[10px] text-ghost/50">
              <span>venue: <span className="text-amber">{activeSession.venue}</span></span>
              <span>market: <span className="text-amber">{activeSession.market}</span></span>
              <span>since: <span className="text-ghost/70">{fmtTime(activeSession.started_at)}</span> UTC</span>
            </div>
          )}
        </div>
      </div>

      {/* metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-px bg-border">
        <MetricTile label="EQUITY" value={fmtUsd(latest?.equity)} />
        <MetricTile label="SESSION.PNL" value={(pnl >= 0 ? '+' : '') + fmtUsd(pnl)} accent={pnl >= 0 ? true : false} />
        <MetricTile label="OPEN.POSITIONS" value={String(openPositions.length)} />
        <MetricTile label="MAX.DRAWDOWN" value={`${fmt(drawdown)}%`} accent={drawdown < 5 ? true : false} />
      </div>

      {/* equity curve */}
      <div className="border border-border bg-surface/60">
        <div className="px-4 py-2.5 border-b border-border flex items-center gap-2">
          <span className="w-2 h-2 bg-amber/60" />
          <span className="text-[10px] text-ghost tracking-[0.2em]">EQUITY.CURVE</span>
          <span className="ml-auto text-[10px] text-ghost/30 animate-pulse">POLLING 2s</span>
        </div>
        <div className="p-4">
          {equity.length === 0 ? (
            <div className="flex items-center justify-center h-40">
              <span className="text-ghost/30 text-[10px] tracking-[0.3em]">
                {activeId ? 'WAITING FOR DATA...' : 'SELECT A SESSION'}
              </span>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={equity} margin={{ top: 4, right: 8, left: 8, bottom: 4 }}>
                <XAxis
                  dataKey="ts"
                  tickFormatter={ts => new Date(ts * 1000).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })}
                  tick={{ fill: 'var(--color-ghost)', fontSize: 9 }}
                  axisLine={{ stroke: 'var(--color-border)' }}
                  tickLine={false}
                />
                <YAxis
                  tickFormatter={v => `$${(v / 1000).toFixed(1)}k`}
                  tick={{ fill: 'var(--color-ghost)', fontSize: 9 }}
                  axisLine={false}
                  tickLine={false}
                  width={52}
                />
                <Tooltip content={<EqTooltip />} />
                <Line
                  type="monotone"
                  dataKey="equity"
                  stroke="var(--color-amber)"
                  strokeWidth={1.5}
                  dot={false}
                  activeDot={{ r: 3, fill: 'var(--color-amber)' }}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* open positions placeholder */}
      <div className="border border-border bg-surface/60">
        <div className="px-4 py-2.5 border-b border-border flex items-center gap-2">
          <span className="w-2 h-2 bg-phosphor/60" />
          <span className="text-[10px] text-ghost tracking-[0.2em]">OPEN.POSITIONS</span>
        </div>
        <div className="px-4 py-6 text-center text-[10px] text-ghost/30 tracking-wider">
          {activeId ? 'NO OPEN POSITIONS' : 'SELECT A SESSION TO VIEW POSITIONS'}
        </div>
      </div>

      {/* recent fills */}
      <div className="border border-border bg-surface/60">
        <div className="px-4 py-2.5 border-b border-border flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 bg-phosphor/60" />
            <span className="text-[10px] text-ghost tracking-[0.2em]">RECENT.FILLS</span>
          </div>
          <span className="text-[10px] text-ghost/50">{Math.min(fills.length, 20)} shown</span>
        </div>
        {fills.length === 0 ? (
          <div className="px-4 py-6 text-center text-[10px] text-ghost/30 tracking-wider">
            {activeId ? 'NO FILLS YET' : 'SELECT A SESSION'}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[11px] font-mono">
              <thead>
                <tr className="border-b border-border">
                  {['TIME', 'MARKET', 'SIDE', 'PRICE', 'SIZE', 'FEE', 'VENUE'].map(h => (
                    <th key={h} className="px-3 py-2 text-left text-[9px] text-ghost/50 tracking-[0.2em] font-normal">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {fills.slice(-20).reverse().map((f, i) => (
                  <tr key={f.fill_id || i} className="hover:bg-amber-glow transition-colors">
                    <td className="px-3 py-2 text-ghost/60 whitespace-nowrap">{fmtTime(f.ts)}</td>
                    <td className="px-3 py-2 text-amber">{f.market}</td>
                    <td className={`px-3 py-2 font-semibold ${f.side === 'buy' || f.side === 'long' ? 'text-phosphor' : 'text-loss'}`}>
                      {f.side?.toUpperCase()}
                    </td>
                    <td className="px-3 py-2 text-white/70 tabular-nums">{fmtUsd(f.price)}</td>
                    <td className="px-3 py-2 text-white/70 tabular-nums">{fmt(f.size, 4)}</td>
                    <td className="px-3 py-2 text-ghost/60 tabular-nums">{fmtUsd(f.fee)}</td>
                    <td className="px-3 py-2 text-ghost/50">{f.venue}</td>
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
