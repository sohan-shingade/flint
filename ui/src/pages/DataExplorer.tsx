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

const MARKET_PACKS = {
  starter: {
    label: 'Starter Pack',
    description: '5 perps — fast download (~2 min)',
    markets: ['SOL-PERP', 'BTC-PERP', 'ETH-PERP', 'WIF-PERP', 'JUP-PERP'],
  },
  defi: {
    label: 'DeFi Pack',
    description: '10 perps + 5 spot — moderate (~5 min)',
    markets: ['SOL-PERP', 'BTC-PERP', 'ETH-PERP', 'JUP-PERP', 'DRIFT-PERP', 'PYTH-PERP', 'RENDER-PERP', 'WIF-PERP', 'POPCAT-PERP', 'JTO-PERP', 'SOL', 'BTC', 'ETH', 'JUP', 'DRIFT'],
  },
  advanced: {
    label: 'Advanced Pack',
    description: '20 perps + 10 spot — longer (~10 min)',
    markets: [
      'SOL-PERP', 'BTC-PERP', 'ETH-PERP', 'WIF-PERP', 'JUP-PERP', 'DRIFT-PERP', 'PYTH-PERP',
      'RENDER-PERP', 'POPCAT-PERP', 'JTO-PERP', 'SUI-PERP', 'ARB-PERP', 'LINK-PERP', 'DOGE-PERP',
      'AVAX-PERP', 'XRP-PERP', 'INJ-PERP', 'TIA-PERP', 'SEI-PERP', 'OP-PERP',
      'SOL', 'BTC', 'ETH', 'JUP', 'DRIFT', 'PYTH', 'WIF', 'JTO', 'BONK', 'POPCAT',
    ],
  },
  all: {
    label: 'Everything',
    description: 'All 57 Drift markets (36 perp + 21 spot) — ~20 min',
    markets: [] as string[],  // filled from API
  },
}

