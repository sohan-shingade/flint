import { useState, useEffect } from 'react'
import { ScatterChart, Scatter, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'

/* ── types ───────────────────────────────────────────────── */

interface Fill {
  fill_id: string
  ts: number
  market: string
  side: string
  size: number
  price: number
  fee: number
  impact_bps: number
  latency_ms: number
  tx_cost: number
  venue: string
}

/* ── helpers ─────────────────────────────────────────────── */

const fmtUsd = (v: number | undefined | null) =>
  v != null ? `$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '-'

const fmt = (v: number | undefined | null, dec = 2) =>
  v != null ? v.toFixed(dec) : '-'

const fmtTime = (ts: number) =>
  new Date(ts * 1000).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    hour12: false, timeZone: 'UTC',
  })

/* ── scatter tooltip ─────────────────────────────────────── */

function ScatterTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload as Fill
  return (
    <div className="bg-panel border border-border px-3 py-2 text-[11px] font-mono space-y-0.5">
      <div className="text-amber">{d.market} {d.side?.toUpperCase()}</div>
      <div className="text-ghost/70">size: {fmt(d.size, 4)}</div>
      <div className="text-ghost/70">impact: <span className="text-white/80">{fmt(d.impact_bps)} bps</span></div>
      <div className="text-ghost/60">price: {fmtUsd(d.price)}</div>
    </div>
  )
}

/* ── main page ───────────────────────────────────────────── */

export default function FillAnalysis() {
  const [fills, setFills] = useState<Fill[]>([])
  const [loading, setLoading] = useState(false)
  const [sessionId, setSessionId] = useState('')
  const [venue, setVenue] = useState('')
  const [market, setMarket] = useState('')
  const [markets, setMarkets] = useState<string[]>([])

  // Fetch available markets for filter
  useEffect(() => {
    fetch('/api/v1/data/markets')
      .then(r => r.json())
      .then(d => setMarkets(d.markets || []))
      .catch(() => {})
  }, [])

  // Fetch fills whenever filters change
  useEffect(() => {
    setLoading(true)
    const params = new URLSearchParams()
    if (sessionId) params.set('session_id', sessionId)
    if (venue) params.set('venue', venue)
    if (market) params.set('market', market)
    fetch(`/api/v1/live/fills?${params}`)
      .then(r => r.json())
      .then(d => setFills(d.fills || []))
      .catch(() => setFills([]))
      .finally(() => setLoading(false))
  }, [sessionId, venue, market])

  const scatterData = fills.filter(f => f.impact_bps != null && f.size != null)

  const venues = ['drift', 'hyperliquid', 'binance', 'okx', 'bybit']

  return (
    <div className="space-y-6">
      {/* header */}
      <div className="flex items-baseline gap-4">
        <h1 className="font-[var(--font-display)] text-2xl text-white/90 italic">Fills</h1>
        <span className="text-[10px] text-ghost tracking-[0.2em]">// FILL ANALYSIS</span>
        {loading && <span className="text-[10px] text-ghost/40 animate-pulse ml-auto">LOADING...</span>}
      </div>

      {/* filters */}
      <div className="border border-border bg-surface/60">
        <div className="px-4 py-2.5 border-b border-border flex items-center gap-2">
          <span className="w-2 h-2 bg-amber/60" />
          <span className="text-[10px] text-ghost tracking-[0.2em]">FILTERS</span>
        </div>
        <div className="p-4 flex flex-wrap gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-[9px] text-ghost/50 tracking-[0.2em]">SESSION.ID</label>
            <input
              type="text"
              value={sessionId}
              onChange={e => setSessionId(e.target.value)}
              placeholder="optional"
              className="bg-panel border border-border text-terminal text-xs px-3 py-1.5 focus:outline-none focus:border-amber w-48"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[9px] text-ghost/50 tracking-[0.2em]">VENUE</label>
            <select
              value={venue}
              onChange={e => setVenue(e.target.value)}
              className="bg-panel border border-border text-terminal text-xs px-3 py-1.5 focus:outline-none focus:border-amber w-36"
            >
              <option value="">All venues</option>
              {venues.map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[9px] text-ghost/50 tracking-[0.2em]">MARKET</label>
            <select
              value={market}
              onChange={e => setMarket(e.target.value)}
              className="bg-panel border border-border text-terminal text-xs px-3 py-1.5 focus:outline-none focus:border-amber w-40"
            >
              <option value="">All markets</option>
              {markets.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
        </div>
      </div>

      {/* scatter plot */}
      <div className="border border-border bg-surface/60">
        <div className="px-4 py-2.5 border-b border-border flex items-center gap-2">
          <span className="w-2 h-2 bg-amber/60" />
          <span className="text-[10px] text-ghost tracking-[0.2em]">IMPACT.VS.SIZE</span>
          <span className="text-[10px] text-ghost/40 ml-2">y=impact_bps x=size</span>
          <span className="ml-auto text-[10px] text-ghost/40">{scatterData.length} points</span>
        </div>
        <div className="p-4">
          {scatterData.length === 0 ? (
            <div className="flex items-center justify-center h-48">
              <span className="text-ghost/30 text-[10px] tracking-[0.3em]">NO FILL DATA</span>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <ScatterChart margin={{ top: 4, right: 8, left: 8, bottom: 4 }}>
                <CartesianGrid strokeDasharray="2 4" stroke="rgba(255,255,255,0.04)" />
                <XAxis
                  dataKey="size"
                  name="size"
                  tick={{ fill: 'var(--color-ghost)', fontSize: 9 }}
                  axisLine={{ stroke: 'var(--color-border)' }}
                  tickLine={false}
                  label={{ value: 'Size', position: 'insideBottom', offset: -2, fill: 'var(--color-ghost)', fontSize: 9 }}
                />
                <YAxis
                  dataKey="impact_bps"
                  name="impact_bps"
                  tick={{ fill: 'var(--color-ghost)', fontSize: 9 }}
                  axisLine={false}
                  tickLine={false}
                  width={48}
                  label={{ value: 'bps', angle: -90, position: 'insideLeft', fill: 'var(--color-ghost)', fontSize: 9 }}
                />
                <Tooltip content={<ScatterTooltip />} />
                <Scatter data={scatterData} fill="var(--color-amber)" fillOpacity={0.7} r={4} />
              </ScatterChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* fills table */}
      <div className="border border-border bg-surface/60">
        <div className="px-4 py-2.5 border-b border-border flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 bg-phosphor/60" />
            <span className="text-[10px] text-ghost tracking-[0.2em]">FILL.LOG</span>
          </div>
          <span className="text-[10px] text-ghost/50">{fills.length} records</span>
        </div>
        {fills.length === 0 ? (
          <div className="px-4 py-8 text-center text-[10px] text-ghost/30 tracking-wider">
            NO FILLS FOUND
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[11px] font-mono">
              <thead>
                <tr className="border-b border-border">
                  {['TIME', 'MARKET', 'SIDE', 'SIZE', 'PRICE', 'FEE', 'IMPACT.BPS', 'LATENCY.MS', 'TX.COST'].map(h => (
                    <th key={h} className="px-3 py-2 text-left text-[9px] text-ghost/50 tracking-[0.2em] font-normal whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {fills.map((f, i) => (
                  <tr key={f.fill_id || i} className="hover:bg-amber-glow transition-colors">
                    <td className="px-3 py-2 text-ghost/60 whitespace-nowrap">{fmtTime(f.ts)}</td>
                    <td className="px-3 py-2 text-amber">{f.market}</td>
                    <td className={`px-3 py-2 font-semibold ${f.side === 'buy' || f.side === 'long' ? 'text-phosphor' : 'text-loss'}`}>
                      {f.side?.toUpperCase()}
                    </td>
                    <td className="px-3 py-2 text-white/70 tabular-nums">{fmt(f.size, 4)}</td>
                    <td className="px-3 py-2 text-white/70 tabular-nums">{fmtUsd(f.price)}</td>
                    <td className="px-3 py-2 text-ghost/60 tabular-nums">{fmtUsd(f.fee)}</td>
                    <td className="px-3 py-2 tabular-nums text-white/80">{fmt(f.impact_bps)}</td>
                    <td className="px-3 py-2 tabular-nums text-ghost/60">{f.latency_ms != null ? `${f.latency_ms}ms` : '-'}</td>
                    <td className="px-3 py-2 tabular-nums text-ghost/60">{fmtUsd(f.tx_cost)}</td>
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
