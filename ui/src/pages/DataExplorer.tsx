import { useEffect, useState, useCallback, useMemo } from 'react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, ReferenceLine } from 'recharts'
import InteractiveChart from '../components/InteractiveChart'

interface CandleData { ts: number; open: number; high: number; low: number; close: number; volume: number }
interface MarketInfo { market: string; resolution_s: number; candle_count: number; first_ts: number; last_ts: number }

const tooltipStyle = { background: '#111114', border: '1px solid #2a2a32', borderRadius: 0, fontSize: 11, fontFamily: "'JetBrains Mono', monospace" }
const TICK = { fill: '#555560', fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }

const fmtDate = (ts: number) => new Date(ts * 1000).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
const fmtShort = (ts: number) => { const d = new Date(ts * 1000); return `${d.getUTCMonth()+1}/${d.getUTCDate()}` }
const fmtHour = (ts: number) => { const d = new Date(ts * 1000); return `${d.getUTCMonth()+1}/${d.getUTCDate()} ${String(d.getUTCHours()).padStart(2,'0')}:00` }
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
  const [fundingByVenue, setFundingByVenue] = useState<Record<string, {ts: number, rate: number}[]>>({})
  const [activeTab, setActiveTab] = useState<'price' | 'funding'>('price')
  // Add Markets panel
  const [showAddMarkets, setShowAddMarkets] = useState(false)
  const [availableForDownload, setAvailableForDownload] = useState<{market: string, source: string, type: string}[]>([])
  const [selectedDownloads, setSelectedDownloads] = useState<string[]>([])
  const [downloadRange, setDownloadRange] = useState('3m')  // preset
  const [dlStartDate, setDlStartDate] = useState('')
  const [dlEndDate, setDlEndDate] = useState('')
  const [downloadProgress, setDownloadProgress] = useState<{market: string, status: string}[]>([])
  const [downloadWarnings, setDownloadWarnings] = useState<string[]>([])
  const [isDownloading, setIsDownloading] = useState(false)
  // Candle venue selection for downloads (multi-select)
  const CANDLE_VENUES = [
    { id: 'drift',        label: 'Drift',        color: '#e8a849' },
    { id: 'hyperliquid',  label: 'Hyperliquid',  color: '#22d3ee' },
    { id: 'binance',      label: 'Binance',       color: '#f0b90b' },
    { id: 'okx',          label: 'OKX',           color: '#a78bfa' },
    { id: 'bybit',        label: 'Bybit',         color: '#57c84d' },
  ]
  const DEFAULT_CANDLE_VENUES = ['drift']
  const [selectedCandleVenues, setSelectedCandleVenues] = useState<string[]>(DEFAULT_CANDLE_VENUES)
  // Funding venue selection for downloads
  const ALL_VENUES = [
    { id: 'drift',       label: 'Drift',       freq: '1h',  color: '#e8a849' },
    { id: 'hyperliquid', label: 'Hyperliquid',  freq: '1h',  color: '#22d3ee' },
    { id: 'dydx',        label: 'dYdX',         freq: '1h',  color: '#818cf8' },
    { id: 'okx',         label: 'OKX',          freq: '8h',  color: '#a78bfa' },
    { id: 'bybit',       label: 'Bybit',        freq: '8h',  color: '#57c84d' },
    { id: 'gateio',      label: 'Gate.io',      freq: '8h',  color: '#f472b6' },
    { id: 'bitget',      label: 'Bitget',       freq: '8h',  color: '#fb923c' },
    { id: 'mexc',        label: 'MEXC',         freq: '8h',  color: '#34d399' },
    { id: 'phemex',      label: 'Phemex',       freq: '8h',  color: '#f87171' },
    { id: 'bitmex',      label: 'BitMEX',       freq: '8h',  color: '#fbbf24' },
    { id: 'jupiter',     label: 'Jupiter',      freq: 'borrow', color: '#c4b5fd' },
  ]
  const DEFAULT_VENUES = ['drift', 'hyperliquid', 'okx', 'dydx', 'gateio', 'bitget', 'jupiter']
  const [selectedVenues, setSelectedVenues] = useState<string[]>(DEFAULT_VENUES)
  // Multi-venue price visualization
  const VENUE_COLORS: Record<string, string> = {
    default: '#e8a849',
    drift: '#e8a849',
    hyperliquid: '#22d3ee',
    binance: '#a78bfa',
    okx: '#57c84d',
    bybit: '#f472b6',
  }
  const [priceVenues, setPriceVenues] = useState<string[]>(['default'])
  const [priceViewMode, setPriceViewMode] = useState<'overlay' | 'separate'>('overlay')
  const [venueCandles, setVenueCandles] = useState<Record<string, CandleData[]>>({})

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
  const [showFunding, setShowFunding] = useState(false)

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
    setLoadStatus('Loading...')

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

    // Query local candle data (no downloading — that's done via ADD MARKETS)
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

    // If no data in requested range, try loading all available data for this market
    if (loaded.length === 0) {
      try {
        const res = await fetch(`/api/v1/data/ohlcv?market=${market}&resolution_s=${resolution}&limit=10000`)
        const data = await res.json()
        loaded = data.candles || []
      } catch {}
    }

    // Only update candles state if data actually changed (prevents chart remount + scroll jump)
    setCandles(prev => {
      if (prev.length === loaded.length && loaded.length > 0 &&
          prev[0]?.ts === loaded[0]?.ts && prev[prev.length - 1]?.ts === loaded[loaded.length - 1]?.ts) {
        return prev
      }
      return loaded
    })

    // Fetch per-venue candles if multiple venues selected
    if (priceVenues.length > 0 && priceVenues[0] !== 'default') {
      const venueData: Record<string, CandleData[]> = {}
      for (const v of priceVenues) {
        try {
          const vParams = new URLSearchParams({
            market, resolution_s: String(resolution), limit: String(Math.floor(limit)),
            venue: v,
            ...(startTs > 0 ? { start_ts: String(startTs) } : {}),
            ...(endTs < now ? { end_ts: String(endTs) } : {}),
          })
          const vRes = await fetch(`/api/v1/data/ohlcv?${vParams}`)
          const vData = await vRes.json()
          if (vData.candles?.length > 0) {
            venueData[v] = vData.candles
          }
        } catch {}
      }
      setVenueCandles(venueData)
    } else {
      setVenueCandles({})
    }

    // Query local funding data (grouped by venue)
    if (market.includes('-PERP') && loaded.length > 0) {
      try {
        const candleStart = loaded[0].ts
        const candleEnd = loaded[loaded.length - 1].ts
        const fParams = new URLSearchParams({
          market,
          start_ts: String(candleStart),
          end_ts: String(candleEnd),
        })
        const fr = await fetch(`/api/v1/data/funding?${fParams}`)
        const fd = await fr.json()
        const venues = fd.venues || {}
        const parsed: Record<string, {ts: number, rate: number}[]> = {}
        for (const [v, rates] of Object.entries(venues) as any) {
          parsed[v] = (rates || []).map((r: any) => ({ ts: r.ts, rate: r.rate }))
        }
        setFundingByVenue(parsed)
      } catch { setFundingByVenue({}) }
    } else {
      setFundingByVenue({})
    }

    if (loaded.length > 0) {
      setLoadStatus(`${loaded.length.toLocaleString()} candles`)
    } else {
      setLoadStatus('No data — download this market from ADD MARKETS')
    }

    setLoading(false)
  }, [market, resolution, horizon, startDate, endDate, priceVenues, priceViewMode])

  // Load data on mount and when market/settings change
  useEffect(() => { loadData() }, [loadData])

  const handleBulkDownload = async () => {
    if (selectedDownloads.length === 0) return
    setIsDownloading(true)
    setDownloadWarnings([])

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

    // Determine which candle venues to download; fall back to default (auto) if none selected
    const candleVenuesToFetch = selectedCandleVenues.length > 0 ? selectedCandleVenues : ['default']
    const allWarnings: string[] = []

    for (let i = 0; i < selectedDownloads.length; i++) {
      const mkt = selectedDownloads[i]
      progress[i].status = 'downloading...'
      setDownloadProgress([...progress])

      let totalCandles = 0
      let totalFunding = 0
      let anyError = false

      for (const cv of candleVenuesToFetch) {
        try {
          const res = await fetch('/api/v1/data/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              market: mkt,
              resolution_s: 3600,
              start_ts: startTs,
              end_ts: endTs,
              funding_venues: selectedVenues,
              venue: cv,
            }),
          })
          const data = await res.json()
          totalCandles += (data.downloaded || 0) + (data.existing || 0)
          totalFunding += data.funding_fetched || 0
          if (data.warnings && data.warnings.length > 0) {
            for (const w of data.warnings) {
              allWarnings.push(`[${mkt}] ${w}`)
            }
            setDownloadWarnings([...allWarnings])
          }
        } catch {
          anyError = true
        }
      }

      if (anyError && totalCandles === 0) {
        progress[i].status = 'failed'
      } else if (totalCandles === 0) {
        progress[i].status = 'no data found'
      } else {
        const fundingInfo = totalFunding > 0 ? ` + ${totalFunding} funding` : ''
        progress[i].status = `${totalCandles.toLocaleString()} candles${fundingInfo}`
      }
      setDownloadProgress([...progress])
    }

    setIsDownloading(false)
    refreshInventory()
    // Reload chart data (candles + funding) for the currently selected market
    loadData()
  }

  const uniqueMarkets = Array.from(new Set(markets.map(m => m.market))).sort()
  const totalRecords = markets.reduce((s, m) => s + m.candle_count, 0)
  const selectedInfo = markets.find(m => m.market === market && m.resolution_s === resolution)

  // Memoize chart props to prevent chart recreation on unrelated re-renders (e.g. clock tick)
  const chartIndicators = useMemo(() => ({
    sma: showSMA, smaPeriod,
    ema: showEMA, emaPeriod,
    vwap: showVWAP,
    bb: showBB, bbPeriod,
    rsi: showRSI, rsiPeriod,
    volume: showVolume,
    funding: showFunding,
  }), [showSMA, smaPeriod, showEMA, emaPeriod, showVWAP, showBB, bbPeriod, showRSI, rsiPeriod, showVolume, showFunding])

  const chartFundingRates = useMemo(() => {
    if (!showFunding) return []
    return Object.entries(fundingByVenue).flatMap(([venue, rates]) =>
      rates.map(r => ({ ts: r.ts, rate: r.rate, source: venue }))
    )
  }, [showFunding, fundingByVenue])

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
              <button onClick={() => setShowFunding(!showFunding)}
                className={`px-2 py-1 text-[9px] tracking-wider border transition-all ${showFunding ? 'border-amber/50 text-amber bg-amber/10' : 'border-border text-ghost/50 hover:text-terminal'}`}>
                FUND
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

            {/* Candle source venues (multi-select) */}
            <div>
              <div className="flex items-center gap-3 mb-2">
                <span className="text-[10px] text-ghost tracking-[0.15em]">CANDLE SOURCE</span>
                <span className="text-[9px] text-ghost/40">{selectedCandleVenues.length} selected — OHLCV venue(s) to download candle data from</span>
                <button onClick={() => setSelectedCandleVenues(CANDLE_VENUES.map(v => v.id))}
                  className="text-[9px] text-ghost/40 hover:text-amber ml-auto">All</button>
                <button onClick={() => setSelectedCandleVenues(DEFAULT_CANDLE_VENUES)}
                  className="text-[9px] text-ghost/40 hover:text-amber">Default</button>
                <button onClick={() => setSelectedCandleVenues([])}
                  className="text-[9px] text-ghost/40 hover:text-terminal">None</button>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {CANDLE_VENUES.map(v => {
                  const sel = selectedCandleVenues.includes(v.id)
                  return (
                    <button key={v.id}
                      onClick={() => setSelectedCandleVenues(prev =>
                        prev.includes(v.id) ? prev.filter(x => x !== v.id) : [...prev, v.id]
                      )}
                      className={`px-2.5 py-1.5 text-[9px] tracking-wider border transition-all flex items-center gap-1.5 ${
                        sel ? 'text-white' : 'border-border text-ghost/40 hover:text-terminal'
                      }`}
                      style={sel ? { borderColor: v.color + '66', background: v.color + '15', color: v.color } : {}}
                    >
                      <span className="w-1.5 h-1.5 rounded-full" style={{ background: sel ? v.color : '#555' }} />
                      {v.label}
                    </button>
                  )
                })}
              </div>
            </div>

            {/* Funding venues */}
            <div>
              <div className="flex items-center gap-3 mb-2">
                <span className="text-[10px] text-ghost tracking-[0.15em]">FUNDING VENUES</span>
                <span className="text-[9px] text-ghost/40">{selectedVenues.length} selected — funding rates will be downloaded for perp markets</span>
                <button onClick={() => setSelectedVenues(ALL_VENUES.map(v => v.id))}
                  className="text-[9px] text-ghost/40 hover:text-amber ml-auto">All</button>
                <button onClick={() => setSelectedVenues(DEFAULT_VENUES)}
                  className="text-[9px] text-ghost/40 hover:text-amber">Defaults</button>
                <button onClick={() => setSelectedVenues([])}
                  className="text-[9px] text-ghost/40 hover:text-terminal">None</button>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {ALL_VENUES.map(v => {
                  const sel = selectedVenues.includes(v.id)
                  return (
                    <button key={v.id}
                      onClick={() => setSelectedVenues(prev =>
                        prev.includes(v.id) ? prev.filter(x => x !== v.id) : [...prev, v.id]
                      )}
                      className={`px-2.5 py-1.5 text-[9px] tracking-wider border transition-all flex items-center gap-1.5 ${
                        sel ? 'text-white' : 'border-border text-ghost/40 hover:text-terminal'
                      }`}
                      style={sel ? { borderColor: v.color + '66', background: v.color + '15', color: v.color } : {}}
                    >
                      <span className="w-1.5 h-1.5 rounded-full" style={{ background: sel ? v.color : '#555' }} />
                      {v.label}
                      <span className="text-[8px] opacity-50">{v.freq}</span>
                    </button>
                  )
                })}
              </div>
            </div>

            {/* Market grid — split by type */}
            <div>
              <div className="flex items-center gap-3 mb-2">
                <span className="text-[10px] text-ghost tracking-[0.15em]">MARKETS</span>
                <span className="text-[9px] text-ghost/40">{selectedDownloads.length} selected</span>
                <button onClick={() => setSelectedDownloads(availableForDownload.filter(m => m.type === 'perp').map(m => m.market))}
                  className="text-[9px] text-ghost/40 hover:text-amber ml-auto">All perps</button>
                <button onClick={() => setSelectedDownloads(availableForDownload.filter(m => m.type === 'spot').map(m => m.market))}
                  className="text-[9px] text-ghost/40 hover:text-amber">All spot</button>
                <button onClick={() => setSelectedDownloads([])} className="text-[9px] text-ghost/40 hover:text-terminal">Clear</button>
              </div>

              {/* Perp markets */}
              <div className="mb-3">
                <div className="text-[9px] text-ghost/50 tracking-wider mb-1 flex items-center gap-2">
                  <span className="w-1.5 h-1.5 bg-amber/50" />
                  PERPETUALS ({availableForDownload.filter(m => m.type === 'perp').length})
                </div>
                <div className="grid grid-cols-4 sm:grid-cols-6 lg:grid-cols-8 gap-1">
                  {availableForDownload.filter(m => m.type === 'perp').map(m => {
                    const isSelected = selectedDownloads.includes(m.market)
                    const isDownloaded = uniqueMarkets.includes(m.market)
                    return (
                      <button
                        key={m.market}
                        onClick={() => {
                          if (isSelected) setSelectedDownloads(prev => prev.filter(x => x !== m.market))
                          else setSelectedDownloads(prev => [...prev, m.market])
                        }}
                        className={`px-2 py-1.5 text-[9px] tracking-wider border transition-all text-left ${
                          isSelected ? 'border-amber/50 bg-amber-glow text-amber'
                            : isDownloaded ? 'border-gain/20 text-gain/60'
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

              {/* Spot markets */}
              <div>
                <div className="text-[9px] text-ghost/50 tracking-wider mb-1 flex items-center gap-2">
                  <span className="w-1.5 h-1.5 bg-phosphor/50" />
                  SPOT ({availableForDownload.filter(m => m.type === 'spot').length})
                </div>
                <div className="grid grid-cols-4 sm:grid-cols-6 lg:grid-cols-8 gap-1">
                  {availableForDownload.filter(m => m.type === 'spot').map(m => {
                    const isSelected = selectedDownloads.includes(m.market)
                    const isDownloaded = uniqueMarkets.includes(m.market)
                    return (
                      <button
                        key={m.market}
                        onClick={() => {
                          if (isSelected) setSelectedDownloads(prev => prev.filter(x => x !== m.market))
                          else setSelectedDownloads(prev => [...prev, m.market])
                        }}
                        className={`px-2 py-1.5 text-[9px] tracking-wider border transition-all text-left ${
                          isSelected ? 'border-phosphor/50 bg-phosphor-dim text-phosphor'
                            : isDownloaded ? 'border-gain/20 text-gain/60'
                            : 'border-border text-ghost/50 hover:text-terminal hover:border-border-bright'
                        }`}
                      >
                        {m.market}
                        {isDownloaded && <span className="text-gain/40 ml-1">&#10003;</span>}
                      </button>
                    )
                  })}
                </div>
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
                {downloadWarnings.length > 0 && (
                  <div className="mt-2 space-y-1">
                    {downloadWarnings.map((w, i) => (
                      <div key={i} className="text-[9px] text-loss/80 font-mono">⚠ {w}</div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── loading status — fixed height to prevent scroll jump ── */}
      <div className="h-7 flex items-center px-1">
        {loading ? (
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 border border-amber/50 border-t-amber rounded-full animate-spin" />
            <span className="text-[10px] text-amber/60 tracking-wider">{loadStatus || 'Loading...'}</span>
          </div>
        ) : loadStatus && candles.length > 0 ? (
          <span className="text-[10px] text-gain/60 tracking-wider">{loadStatus}</span>
        ) : null}
      </div>

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
        <div className={loading ? 'opacity-50 pointer-events-none transition-opacity' : 'transition-opacity'} style={{ animation: 'fadeUp 0.3s ease' }}>
          {/* Venue price selector */}
          <div className="flex items-center gap-3 mb-3">
            <span className="text-[9px] text-ghost tracking-wider">PRICE VENUES</span>
            {['default', 'drift', 'hyperliquid', 'binance', 'okx', 'bybit'].map(v => (
              <button key={v} onClick={() => {
                setPriceVenues(prev =>
                  prev.includes(v) ? prev.filter(x => x !== v) : [...prev.filter(x => x !== 'default'), v]
                )
              }}
                className={`px-2 py-0.5 text-[9px] border transition-all ${
                  priceVenues.includes(v)
                    ? 'border-amber/50 bg-amber-glow text-amber'
                    : 'border-border text-ghost/50 hover:text-ghost'
                }`}
              >{v === 'default' ? 'ALL' : v.toUpperCase()}</button>
            ))}

            {priceVenues.length > 1 && priceVenues[0] !== 'default' && (
              <div className="flex gap-1 ml-4">
                <button onClick={() => setPriceViewMode('overlay')}
                  className={`px-2 py-0.5 text-[9px] border ${priceViewMode === 'overlay' ? 'border-amber/50 text-amber' : 'border-border text-ghost/50'}`}
                >OVERLAY</button>
                <button onClick={() => setPriceViewMode('separate')}
                  className={`px-2 py-0.5 text-[9px] border ${priceViewMode === 'separate' ? 'border-amber/50 text-amber' : 'border-border text-ghost/50'}`}
                >SEPARATE</button>
              </div>
            )}
          </div>

          <InteractiveChart
            candles={candles}
            height={500}
            indicators={chartIndicators}
            fundingRates={chartFundingRates}
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

          {/* Multi-venue overlay chart */}
          {Object.keys(venueCandles).length > 1 && priceViewMode === 'overlay' && (
            <div className="mt-4 border border-border bg-surface/30 p-4">
              <div className="text-[9px] text-ghost tracking-wider mb-2">CLOSE PRICE OVERLAY — {Object.keys(venueCandles).length} VENUES</div>
              <ResponsiveContainer width="100%" height={300}>
                <LineChart>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1a1a24" />
                  <XAxis
                    dataKey="ts"
                    type="number"
                    domain={['auto', 'auto']}
                    tickFormatter={fmtShort}
                    tick={TICK}
                  />
                  <YAxis tick={TICK} domain={['auto', 'auto']} />
                  <Tooltip
                    contentStyle={tooltipStyle}
                    labelFormatter={(ts: any) => fmtDate(Number(ts))}
                  />
                  {Object.entries(venueCandles).map(([venue, data]) => (
                    <Line
                      key={venue}
                      data={data}
                      dataKey="close"
                      name={venue.toUpperCase()}
                      stroke={VENUE_COLORS[venue] || '#888'}
                      dot={false}
                      strokeWidth={1.5}
                      isAnimationActive={false}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
              <div className="flex gap-4 mt-2">
                {Object.keys(venueCandles).map(v => (
                  <span key={v} className="text-[9px] flex items-center gap-1">
                    <span style={{ background: VENUE_COLORS[v] || '#888', width: 8, height: 8, display: 'inline-block', borderRadius: 2 }} />
                    {v.toUpperCase()}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Multi-venue separate charts */}
          {Object.keys(venueCandles).length > 1 && priceViewMode === 'separate' && (
            <div className="mt-4 space-y-4">
              {Object.entries(venueCandles).map(([venue, data]) => (
                <div key={venue} className="border border-border bg-surface/30 p-4">
                  <div className="text-[9px] tracking-wider mb-2" style={{ color: VENUE_COLORS[venue] || '#888' }}>
                    {venue.toUpperCase()} — {data.length} candles
                  </div>
                  <ResponsiveContainer width="100%" height={200}>
                    <LineChart data={downsample(data, 500)}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1a1a24" />
                      <XAxis dataKey="ts" tickFormatter={fmtShort} tick={TICK} />
                      <YAxis tick={TICK} domain={['auto', 'auto']} />
                      <Tooltip contentStyle={tooltipStyle} labelFormatter={(ts: any) => fmtDate(Number(ts))} />
                      <Line dataKey="close" stroke={VENUE_COLORS[venue] || '#888'} dot={false} strokeWidth={1.5} isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── funding tab ── */}
      {activeTab === 'funding' && (
        <div style={{ animation: 'fadeUp 0.3s ease' }} className="space-y-3">
          {Object.keys(fundingByVenue).length > 0 ? (() => {
            const VENUE_COLORS: Record<string, { color: string; label: string }> = {
              drift:       { color: '#e8a849', label: 'DRIFT' },
              hyperliquid: { color: '#22d3ee', label: 'HYPERLIQUID' },
              okx:         { color: '#a78bfa', label: 'OKX' },
              bybit:       { color: '#57c84d', label: 'BYBIT' },
              gateio:      { color: '#f472b6', label: 'GATE.IO' },
              bitget:      { color: '#fb923c', label: 'BITGET' },
              dydx:        { color: '#818cf8', label: 'DYDX' },
              mexc:        { color: '#34d399', label: 'MEXC' },
              phemex:      { color: '#f87171', label: 'PHEMEX' },
              bitmex:      { color: '#fbbf24', label: 'BITMEX' },
            }

            const venues = Object.keys(fundingByVenue).sort()
            const totalPts = Object.values(fundingByVenue).reduce((s, v) => s + v.length, 0)

            // Merge into unified timeline (one row per hour, columns per venue).
            // Data is already hourly in the DB (forward-filled at download time).
            const merged: Record<number, any> = {}
            const allBps: number[] = []
            for (const venue of venues) {
              for (const pt of fundingByVenue[venue]) {
                const hour = Math.floor(pt.ts / 3600) * 3600
                const bps = pt.rate * 10000
                if (!merged[hour]) merged[hour] = { ts: hour, date: fmtShort(hour), tooltip: fmtHour(hour) }
                merged[hour][venue] = bps
                allBps.push(bps)
              }
            }
            const sortedData = Object.values(merged).sort((a, b) => a.ts - b.ts)
            // Smart X-axis: show hours for short ranges, days for long ranges
            const spanDays = sortedData.length > 1 ? (sortedData[sortedData.length-1].ts - sortedData[0].ts) / 86400 : 0
            const xKey = spanDays <= 30 ? 'tooltip' : 'date'
            const chartData = downsample(sortedData, 600)

            // Stats (across all venues)
            const avg = allBps.length > 0 ? allBps.reduce((s, v) => s + v, 0) / allBps.length : 0
            const maxBps = allBps.length > 0 ? Math.max(...allBps) : 0
            const minBps = allBps.length > 0 ? Math.min(...allBps) : 0
            const annualized = avg * 8760 / 100

            return (
              <>
                {/* Venue legend bar */}
                <div className="border border-border bg-surface/60 p-3">
                  <div className="flex items-center gap-3 mb-2">
                    <span className="text-[10px] text-ghost tracking-[0.2em]">VENUES</span>
                    <span className="text-[9px] text-ghost/40 ml-auto">{totalPts.toLocaleString()} data points across {venues.length} venues</span>
                  </div>
                  <div className="flex h-2 w-full overflow-hidden" style={{ borderRadius: 1 }}>
                    {venues.map(v => {
                      const pct = fundingByVenue[v].length / totalPts * 100
                      return <div key={v} style={{ width: `${pct}%`, background: VENUE_COLORS[v]?.color || '#555' }} />
                    })}
                  </div>
                  <div className="flex gap-4 mt-2">
                    {venues.map(v => (
                      <div key={v} className="flex items-center gap-1.5">
                        <span className="w-2.5 h-2.5" style={{ background: VENUE_COLORS[v]?.color || '#555' }} />
                        <span className="text-[9px] text-ghost/60">{VENUE_COLORS[v]?.label || v.toUpperCase()}</span>
                        <span className="text-[9px] text-ghost/30">{fundingByVenue[v].length}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Multi-venue funding chart */}
                <div className="border border-border bg-surface/60 p-3">
                  <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">FUNDING RATE ACROSS VENUES (bps)</div>
                  <ResponsiveContainer width="100%" height={320}>
                    <LineChart data={chartData}>
                      <CartesianGrid strokeDasharray="2 6" stroke="#1a1a1f" />
                      <XAxis dataKey={xKey} tick={TICK} minTickGap={80} interval="preserveStartEnd" />
                      <YAxis tick={TICK} width={50} domain={['auto', 'auto']} />
                      <ReferenceLine y={0} stroke="#ffffff15" strokeWidth={1} />
                      <Tooltip
                        contentStyle={tooltipStyle}
                        labelFormatter={(_, payload) => {
                          const item = payload?.[0]?.payload
                          return item?.tooltip || item?.date || ''
                        }}
                        formatter={(v: any, name: any) => {
                          const vc = VENUE_COLORS[String(name)]
                          return [v != null ? `${Number(v).toFixed(3)} bps` : '—', vc?.label || String(name).toUpperCase()]
                        }}
                      />
                      {venues.map(v => (
                        <Line key={v} type="monotone" dataKey={v}
                          stroke={VENUE_COLORS[v]?.color || '#888'} strokeWidth={1.5}
                          dot={false} isAnimationActive={false} connectNulls={false} />
                      ))}
                    </LineChart>
                  </ResponsiveContainer>
                </div>

                {/* Stats row */}
                <div className="grid grid-cols-4 gap-3">
                  {[
                    { label: 'AVERAGE', value: `${avg.toFixed(2)} bps`, accent: false },
                    { label: 'MAX', value: `${maxBps.toFixed(2)} bps`, accent: false },
                    { label: 'MIN', value: `${minBps.toFixed(2)} bps`, accent: false },
                    { label: 'ANNUALIZED', value: `${annualized.toFixed(1)}%`, accent: true },
                  ].map(s => (
                    <div key={s.label} className="border border-border bg-surface/60 p-3">
                      <div className="text-[8px] text-ghost/50 tracking-wider">{s.label}</div>
                      <div className={`text-sm font-mono ${s.accent ? 'text-amber' : 'text-terminal'}`}>{s.value}</div>
                    </div>
                  ))}
                </div>

                {/* Per-venue stats */}
                <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.min(venues.length, 4)}, 1fr)` }}>
                  {venues.map(v => {
                    const data = fundingByVenue[v]
                    const bpsArr = data.map(d => d.rate * 10000)
                    const vAvg = bpsArr.reduce((s, x) => s + x, 0) / bpsArr.length
                    const vCurrent = bpsArr[bpsArr.length - 1] || 0
                    return (
                      <div key={v} className="border bg-surface/60 p-3" style={{ borderColor: (VENUE_COLORS[v]?.color || '#555') + '33' }}>
                        <div className="flex items-center gap-1.5 mb-2">
                          <span className="w-2.5 h-0.5" style={{ background: VENUE_COLORS[v]?.color || '#888' }} />
                          <span className="text-[9px] tracking-[0.15em]" style={{ color: VENUE_COLORS[v]?.color || '#888' }}>
                            {VENUE_COLORS[v]?.label || v.toUpperCase()}
                          </span>
                        </div>
                        <div className="space-y-1 text-[10px]">
                          <div className="flex justify-between">
                            <span className="text-ghost/40">current</span>
                            <span className="text-terminal font-mono">{vCurrent.toFixed(3)} bps</span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-ghost/40">avg</span>
                            <span className="text-terminal font-mono">{vAvg.toFixed(3)} bps</span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-ghost/40">points</span>
                            <span className="text-terminal font-mono">{data.length.toLocaleString()}</span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-ghost/40">range</span>
                            <span className="text-ghost/50 font-mono text-[9px]">{fmtShort(data[0]?.ts || 0)} — {fmtShort(data[data.length-1]?.ts || 0)}</span>
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </>
            )
          })() : (
            <div className="border border-border/50 py-12 text-center">
              <div className="text-ghost/30 text-xs tracking-[0.3em]">NO FUNDING DATA</div>
              <div className="text-ghost/20 text-[10px] mt-2">Download a perp market to see funding rates from Drift, Hyperliquid, OKX, and Bybit</div>
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
                  <th className="py-2 px-4">TYPE</th>
                  <th className="py-2 px-4">RES</th>
                  <th className="py-2 px-4 text-right">CANDLES</th>
                  <th className="py-2 px-4">FROM</th>
                  <th className="py-2 px-4">TO</th>
                  <th className="py-2 px-4"></th>
                </tr>
              </thead>
              <tbody>
                {markets.map((m, i) => (
                  <tr key={i}
                    className={`border-b border-border/20 hover:bg-amber-glow/50 cursor-pointer ${market === m.market ? 'bg-amber-glow/30' : ''}`}
                    onClick={() => { setMarket(m.market); setResolution(m.resolution_s) }}
                  >
                    <td className="py-1.5 px-4 text-amber/80 font-medium">{m.market}</td>
                    <td className="py-1.5 px-4">
                      <span className={`text-[8px] tracking-wider px-1.5 py-0.5 border ${
                        m.market.includes('-PERP')
                          ? 'text-amber/60 border-amber/20'
                          : 'text-phosphor/60 border-phosphor/20'
                      }`}>
                        {m.market.includes('-PERP') ? 'PERP' : 'SPOT'}
                      </span>
                    </td>
                    <td className="py-1.5 px-4 text-ghost/50">{fmtRes(m.resolution_s)}</td>
                    <td className="py-1.5 px-4 text-right text-white/60 tabular-nums">{m.candle_count.toLocaleString()}</td>
                    <td className="py-1.5 px-4 text-ghost/40 text-[10px]">{fmtDate(m.first_ts)}</td>
                    <td className="py-1.5 px-4 text-ghost/40 text-[10px]">{fmtDate(m.last_ts)}</td>
                    <td className="py-1.5 px-2">
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          if (confirm(`Delete all data for ${m.market}? You can re-download it after.`)) {
                            fetch(`/api/v1/data/market/${encodeURIComponent(m.market)}`, { method: 'DELETE' })
                              .then(() => refreshInventory())
                              .catch(() => {})
                          }
                        }}
                        className="text-[8px] text-ghost/30 hover:text-loss tracking-wider transition-colors"
                        title={`Delete ${m.market} data`}
                      >
                        DEL
                      </button>
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
