import { useState, useEffect } from 'react'

/* ── types ───────────────────────────────────────────────── */

interface FundingEntry {
  market: string
  venue: string
  rate: number
  ts: number
}

/* ── helpers ─────────────────────────────────────────────── */

// Convert rate to background color: green positive, red negative
function rateColor(rate: number | undefined): string {
  if (rate == null) return 'rgba(255,255,255,0.02)'
  const intensity = Math.min(Math.abs(rate) * 10000, 100) // scale bps to 0-100
  const alpha = 0.15 + (intensity / 100) * 0.65
  if (rate > 0) return `rgba(0, 255, 136, ${alpha})`
  return `rgba(255, 80, 80, ${alpha})`
}

function rateTextColor(rate: number | undefined): string {
  if (rate == null) return 'var(--color-ghost)'
  if (rate > 0) return 'var(--color-phosphor)'
  return 'var(--color-loss)'
}

function fmtRate(rate: number | undefined): string {
  if (rate == null) return '—'
  const bps = rate * 10000
  return `${bps >= 0 ? '+' : ''}${bps.toFixed(2)}bps`
}

/* ── main page ───────────────────────────────────────────── */

export default function FundingHeatmap() {
  const [entries, setEntries] = useState<FundingEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [lastUpdated, setLastUpdated] = useState<string>('')

  const fetchFunding = () => {
    setLoading(true)
    fetch('/api/v1/data/funding')
      .then(r => r.json())
      .then(d => {
        setEntries(d.rates || d.funding || [])
        setLastUpdated(new Date().toLocaleTimeString('en-US', { hour12: false, timeZone: 'UTC' }) + ' UTC')
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    fetchFunding()
    const id = setInterval(fetchFunding, 60000)
    return () => clearInterval(id)
  }, [])

  // Build matrix: markets x venues
  const markets = [...new Set(entries.map(e => e.market))].sort()
  const venues = [...new Set(entries.map(e => e.venue))].sort()

  // rate lookup: matrix[market][venue] = rate
  const matrix: Record<string, Record<string, number>> = {}
  for (const e of entries) {
    if (!matrix[e.market]) matrix[e.market] = {}
    matrix[e.market][e.venue] = e.rate
  }

  // Compute spread: max rate - min rate across venues per market
  const getSpread = (mkt: string): number | undefined => {
    const rates = venues.map(v => matrix[mkt]?.[v]).filter(r => r != null) as number[]
    if (rates.length < 2) return undefined
    return Math.max(...rates) - Math.min(...rates)
  }

  return (
    <div className="space-y-6">
      {/* header */}
      <div className="flex items-baseline gap-4">
        <h1 className="font-[var(--font-display)] text-2xl text-white/90 italic">Funding</h1>
        <span className="text-[10px] text-ghost tracking-[0.2em]">// CROSS-VENUE FUNDING SPREAD HEATMAP</span>
        {loading && <span className="text-[10px] text-ghost/40 animate-pulse ml-auto">LOADING...</span>}
        {!loading && lastUpdated && (
          <span className="text-[10px] text-ghost/40 ml-auto">updated {lastUpdated}</span>
        )}
      </div>

      {/* legend */}
      <div className="flex items-center gap-6 text-[10px] text-ghost/60">
        <span className="flex items-center gap-2">
          <span style={{ width: 16, height: 10, display: 'inline-block', background: 'rgba(0,255,136,0.5)', border: '1px solid rgba(0,255,136,0.2)' }} />
          Positive funding (longs pay shorts)
        </span>
        <span className="flex items-center gap-2">
          <span style={{ width: 16, height: 10, display: 'inline-block', background: 'rgba(255,80,80,0.5)', border: '1px solid rgba(255,80,80,0.2)' }} />
          Negative funding (shorts pay longs)
        </span>
        <span className="text-ghost/30">| Rates shown as bps/hr | Refreshes every 60s</span>
      </div>

      {/* heatmap table */}
      <div className="border border-border bg-surface/60 overflow-x-auto">
        <div className="px-4 py-2.5 border-b border-border flex items-center gap-2">
          <span className="w-2 h-2 bg-amber/60" />
          <span className="text-[10px] text-ghost tracking-[0.2em]">RATE.MATRIX</span>
          <span className="text-[10px] text-ghost/40 ml-2">{markets.length} markets × {venues.length} venues</span>
        </div>
        {markets.length === 0 ? (
          <div className="px-4 py-12 text-center text-[10px] text-ghost/30 tracking-wider">
            {loading ? 'LOADING FUNDING DATA...' : 'NO FUNDING DATA AVAILABLE'}
          </div>
        ) : (
          <table className="w-full text-[11px] font-mono border-collapse">
            <thead>
              <tr className="border-b border-border">
                <th className="px-4 py-2.5 text-left text-[9px] text-ghost/50 tracking-[0.2em] font-normal sticky left-0 bg-surface/90 backdrop-blur w-36">
                  MARKET
                </th>
                {venues.map(v => (
                  <th key={v} className="px-3 py-2.5 text-center text-[9px] text-ghost/50 tracking-[0.15em] font-normal whitespace-nowrap min-w-[90px]">
                    {v.toUpperCase()}
                  </th>
                ))}
                <th className="px-3 py-2.5 text-center text-[9px] text-ghost/50 tracking-[0.15em] font-normal whitespace-nowrap min-w-[80px]">
                  SPREAD
                </th>
              </tr>
            </thead>
            <tbody>
              {markets.map((mkt, i) => {
                const spread = getSpread(mkt)
                return (
                  <tr
                    key={mkt}
                    className="border-b border-border/40 hover:brightness-110 transition-all"
                    style={{ animation: `fadeUp 0.3s ease ${i * 0.02}s both` }}
                  >
                    <td className="px-4 py-2.5 text-amber text-xs sticky left-0 bg-surface/90 backdrop-blur font-medium">
                      {mkt}
                    </td>
                    {venues.map(v => {
                      const rate = matrix[mkt]?.[v]
                      return (
                        <td
                          key={v}
                          className="px-3 py-2.5 text-center tabular-nums text-[11px]"
                          style={{
                            background: rateColor(rate),
                            color: rateTextColor(rate),
                          }}
                        >
                          {fmtRate(rate)}
                        </td>
                      )
                    })}
                    <td
                      className="px-3 py-2.5 text-center tabular-nums text-[11px]"
                      style={{
                        color: spread != null && spread > 0.0005 ? 'var(--color-amber)' : 'var(--color-ghost)',
                      }}
                    >
                      {spread != null ? `${(spread * 10000).toFixed(2)}bps` : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* refresh button */}
      <div className="flex justify-end">
        <button
          onClick={fetchFunding}
          disabled={loading}
          className="px-4 py-1.5 text-[10px] tracking-[0.15em] border border-border text-ghost hover:border-amber hover:text-amber transition-colors disabled:opacity-40"
        >
          {loading ? 'REFRESHING...' : '> REFRESH'}
        </button>
      </div>
    </div>
  )
}
