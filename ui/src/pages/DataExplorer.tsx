import { useEffect, useState, useCallback } from 'react'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'
import InteractiveChart from '../components/InteractiveChart'

interface CandleData { ts: number; open: number; high: number; low: number; close: number; volume: number }
interface MarketInfo { market: string; resolution_s: number; candle_count: number; first_ts: number; last_ts: number }

const tooltipStyle = { background: '#111114', border: '1px solid #2a2a32', borderRadius: 0, fontSize: 11, fontFamily: "'JetBrains Mono', monospace" }
const TICK = { fill: '#555560', fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }

const fmtDate = (ts: number) => new Date(ts * 1000).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
const fmtShort = (ts: number) => { const d = new Date(ts * 1000); return `${d.getUTCMonth()+1}/${d.getUTCDate()}` }
const fmtRes = (s: number) => s >= 86400 ? `${s/86400}d` : s >= 3600 ? `${s/3600}h` : s >= 60 ? `${s/60}m` : `${s}s`

const HORIZONS = [
  { label: '1W', days: 7 },
  { label: '1M', days: 30 },
  { label: '3M', days: 90 },
  { label: '6M', days: 180 },
  { label: '1Y', days: 365 },
  { label: 'ALL', days: 0 },
]

const RESOLUTIONS = [
  { label: '1m', value: 60 },
  { label: '5m', value: 300 },
  { label: '1h', value: 3600 },
  { label: '4h', value: 14400 },
  { label: '1d', value: 86400 },
]

function downsample(data: any[], max: number) {
  if (data.length <= max) return data
  const step = Math.ceil(data.length / max)
  return data.filter((_, i) => i % step === 0 || i === data.length - 1)
}

