import { useState } from 'react'
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

const tooltipStyle = {
  background: '#111114',
  border: '1px solid #2a2a32',
  borderRadius: 0,
  fontSize: 11,
  fontFamily: "'JetBrains Mono', monospace",
}

export default function DataExplorer() {
  const [market, setMarket] = useState('SOL-PERP')
  const [candles, setCandles] = useState<CandleData[]>([])
  const [loading, setLoading] = useState(false)

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

  const chartData = candles.map((c) => ({
    date: new Date(c.ts * 1000).toLocaleDateString(),
    close: c.close,
    volume: c.volume,
  }))

  const inputClass = 'bg-void border border-border text-terminal text-xs px-2.5 py-2 focus:border-amber/50 focus:outline-none transition-colors'

  return (
    <div className="space-y-6">
      <div className="flex items-baseline gap-4">
        <h1 className="font-[var(--font-display)] text-2xl text-white/90 italic">Data Explorer</h1>
        <span className="text-[10px] text-ghost tracking-[0.2em]">// OHLCV BROWSER</span>
      </div>

      <div className="border border-border bg-surface/60 backdrop-blur p-4 flex gap-3 items-end">
        <div>
          <label className="block text-[10px] text-ghost tracking-[0.15em] mb-1.5">MARKET</label>
          <select value={market} onChange={(e) => setMarket(e.target.value)} className={inputClass}>
            <option>SOL-PERP</option>
            <option>BTC-PERP</option>
            <option>ETH-PERP</option>
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
          <div className="text-ghost/20 text-[10px] mt-2">Select a market and click QUERY</div>
        </div>
      )}
    </div>
  )
}
