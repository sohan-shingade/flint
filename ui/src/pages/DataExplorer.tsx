import { useEffect, useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'

interface CandleData {
  ts: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

interface MarketInfo {
  market: string
  resolution_s: number
  candle_count: number
  first_ts: number
  last_ts: number
}

const tooltipStyle = {
  background: '#111114',
  border: '1px solid #2a2a32',
  borderRadius: 0,
  fontSize: 11,
  fontFamily: "'JetBrains Mono', monospace",
}

const fmtDate = (ts: number) =>
  new Date(ts * 1000).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })

const fmtRes = (s: number) => {
  if (s >= 86400) return `${s / 86400}d`
  if (s >= 3600) return `${s / 3600}h`
  if (s >= 60) return `${s / 60}m`
  return `${s}s`
}

const fmtDuration = (startTs: number, endTs: number) => {
  const days = Math.round((endTs - startTs) / 86400)
  if (days >= 365) return `${(days / 365).toFixed(1)}y`
  if (days >= 30) return `${Math.round(days / 30)}mo`
  return `${days}d`
}

export default function DataExplorer() {
  const [markets, setMarkets] = useState<MarketInfo[]>([])
  const [market, setMarket] = useState('SOL-PERP')
  const [candles, setCandles] = useState<CandleData[]>([])
  const [loading, setLoading] = useState(false)
  const [inventoryLoading, setInventoryLoading] = useState(true)

  useEffect(() => {
    fetch('/api/v1/data/markets')
      .then(r => r.json())
      .then(d => setMarkets(d.markets || []))
      .catch(() => {})
      .finally(() => setInventoryLoading(false))
  }, [])

  const loadData = async () => {
    setLoading(true)
    try {
      const res = await fetch(`/api/v1/data/ohlcv?market=${market}&resolution_s=3600&limit=500`)
      const data = await res.json()
      setCandles(data.candles || [])
    } catch {
      setCandles([])
    }
    setLoading(false)
  }

  const handleMarketClick = (m: string) => {
    setMarket(m)
  }

  const chartData = candles.map((c) => ({
    date: new Date(c.ts * 1000).toLocaleDateString(),
    close: c.close,
    volume: c.volume,
  }))

  const totalRecords = markets.reduce((sum, m) => sum + m.candle_count, 0)
  const uniqueMarkets = [...new Set(markets.map(m => m.market))]
  const uniqueResolutions = [...new Set(markets.map(m => m.resolution_s))].sort()

  const inputClass = 'bg-void border border-border text-terminal text-xs px-2.5 py-2 focus:border-amber/50 focus:outline-none transition-colors'

  return (
    <div className="space-y-6">
      <div className="flex items-baseline gap-4">
        <h1 className="font-[var(--font-display)] text-2xl text-white/90 italic">Data Explorer</h1>
        <span className="text-[10px] text-ghost tracking-[0.2em]">// OHLCV BROWSER</span>
      </div>

      {/* ============================================================
          DATA INVENTORY
          ============================================================ */}
      <div className="border border-border bg-surface/60 backdrop-blur">
        <div className="px-4 py-2.5 border-b border-border flex items-center gap-2">
          <span className="w-2 h-2 bg-amber/60" />
          <span className="text-[10px] text-ghost tracking-[0.2em]">DATA.INVENTORY</span>
          {markets.length > 0 && (
            <span className="ml-auto text-[10px] text-amber/40">
              {uniqueMarkets.length} markets / {uniqueResolutions.map(fmtRes).join(', ')} / {totalRecords.toLocaleString()} records
            </span>
          )}
        </div>

        {inventoryLoading ? (
          <div className="p-6 text-center text-[11px] text-ghost/40 tracking-wider">
            SCANNING DATABASE...
          </div>
        ) : markets.length === 0 ? (
          <div className="p-6 text-center">
            <div className="text-ghost/30 text-xs tracking-[0.3em]">NO DATA AVAILABLE</div>
            <div className="text-ghost/20 text-[10px] mt-2">
              Run a backtest or start the collector to populate the database
            </div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-ghost/40 border-b border-border text-[10px] tracking-[0.15em]">
                  <th className="py-2.5 px-4">MARKET</th>
                  <th className="py-2.5 px-4">RESOLUTION</th>
                  <th className="py-2.5 px-4 text-right">RECORDS</th>
                  <th className="py-2.5 px-4">FROM</th>
                  <th className="py-2.5 px-4">TO</th>
                  <th className="py-2.5 px-4">SPAN</th>
                  <th className="py-2.5 px-4"></th>
                </tr>
              </thead>
              <tbody>
                {markets.map((m, i) => (
                  <tr
                    key={i}
                    className={`border-b border-border/30 hover:bg-amber-glow transition-colors cursor-pointer ${
                      market === m.market ? 'bg-amber-glow/50' : ''
                    }`}
                    onClick={() => handleMarketClick(m.market)}
                  >
                    <td className="py-2.5 px-4 text-amber font-medium">{m.market}</td>
                    <td className="py-2.5 px-4">
                      <span className="inline-block px-1.5 py-0.5 border border-border text-ghost text-[10px]">
                        {fmtRes(m.resolution_s)}
                      </span>
                    </td>
                    <td className="py-2.5 px-4 text-right text-white/70 tabular-nums">
                      {m.candle_count.toLocaleString()}
                    </td>
                    <td className="py-2.5 px-4 text-ghost/50 tabular-nums">{fmtDate(m.first_ts)}</td>
                    <td className="py-2.5 px-4 text-ghost/50 tabular-nums">{fmtDate(m.last_ts)}</td>
                    <td className="py-2.5 px-4">
                      <span className="text-ghost/40">{fmtDuration(m.first_ts, m.last_ts)}</span>
                    </td>
                    <td className="py-2.5 px-4">
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          setMarket(m.market)
                          setTimeout(loadData, 0)
                        }}
                        className="text-[10px] text-ghost/40 hover:text-amber tracking-wider transition-colors"
                      >
                        LOAD {'>'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ============================================================
          QUERY CONTROLS
          ============================================================ */}
      <div className="border border-border bg-surface/60 backdrop-blur p-4 flex gap-3 items-end">
        <div>
          <label className="block text-[10px] text-ghost tracking-[0.15em] mb-1.5">MARKET</label>
          <select value={market} onChange={(e) => setMarket(e.target.value)} className={inputClass}>
            {uniqueMarkets.length > 0
              ? uniqueMarkets.map(m => <option key={m}>{m}</option>)
              : <>
                  <option>SOL-PERP</option>
                  <option>BTC-PERP</option>
                  <option>ETH-PERP</option>
                </>
            }
          </select>
        </div>
        <button
          onClick={loadData}
          disabled={loading}
          className="px-4 py-2 border border-border text-[11px] text-ghost tracking-[0.1em] hover:text-amber hover:border-amber/30 disabled:opacity-40 transition-all"
        >
          {loading ? '> LOADING...' : '> QUERY'}
        </button>
        {candles.length > 0 && (
          <span className="text-[10px] text-amber/60 tracking-wider">{candles.length} records</span>
        )}
      </div>

      {/* ============================================================
          CHARTS
          ============================================================ */}
      {chartData.length > 0 && (
        <div style={{ animation: 'fadeUp 0.4s ease' }}>
          <div className="border border-border bg-surface/60 backdrop-blur p-4 mb-px">
            <div className="text-[10px] text-ghost tracking-[0.2em] mb-3">PRICE.CLOSE</div>
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="2 6" stroke="#1a1a1f" />
                <XAxis dataKey="date" tick={{ fill: '#555560', fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }} minTickGap={60} interval="preserveStartEnd" />
                <YAxis tick={{ fill: '#555560', fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }} minTickGap={60} domain={['auto', 'auto']} />
                <Tooltip contentStyle={tooltipStyle} />
                <Line type="monotone" dataKey="close" stroke="#e8a849" strokeWidth={1.5} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="border border-border bg-surface/60 backdrop-blur p-4">
            <div className="text-[10px] text-ghost tracking-[0.2em] mb-3">VOLUME</div>
            <ResponsiveContainer width="100%" height={140}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="2 6" stroke="#1a1a1f" />
                <XAxis dataKey="date" tick={{ fill: '#555560', fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }} minTickGap={60} interval="preserveStartEnd" />
                <YAxis tick={{ fill: '#555560', fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }} minTickGap={60} />
                <Tooltip contentStyle={tooltipStyle} />
                <Line type="monotone" dataKey="volume" stroke="#555560" strokeWidth={1} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {candles.length === 0 && !loading && (
        <div className="border border-border/50 py-16 text-center">
          <div className="text-ghost/30 text-xs tracking-[0.3em]">NO DATA LOADED</div>
          <div className="text-ghost/20 text-[10px] mt-2">Select a market from the inventory above or click QUERY</div>
        </div>
      )}
    </div>
  )
}