export default function DataExplorer() {
  const [markets, setMarkets] = useState<MarketInfo[]>([])
  const [market, setMarket] = useState('SOL-PERP')
  const [resolution, setResolution] = useState(3600)
  const [horizon, setHorizon] = useState(90)
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [candles, setCandles] = useState<CandleData[]>([])
  const [loading, setLoading] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [loadStatus, setLoadStatus] = useState('')  // status message during load
  const [inventoryLoading, setInventoryLoading] = useState(true)
  const [fundingRates, setFundingRates] = useState<any[]>([])
  const [activeTab, setActiveTab] = useState<'price' | 'funding'>('price')
  // Indicators
  const [showSMA, setShowSMA] = useState(false)
  const [smaPeriod, setSmaPeriod] = useState(20)
  const [showEMA, setShowEMA] = useState(false)
  const [emaPeriod, setEmaPeriod] = useState(12)
  const [showVWAP, setShowVWAP] = useState(false)
  const [showBB, setShowBB] = useState(false)
  const [bbPeriod, setBbPeriod] = useState(20)
  const [showRSI, setShowRSI] = useState(false)
  const [rsiPeriod, setRsiPeriod] = useState(14)
  const [showVolume, setShowVolume] = useState(true)

  const refreshInventory = useCallback(() => {
    setInventoryLoading(true)
    fetch('/api/v1/data/markets')
      .then(r => r.json())
      .then(d => setMarkets(d.markets || []))
      .catch(() => {})
      .finally(() => setInventoryLoading(false))
  }, [])

  useEffect(() => { refreshInventory() }, [refreshInventory])

  const loadData = useCallback(async () => {
    setLoading(true)
    const now = Math.floor(Date.now() / 1000)

    let startTs = 0
    let endTs = now
    if (startDate && endDate) {
      startTs = Math.floor(new Date(startDate + 'T00:00:00Z').getTime() / 1000)
      endTs = Math.floor(new Date(endDate + 'T23:59:59Z').getTime() / 1000)
    } else if (horizon > 0) {
      startTs = now - horizon * 86400
    }

    const limit = Math.min(
      startTs > 0 ? Math.ceil((endTs - startTs) / resolution) : 10000,
      10000
    )

    // Step 1: Check local data first
    setLoadStatus('Checking local data...')
    let loaded: CandleData[] = []
    try {
      const params = new URLSearchParams({
        market, resolution_s: String(resolution), limit: String(Math.floor(limit)),
        ...(startTs > 0 ? { start_ts: String(startTs) } : {}),
        ...(endTs < now ? { end_ts: String(endTs) } : {}),
      })
      const res = await fetch(`/api/v1/data/ohlcv?${params}`)
      const data = await res.json()
      loaded = data.candles || []
    } catch {}

    // Step 2: Check if local data covers the range
    // If data doesn't reach within 1 day of endTs, we need to download
    const needsDownload = loaded.length === 0
      || (startTs > 0 && loaded.length > 0 && loaded[loaded.length - 1].ts < endTs - 86400)

    if (needsDownload) {
      const dlStart = startTs > 0 ? startTs : now - 90 * 86400

      setLoadStatus(`Downloading ${market} from Drift — this may take a moment...`)

      // Use backtest route to trigger download + cache
      try {
        const runRes = await fetch('/api/v1/backtest/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            strategy: 'ma_crossover', market, resolution_s: resolution,
            start_ts: dlStart, end_ts: endTs,
            initial_capital: 1000, fee_rate: 0,
          }),
        })
        const runData = await runRes.json()
        const runId = runData.id

        if (runId) {
          for (let i = 0; i < 240; i++) {
            await new Promise(r => setTimeout(r, 500))
            try {
              const pr = await fetch(`/api/v1/backtest/${runId}/results`)
              const pd = await pr.json()
              const detail = pd.progress?.detail || ''
              const phase = pd.progress?.phase || ''
              void pd.progress?.pct

              if (phase === 'download' || phase === 's3') {
                setLoadStatus(`Downloading ${market}... ${detail}`)
              } else if (phase === 'cached') {
                setLoadStatus(`Downloaded — loading chart...`)
              } else if (phase === 'backtest' || phase === 'tearsheet') {
                setLoadStatus(`Data cached — finalizing...`)
              }

              if (pd.status === 'complete' || pd.status === 'failed') break
            } catch { break }
          }
        }
      } catch {}

      // Re-query with fresh cached data
      try {
        const params = new URLSearchParams({
          market, resolution_s: String(resolution), limit: String(Math.floor(limit)),
          ...(startTs > 0 ? { start_ts: String(startTs) } : {}),
        })
        const res = await fetch(`/api/v1/data/ohlcv?${params}`)
        const data = await res.json()
        loaded = data.candles || []
      } catch {}
    }

    // Step 3: If horizon query still empty, load all available
    if (loaded.length === 0 && !startDate && !endDate) {
      setLoadStatus('Loading all available data...')
      try {
        const fb = await fetch(`/api/v1/data/ohlcv?market=${market}&resolution_s=${resolution}&limit=10000`)
        const fbd = await fb.json()
        loaded = fbd.candles || []
      } catch {}
    }

    setCandles(loaded)
    if (loaded.length > 0) {
      setLoadStatus(`${loaded.length.toLocaleString()} candles loaded`)
    } else {
      setLoadStatus('No data available for this market')
    }

    // Load funding rates
    try {
      const fParams = new URLSearchParams({
        market, limit: '500',
        ...(startTs > 0 ? { start_ts: String(startTs) } : {}),
      })
      const fr = await fetch(`/api/v1/data/funding?${fParams}`)
      const fd = await fr.json()
      let fRates = fd.rates || []
      if (fRates.length === 0) {
        const fb = await fetch(`/api/v1/data/funding?market=${market}&limit=500`)
        const fbd = await fb.json()
        fRates = fbd.rates || []
      }
      setFundingRates(fRates)
    } catch { setFundingRates([]) }

    setLoading(false)
    setTimeout(() => setLoadStatus(''), 5000)
  }, [market, resolution, horizon, startDate, endDate])

  // Auto-load on mount and when config changes
  useEffect(() => { loadData() }, [loadData])

  const handleDownload = async () => {
    setDownloading(true)
    try {
      // Trigger a backtest with the current market to force download
      const now = Math.floor(Date.now() / 1000)
      const startTs = now - 365 * 86400
      await fetch('/api/v1/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          strategy: 'ma_crossover', market, resolution_s: 3600,
          start_ts: startTs, end_ts: now, initial_capital: 1000, fee_rate: 0,
        }),
      })
      // Wait a bit for download to complete then refresh
      await new Promise(r => setTimeout(r, 3000))
      refreshInventory()
      loadData()
    } catch {}
    setDownloading(false)
  }

  const uniqueMarkets = Array.from(new Set(markets.map(m => m.market))).sort()
  const totalRecords = markets.reduce((s, m) => s + m.candle_count, 0)
  const selectedInfo = markets.find(m => m.market === market && m.resolution_s === resolution)

  // Stats computed from raw candles (InteractiveChart handles its own indicator rendering)

  const fundingData = downsample(fundingRates.map((r: any) => ({
    ts: r.ts, date: fmtShort(r.ts),
    rate: r.rate * 100, // convert to percentage
    rateBps: r.rate * 10000,
  })), 250)

  const inputClass = 'bg-void border border-border text-terminal text-xs px-2.5 py-2 focus:border-amber/50 focus:outline-none transition-colors'
  const labelClass = 'block text-[10px] text-ghost tracking-[0.15em] mb-1'

  return (
    <div className="space-y-4">
      <div className="flex items-baseline gap-4">
        <h1 className="font-[var(--font-display)] text-2xl text-white/90 italic">Data Explorer</h1>
        <span className="text-[10px] text-ghost tracking-[0.2em]">// MARKET DATA BROWSER</span>
      </div>

      {/* ── controls ── */}
      <div className="border border-border bg-surface/60 p-3">
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className={labelClass}>MARKET</label>
            <select value={market} onChange={e => setMarket(e.target.value)} className={inputClass} style={{ minWidth: 140 }}>
              <optgroup label="Perp Markets">
                {uniqueMarkets.filter(m => m.includes('-PERP')).map(m => <option key={m}>{m}</option>)}
              </optgroup>
              {uniqueMarkets.some(m => !m.includes('-PERP')) && (
                <optgroup label="Spot Markets">
                  {uniqueMarkets.filter(m => !m.includes('-PERP')).map(m => <option key={m}>{m}</option>)}
                </optgroup>
              )}
            </select>
          </div>
          <div>
            <label className={labelClass}>RESOLUTION</label>
            <select value={resolution} onChange={e => setResolution(+e.target.value)} className={inputClass}>
              {RESOLUTIONS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
            </select>
          </div>
          <div>
            <label className={labelClass}>HORIZON</label>
            <div className="flex gap-0.5">
              {HORIZONS.map(h => (
                <button
                  key={h.label}
                  onClick={() => setHorizon(h.days)}
                  className={`px-2.5 py-1.5 text-[10px] tracking-wider border transition-all ${
                    horizon === h.days
                      ? 'border-amber/50 bg-amber-glow text-amber'
                      : 'border-border text-ghost hover:text-terminal'
                  }`}
                >
                  {h.label}
                </button>
              ))}
            </div>
          </div>
          <button
            onClick={loadData}
            disabled={loading}
            className="px-4 py-2 bg-amber text-void text-[11px] font-semibold tracking-[0.15em] hover:bg-amber-dim disabled:bg-border disabled:text-ghost transition-all"
          >
            {loading ? 'LOADING...' : 'LOAD'}
          </button>
          <button
            onClick={handleDownload}
            disabled={downloading}
            className="px-4 py-2 border border-border text-[11px] text-ghost tracking-[0.1em] hover:text-amber hover:border-amber/30 disabled:opacity-40 transition-all"
          >
            {downloading ? 'DOWNLOADING...' : 'DOWNLOAD 1Y'}
          </button>
          {candles.length > 0 && (
            <span className="text-[10px] text-amber/60">{candles.length} candles</span>
          )}
        </div>

        {/* date range + indicators row */}
        <div className="flex flex-wrap gap-3 items-end mt-3 pt-3 border-t border-border/30">
          <div>
            <label className={labelClass}>START</label>
            <input type="date" value={startDate} onChange={e => { setStartDate(e.target.value); setHorizon(0) }}
              className={inputClass} style={{ width: 145 }} />
          </div>
          <div>
            <label className={labelClass}>END</label>
            <input type="date" value={endDate} onChange={e => { setEndDate(e.target.value); setHorizon(0) }}
              className={inputClass} style={{ width: 145 }} />
          </div>
          <div className="border-l border-border/30 pl-3">
            <label className={labelClass}>INDICATORS</label>
            <div className="flex gap-1">
              <button onClick={() => setShowSMA(!showSMA)}
                className={`px-2 py-1 text-[9px] tracking-wider border transition-all ${showSMA ? 'border-gain/50 text-gain bg-gain/10' : 'border-border text-ghost/50 hover:text-terminal'}`}>
                SMA{showSMA && ` ${smaPeriod}`}
              </button>
              <button onClick={() => setShowEMA(!showEMA)}
                className={`px-2 py-1 text-[9px] tracking-wider border transition-all ${showEMA ? 'border-[#8b5cf6]/50 text-[#8b5cf6] bg-[#8b5cf6]/10' : 'border-border text-ghost/50 hover:text-terminal'}`}>
                EMA{showEMA && ` ${emaPeriod}`}
              </button>
              <button onClick={() => setShowVWAP(!showVWAP)}
                className={`px-2 py-1 text-[9px] tracking-wider border transition-all ${showVWAP ? 'border-[#06b6d4]/50 text-[#06b6d4] bg-[#06b6d4]/10' : 'border-border text-ghost/50 hover:text-terminal'}`}>
                VWAP
              </button>
              <button onClick={() => setShowBB(!showBB)}
                className={`px-2 py-1 text-[9px] tracking-wider border transition-all ${showBB ? 'border-amber/50 text-amber bg-amber/10' : 'border-border text-ghost/50 hover:text-terminal'}`}>
                BB{showBB && ` ${bbPeriod}`}
              </button>
              <button onClick={() => setShowRSI(!showRSI)}
                className={`px-2 py-1 text-[9px] tracking-wider border transition-all ${showRSI ? 'border-[#f59e0b]/50 text-[#f59e0b] bg-[#f59e0b]/10' : 'border-border text-ghost/50 hover:text-terminal'}`}>
                RSI{showRSI && ` ${rsiPeriod}`}
              </button>
              <button onClick={() => setShowVolume(!showVolume)}
                className={`px-2 py-1 text-[9px] tracking-wider border transition-all ${showVolume ? 'border-ghost/50 text-ghost bg-ghost/10' : 'border-border text-ghost/50 hover:text-terminal'}`}>
                VOL
              </button>
            </div>
          </div>
          {/* period inputs for active indicators */}
          {(showSMA || showEMA || showBB) && (
            <div className="flex gap-2 items-center text-[9px] text-ghost/50">
              {showSMA && (
                <span>SMA: <input type="number" value={smaPeriod} onChange={e => setSmaPeriod(+e.target.value)}
                  className="bg-void border border-border text-terminal text-[10px] w-10 px-1 py-0.5 text-center" min={2} max={200} /></span>
              )}
              {showEMA && (
                <span>EMA: <input type="number" value={emaPeriod} onChange={e => setEmaPeriod(+e.target.value)}
                  className="bg-void border border-border text-terminal text-[10px] w-10 px-1 py-0.5 text-center" min={2} max={200} /></span>
              )}
              {showBB && (
                <span>BB: <input type="number" value={bbPeriod} onChange={e => setBbPeriod(+e.target.value)}
                  className="bg-void border border-border text-terminal text-[10px] w-10 px-1 py-0.5 text-center" min={5} max={100} /></span>
              )}
              {showRSI && (
                <span>RSI: <input type="number" value={rsiPeriod} onChange={e => setRsiPeriod(+e.target.value)}
                  className="bg-void border border-border text-terminal text-[10px] w-10 px-1 py-0.5 text-center" min={5} max={30} /></span>
              )}
            </div>
          )}
        </div>

        {selectedInfo && (
          <div className="mt-2 text-[9px] text-ghost/40 tracking-wider">
            DB: {selectedInfo.candle_count.toLocaleString()} candles, {fmtDate(selectedInfo.first_ts)} — {fmtDate(selectedInfo.last_ts)}
          </div>
        )}
      </div>

      {/* ── loading status ── */}
      {loading && (
        <div className="border border-amber/30 bg-amber/5 px-4 py-3">
          <div className="flex items-center gap-3">
            <div className="w-4 h-4 border-2 border-amber/50 border-t-amber rounded-full animate-spin" />
            <span className="text-[11px] text-amber/80 tracking-wider">{loadStatus || 'Loading...'}</span>
          </div>
        </div>
      )}

      {!loading && loadStatus && candles.length > 0 && (
        <div className="text-[10px] text-gain/60 tracking-wider px-1">{loadStatus}</div>
      )}

      {!loading && candles.length === 0 && loadStatus && (
        <div className="border border-border/50 py-8 text-center">
          <div className="text-ghost/40 text-xs tracking-wider">{loadStatus}</div>
          <div className="text-ghost/20 text-[10px] mt-2">Try a different market or click DOWNLOAD 1Y</div>
        </div>
      )}

      {/* ── tab selector ── */}
      {candles.length > 0 && (
        <div className="flex gap-0.5">
          <button onClick={() => setActiveTab('price')}
            className={`px-3 py-1.5 text-[10px] tracking-[0.12em] border transition-all ${
              activeTab === 'price' ? 'border-amber/50 bg-amber-glow text-amber' : 'border-border text-ghost hover:text-terminal'
            }`}>PRICE</button>
          <button onClick={() => setActiveTab('funding')}
            className={`px-3 py-1.5 text-[10px] tracking-[0.12em] border transition-all ${
              activeTab === 'funding' ? 'border-amber/50 bg-amber-glow text-amber' : 'border-border text-ghost hover:text-terminal'
            }`}>FUNDING</button>
        </div>
      )}

      {/* ── interactive price chart ── */}
      {activeTab === 'price' && candles.length > 0 && (
        <div style={{ animation: 'fadeUp 0.3s ease' }}>
          <InteractiveChart
            candles={candles}
            height={500}
            indicators={{
              sma: showSMA, smaPeriod,
              ema: showEMA, emaPeriod,
              vwap: showVWAP,
              bb: showBB, bbPeriod,
              rsi: showRSI, rsiPeriod,
              volume: showVolume,
            }}
          />

          {/* stats bar */}
          <div className="mt-4 grid grid-cols-4 gap-3">
            {[
              { label: 'CURRENT', value: `$${candles[candles.length-1]?.close.toFixed(2)}` },
              { label: 'HIGH', value: `$${Math.max(...candles.map(d => d.high)).toFixed(2)}` },
              { label: 'LOW', value: `$${Math.min(...candles.map(d => d.low)).toFixed(2)}` },
              { label: 'AVG VOL', value: (candles.reduce((s,d) => s + d.volume, 0) / candles.length).toFixed(0) },
            ].map(s => (
              <div key={s.label} className="border border-border bg-surface/60 p-3">
                <div className="text-[8px] text-ghost/50 tracking-wider">{s.label}</div>
                <div className="text-sm text-terminal font-mono">{s.value}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── funding charts ── */}
      {activeTab === 'funding' && (
        <div style={{ animation: 'fadeUp 0.3s ease' }}>
          {fundingData.length > 0 ? (
            <>
              <div className="border border-border bg-surface/60 p-3">
                <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">FUNDING RATE (bps)</div>
                <ResponsiveContainer width="100%" height={280}>
                  <AreaChart data={fundingData}>
                    <CartesianGrid strokeDasharray="2 6" stroke="#1a1a1f" />
                    <XAxis dataKey="date" tick={TICK} minTickGap={50} interval="preserveStartEnd" />
                    <YAxis tick={TICK} width={50} />
                    <Tooltip contentStyle={tooltipStyle} formatter={(v: any) => [`${Number(v).toFixed(2)} bps`, 'Rate']} />
                    <Area type="monotone" dataKey="rateBps" stroke="#e8a849" fill="#e8a849" fillOpacity={0.08}
                      strokeWidth={1} isAnimationActive={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>

              <div className="mt-4 grid grid-cols-3 gap-3">
                {[
                  { label: 'CURRENT', value: `${fundingData[fundingData.length-1]?.rateBps.toFixed(2)} bps` },
                  { label: 'AVG', value: `${(fundingData.reduce((s,d) => s + d.rateBps, 0) / fundingData.length).toFixed(2)} bps` },
                  { label: 'ANNUALIZED', value: `${((fundingData.reduce((s,d) => s + d.rateBps, 0) / fundingData.length) * 8760 / 100).toFixed(1)}%` },
                ].map(s => (
                  <div key={s.label} className="border border-border bg-surface/60 p-3">
                    <div className="text-[8px] text-ghost/50 tracking-wider">{s.label}</div>
                    <div className={`text-sm font-mono ${s.label === 'ANNUALIZED' ? 'text-amber' : 'text-terminal'}`}>{s.value}</div>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="border border-border/50 py-12 text-center">
              <div className="text-ghost/30 text-xs tracking-[0.3em]">NO FUNDING DATA</div>
              <div className="text-ghost/20 text-[10px] mt-2">Funding rates are collected by the data collector for perp markets</div>
            </div>
          )}
        </div>
      )}

      {/* ── inventory table ── */}
      <div className="border border-border bg-surface/60">
        <div className="px-4 py-2.5 border-b border-border flex items-center gap-2">
          <span className="w-2 h-2 bg-amber/60" />
          <span className="text-[10px] text-ghost tracking-[0.2em]">DATA.INVENTORY</span>
          <span className="ml-auto text-[10px] text-ghost/40">
            {uniqueMarkets.length} markets &middot; {totalRecords.toLocaleString()} records
          </span>
        </div>
        {inventoryLoading ? (
          <div className="p-6 text-center text-[11px] text-ghost/40">SCANNING...</div>
        ) : markets.length === 0 ? (
          <div className="p-6 text-center text-ghost/30 text-xs">No data — click DOWNLOAD 1Y or run <code>flint init</code></div>
        ) : (
          <div className="overflow-x-auto max-h-[350px] overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-surface">
                <tr className="text-left text-ghost/40 border-b border-border text-[9px] tracking-[0.15em]">
                  <th className="py-2 px-4">MARKET</th>
                  <th className="py-2 px-4">RES</th>
                  <th className="py-2 px-4 text-right">CANDLES</th>
                  <th className="py-2 px-4">FROM</th>
                  <th className="py-2 px-4">TO</th>
                </tr>
              </thead>
              <tbody>
                {markets.map((m, i) => (
                  <tr key={i}
                    className={`border-b border-border/20 hover:bg-amber-glow/50 cursor-pointer ${market === m.market ? 'bg-amber-glow/30' : ''}`}
                    onClick={() => { setMarket(m.market); setResolution(m.resolution_s) }}
                  >
                    <td className="py-1.5 px-4 text-amber/80 font-medium">{m.market}</td>
                    <td className="py-1.5 px-4 text-ghost/50">{fmtRes(m.resolution_s)}</td>
                    <td className="py-1.5 px-4 text-right text-white/60 tabular-nums">{m.candle_count.toLocaleString()}</td>
                    <td className="py-1.5 px-4 text-ghost/40 text-[10px]">{fmtDate(m.first_ts)}</td>
                    <td className="py-1.5 px-4 text-ghost/40 text-[10px]">{fmtDate(m.last_ts)}</td>
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