const DOWNLOAD_RANGES: Record<string, {label: string, days: number}> = {
  '1m': { label: '1 Month', days: 30 },
  '3m': { label: '3 Months', days: 90 },
  '6m': { label: '6 Months', days: 180 },
  '1y': { label: '1 Year', days: 365 },
  'custom': { label: 'Custom', days: 0 },
}

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
  const [loadStatus, setLoadStatus] = useState('')  // status message during load
  const [inventoryLoading, setInventoryLoading] = useState(true)
  const [fundingRates, setFundingRates] = useState<any[]>([])
  const [activeTab, setActiveTab] = useState<'price' | 'funding'>('price')
  // Add Markets panel
  const [showAddMarkets, setShowAddMarkets] = useState(false)
  const [availableForDownload, setAvailableForDownload] = useState<{market: string, source: string, type: string}[]>([])
  const [selectedDownloads, setSelectedDownloads] = useState<string[]>([])
  const [downloadRange, setDownloadRange] = useState('3m')  // preset
  const [dlStartDate, setDlStartDate] = useState('')
  const [dlEndDate, setDlEndDate] = useState('')
  const [downloadProgress, setDownloadProgress] = useState<{market: string, status: string}[]>([])
  const [isDownloading, setIsDownloading] = useState(false)
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

  useEffect(() => {
    fetch('/api/v1/data/available-markets')
      .then(r => r.json())
      .then(d => {
        const mkts = d.markets || []
        setAvailableForDownload(mkts)
        MARKET_PACKS.all.markets = mkts.map((m: any) => m.market)
      })
      .catch(() => {})
  }, [])

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

  const handleBulkDownload = async () => {
    if (selectedDownloads.length === 0) return
    setIsDownloading(true)

    const now = Math.floor(Date.now() / 1000)
    let startTs: number, endTs: number

    if (downloadRange === 'custom' && dlStartDate && dlEndDate) {
      startTs = Math.floor(new Date(dlStartDate + 'T00:00:00Z').getTime() / 1000)
      endTs = Math.floor(new Date(dlEndDate + 'T23:59:59Z').getTime() / 1000)
    } else {
      const days = DOWNLOAD_RANGES[downloadRange]?.days || 90
      endTs = now
      startTs = now - days * 86400
    }

    const progress: {market: string, status: string}[] = selectedDownloads.map(m => ({ market: m, status: 'pending' }))
    setDownloadProgress([...progress])

    for (let i = 0; i < selectedDownloads.length; i++) {
      const mkt = selectedDownloads[i]
      progress[i].status = 'downloading...'
      setDownloadProgress([...progress])

      try {
        const res = await fetch('/api/v1/data/download', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ market: mkt, resolution_s: 3600, start_ts: startTs, end_ts: endTs }),
        })
        const data = await res.json()
        if (data.downloaded > 0 || data.existing > 0) {
          progress[i].status = `${(data.downloaded + data.existing).toLocaleString()} candles`
        } else {
          progress[i].status = 'no data found'
        }
      } catch {
        progress[i].status = 'failed'
      }
      setDownloadProgress([...progress])
    }

    setIsDownloading(false)
    refreshInventory()
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
            onClick={() => setShowAddMarkets(!showAddMarkets)}
            className={`px-4 py-2 border text-[11px] tracking-[0.1em] transition-all ${
              showAddMarkets
                ? 'border-amber/50 text-amber bg-amber-glow'
                : 'border-border text-ghost hover:text-amber hover:border-amber/30'
            }`}
          >
            {showAddMarkets ? 'HIDE' : 'ADD MARKETS'}
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

      {/* ── add markets panel ── */}
      {showAddMarkets && (
        <div className="border border-amber/20 bg-surface/80 backdrop-blur" style={{ animation: 'fadeUp 0.2s ease' }}>
          <div className="px-4 py-2.5 border-b border-border flex items-center gap-2">
            <span className="w-2 h-2 bg-amber/60" />
            <span className="text-[10px] text-amber tracking-[0.2em]">ADD.MARKETS</span>
            <span className="ml-auto text-[10px] text-ghost/40">Select markets and time range to download</span>
          </div>

          <div className="p-4 space-y-4">
            {/* Presets */}
            <div>
              <div className="text-[10px] text-ghost tracking-[0.15em] mb-2">PRESETS</div>
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
                {Object.entries(MARKET_PACKS).map(([key, pack]) => (
                  <button
                    key={key}
                    onClick={() => setSelectedDownloads(pack.markets.length > 0 ? pack.markets : availableForDownload.map(m => m.market))}
                    className="border border-border hover:border-amber/30 p-3 text-left transition-all group"
                  >
                    <div className="text-[11px] text-white/80 group-hover:text-amber font-medium">{pack.label}</div>
                    <div className="text-[9px] text-ghost/50 mt-0.5">{pack.description}</div>
                    <div className="text-[9px] text-amber/40 mt-1">{pack.markets.length || availableForDownload.length} markets</div>
                  </button>
                ))}
              </div>
            </div>

            {/* Time range */}
            <div className="flex flex-wrap items-end gap-3">
              <div>
                <div className="text-[10px] text-ghost tracking-[0.15em] mb-2">TIME RANGE</div>
                <div className="flex gap-0.5">
                  {Object.entries(DOWNLOAD_RANGES).map(([key, r]) => (
                    <button
                      key={key}
                      onClick={() => setDownloadRange(key)}
                      className={`px-2.5 py-1.5 text-[10px] tracking-wider border transition-all ${
                        downloadRange === key
                          ? 'border-amber/50 bg-amber-glow text-amber'
                          : 'border-border text-ghost hover:text-terminal'
                      }`}
                    >
                      {r.label}
                    </button>
                  ))}
                </div>
              </div>
              {downloadRange === 'custom' && (
                <>
                  <div>
                    <label className="block text-[9px] text-ghost tracking-wider mb-1">FROM</label>
                    <input type="date" value={dlStartDate} onChange={e => setDlStartDate(e.target.value)}
                      className="bg-void border border-border text-terminal text-xs px-2.5 py-1.5" />
                  </div>
                  <div>
                    <label className="block text-[9px] text-ghost tracking-wider mb-1">TO</label>
                    <input type="date" value={dlEndDate} onChange={e => setDlEndDate(e.target.value)}
                      className="bg-void border border-border text-terminal text-xs px-2.5 py-1.5" />
                  </div>
                </>
              )}
            </div>

            {/* Market grid — show available markets as checkboxes */}
            <div>
              <div className="flex items-center gap-3 mb-2">
                <span className="text-[10px] text-ghost tracking-[0.15em]">MARKETS</span>
                <span className="text-[9px] text-ghost/40">{selectedDownloads.length} selected</span>
                <button onClick={() => setSelectedDownloads([])} className="text-[9px] text-ghost/40 hover:text-terminal ml-auto">Clear all</button>
              </div>
              <div className="grid grid-cols-4 sm:grid-cols-6 lg:grid-cols-8 gap-1 max-h-[180px] overflow-y-auto">
                {availableForDownload.map(m => {
                  const isSelected = selectedDownloads.includes(m.market)
                  const isDownloaded = uniqueMarkets.includes(m.market)
                  return (
                    <button
                      key={m.market}
                      onClick={() => {
                        if (isSelected) {
                          setSelectedDownloads(prev => prev.filter(x => x !== m.market))
                        } else {
                          setSelectedDownloads(prev => [...prev, m.market])
                        }
                      }}
                      className={`px-2 py-1.5 text-[9px] tracking-wider border transition-all text-left ${
                        isSelected
                          ? 'border-amber/50 bg-amber-glow text-amber'
                          : isDownloaded
                            ? 'border-gain/20 text-gain/60'
                            : 'border-border text-ghost/50 hover:text-terminal hover:border-border-bright'
                      }`}
                    >
                      {m.market.replace('-PERP', '')}
                      {isDownloaded && <span className="text-gain/40 ml-1">&#10003;</span>}
                    </button>
                  )
                })}
              </div>
            </div>

            {/* Download button + progress */}
            <div className="flex items-center gap-3 pt-2 border-t border-border/30">
              <button
                onClick={handleBulkDownload}
                disabled={isDownloading || selectedDownloads.length === 0}
                className="px-5 py-2 bg-amber text-void text-[11px] font-semibold tracking-[0.15em] hover:bg-amber-dim disabled:bg-border disabled:text-ghost transition-all"
              >
                {isDownloading ? 'DOWNLOADING...' : `DOWNLOAD ${selectedDownloads.length} MARKET${selectedDownloads.length !== 1 ? 'S' : ''}`}
              </button>
              {selectedDownloads.length > 10 && !isDownloading && (
                <span className="text-[9px] text-loss/60">This may take several minutes</span>
              )}
              {selectedDownloads.length > 20 && !isDownloading && (
                <span className="text-[9px] text-loss/60">Large download — grab a coffee</span>
              )}
            </div>

            {/* Download progress */}
            {downloadProgress.length > 0 && (
              <div className="max-h-[200px] overflow-y-auto">
                <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-1">
                  {downloadProgress.map(p => (
                    <div key={p.market} className={`px-2 py-1 text-[9px] border ${
                      p.status === 'pending' ? 'border-border text-ghost/30' :
                      p.status.includes('downloading') ? 'border-amber/30 text-amber animate-pulse' :
                      p.status === 'failed' || p.status === 'no data found' ? 'border-loss/30 text-loss/60' :
                      'border-gain/30 text-gain/60'
                    }`}>
                      <div className="font-medium">{p.market.replace('-PERP', '')}</div>
                      <div className="text-[8px] truncate">{p.status}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

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
          <div className="text-ghost/20 text-[10px] mt-2">Try a different market or click ADD MARKETS</div>
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
          <div className="p-6 text-center text-ghost/30 text-xs">No data — click ADD MARKETS or run <code>flint init</code></div>
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
