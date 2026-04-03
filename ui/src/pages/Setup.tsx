import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

type Step = 'welcome' | 'markets' | 'venues' | 'keys' | 'downloading' | 'done'

interface Market {
  market: string
  source: string
}

interface DownloadProgress {
  market: string
  status: 'pending' | 'downloading' | 'done' | 'failed'
  detail: string
}

const BACKFILL_OPTIONS = [
  { label: '30 days', value: 30 },
  { label: '60 days', value: 60 },
  { label: '90 days', value: 90 },
  { label: '180 days', value: 180 },
]

const DEFAULT_MARKETS = ['SOL-PERP', 'BTC-PERP', 'ETH-PERP']

const CANDLE_VENUES = [
  { id: 'drift', label: 'Drift', color: '#e8a849' },
  { id: 'hyperliquid', label: 'Hyperliquid', color: '#22d3ee' },
  { id: 'binance', label: 'Binance', color: '#a78bfa' },
  { id: 'okx', label: 'OKX', color: '#57c84d' },
  { id: 'bybit', label: 'Bybit', color: '#f472b6' },
]

const FUNDING_VENUES = [
  { id: 'drift', label: 'Drift', freq: '1h', color: '#e8a849' },
  { id: 'hyperliquid', label: 'Hyperliquid', freq: '1h', color: '#22d3ee' },
  { id: 'dydx', label: 'dYdX', freq: '1h', color: '#818cf8' },
  { id: 'okx', label: 'OKX', freq: '8h', color: '#57c84d' },
  { id: 'bybit', label: 'Bybit', freq: '8h', color: '#f472b6' },
  { id: 'gateio', label: 'Gate.io', freq: '8h', color: '#fb923c' },
  { id: 'bitget', label: 'Bitget', freq: '8h', color: '#f97316' },
]

export default function Setup() {
  const navigate = useNavigate()
  const [step, setStep] = useState<Step>('welcome')
  const [availableMarkets, setAvailableMarkets] = useState<Market[]>([])
  const [selectedMarkets, setSelectedMarkets] = useState<string[]>(DEFAULT_MARKETS)
  const [backfillDays, setBackfillDays] = useState(90)
  const [birdeyeKey, setBirdeyeKey] = useState('')
  const [heliusKey, setHeliusKey] = useState('')
  const [showKeys, setShowKeys] = useState(false)
  const [selectedCandleVenues, setSelectedCandleVenues] = useState<string[]>(['drift'])
  const [selectedFundingVenues, setSelectedFundingVenues] = useState<string[]>(['drift', 'hyperliquid'])
  const [downloadProgress, setDownloadProgress] = useState<DownloadProgress[]>([])
  const [loadingMarkets, setLoadingMarkets] = useState(false)
  const [fetchError, setFetchError] = useState(false)

  useEffect(() => {
    if (step === 'markets' && availableMarkets.length === 0) {
      setLoadingMarkets(true)
      fetch('/api/v1/data/available-markets')
        .then(r => r.json())
        .then(data => {
          const markets = (data.markets || []).map((m: any) => ({
            market: m.market || m,
            source: m.source || 'drift',
          }))
          setAvailableMarkets(markets)
        })
        .catch(() => setFetchError(true))
        .finally(() => setLoadingMarkets(false))
    }
  }, [step, availableMarkets.length])

  const toggleMarket = (market: string) => {
    setSelectedMarkets(prev =>
      prev.includes(market) ? prev.filter(m => m !== market) : [...prev, market]
    )
  }

  const saveKeys = async () => {
    if (birdeyeKey || heliusKey) {
      await fetch('/api/v1/system/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          birdeye_api_key: birdeyeKey || undefined,
          helius_api_key: heliusKey || undefined,
        }),
      }).catch(() => {})
    }
  }

  const startDownload = async () => {
    setStep('downloading')
    // One progress entry per market (aggregate across venues)
    const progress: DownloadProgress[] = selectedMarkets.map(m => ({
      market: m, status: 'pending', detail: '',
    }))
    setDownloadProgress([...progress])

    const now = Math.floor(Date.now() / 1000)
    const startTs = now - backfillDays * 86400

    for (let i = 0; i < selectedMarkets.length; i++) {
      progress[i].status = 'downloading'
      setDownloadProgress([...progress])

      let totalCandles = 0
      const warnings: string[] = []

      // Download candles from each selected venue
      for (const venue of selectedCandleVenues) {
        try {
          const res = await fetch('/api/v1/data/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              market: selectedMarkets[i],
              resolution_s: 3600,
              start_ts: startTs,
              end_ts: now,
              venue,
              funding_venues: selectedFundingVenues,
            }),
          })
          if (!res.ok) throw new Error(`HTTP ${res.status}`)
          const data = await res.json()
          totalCandles += (data.downloaded || 0) + (data.existing || 0)
          if (data.warnings) warnings.push(...data.warnings)
        } catch {
          warnings.push(`${venue}: download failed`)
        }
      }

      progress[i].status = totalCandles > 0 || warnings.length === 0 ? 'done' : 'failed'
      progress[i].detail = totalCandles > 0
        ? `${totalCandles.toLocaleString()} candles (${selectedCandleVenues.length} venue${selectedCandleVenues.length > 1 ? 's' : ''})`
        : warnings.length > 0 ? warnings[0] : 'no data'
      setDownloadProgress([...progress])
    }
    setStep('done')
  }

  const handleKeysNext = async () => {
    await saveKeys()
    await startDownload()
  }

  // -- Welcome --
  if (step === 'welcome') {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-8">
        <div className="text-center">
          <h1 className="text-amber text-2xl font-bold tracking-[0.2em] mb-3">FLINT</h1>
          <p className="text-ghost text-sm tracking-wider">
            Algorithmic trading, backtesting & MEV research for Solana
          </p>
        </div>
        <button
          onClick={() => setStep('markets')}
          className="px-8 py-3 border border-amber text-amber text-xs tracking-[0.2em] hover:bg-amber-glow transition-colors"
        >
          GET STARTED
        </button>
        <p className="text-ghost/50 text-[10px] tracking-wider">
          This will download market data so you can start backtesting
        </p>
      </div>
    )
  }

  // -- Markets --
  if (step === 'markets') {
    const perpMarkets = availableMarkets.filter(m => m.market.endsWith('-PERP'))
    const spotMarkets = availableMarkets.filter(m => !m.market.endsWith('-PERP'))

    return (
      <div className="max-w-2xl mx-auto">
        <h2 className="text-amber text-sm tracking-[0.2em] mb-1">SELECT MARKETS</h2>
        <p className="text-ghost text-xs mb-6">Choose which markets to download. You can add more later from the Data page.</p>

        <div className="mb-6">
          <label className="text-ghost text-[10px] tracking-wider block mb-2">BACKFILL PERIOD</label>
          <div className="flex gap-2">
            {BACKFILL_OPTIONS.map(opt => (
              <button
                key={opt.value}
                onClick={() => setBackfillDays(opt.value)}
                className={`px-3 py-1.5 text-[11px] border transition-colors ${
                  backfillDays === opt.value
                    ? 'border-amber text-amber bg-amber-glow'
                    : 'border-border text-ghost hover:border-border-bright'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {loadingMarkets ? (
          <p className="text-ghost text-xs animate-pulse">Loading available markets...</p>
        ) : fetchError ? (
          <div className="text-loss text-xs mb-4">
            <p>Failed to load markets.</p>
            <button
              onClick={() => { setFetchError(false); setAvailableMarkets([]); }}
              className="text-amber text-[11px] mt-2 hover:underline"
            >
              Retry
            </button>
          </div>
        ) : (
          <>
            {perpMarkets.length > 0 && (
              <div className="mb-4">
                <h3 className="text-ghost text-[10px] tracking-wider mb-2">PERPETUALS</h3>
                <div className="grid grid-cols-3 sm:grid-cols-4 gap-1.5">
                  {perpMarkets.map(m => (
                    <button
                      key={m.market}
                      onClick={() => toggleMarket(m.market)}
                      className={`px-2 py-1.5 text-[11px] border text-left transition-colors ${
                        selectedMarkets.includes(m.market)
                          ? 'border-amber text-amber bg-amber-glow'
                          : 'border-border text-ghost hover:border-border-bright'
                      }`}
                    >
                      {m.market}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {spotMarkets.length > 0 && (
              <div className="mb-4">
                <h3 className="text-ghost text-[10px] tracking-wider mb-2">SPOT</h3>
                <div className="grid grid-cols-3 sm:grid-cols-4 gap-1.5">
                  {spotMarkets.map(m => (
                    <button
                      key={m.market}
                      onClick={() => toggleMarket(m.market)}
                      className={`px-2 py-1.5 text-[11px] border text-left transition-colors ${
                        selectedMarkets.includes(m.market)
                          ? 'border-amber text-amber bg-amber-glow'
                          : 'border-border text-ghost hover:border-border-bright'
                      }`}
                    >
                      {m.market}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        <div className="flex items-center justify-between mt-8 pt-4 border-t border-border">
          <span className="text-ghost text-[10px]">{selectedMarkets.length} market{selectedMarkets.length !== 1 ? 's' : ''} selected</span>
          <button
            onClick={() => setStep('venues')}
            disabled={selectedMarkets.length === 0}
            className="px-6 py-2 border border-amber text-amber text-xs tracking-[0.15em] hover:bg-amber-glow transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          >
            NEXT
          </button>
        </div>
      </div>
    )
  }

  // -- Venues --
  if (step === 'venues') {
    return (
      <div className="max-w-2xl mx-auto">
        <h2 className="text-amber text-sm tracking-[0.2em] mb-1">SELECT VENUES</h2>
        <p className="text-ghost text-xs mb-6">Choose which venues to download price and funding data from. More venues = more cross-venue analysis.</p>

        <div className="mb-6">
          <label className="text-ghost text-[10px] tracking-wider block mb-2">
            CANDLE SOURCES <span className="text-ghost/40">— OHLCV price data</span>
          </label>
          <div className="flex flex-wrap gap-2">
            {CANDLE_VENUES.map(v => (
              <button
                key={v.id}
                onClick={() => setSelectedCandleVenues(prev =>
                  prev.includes(v.id) ? prev.filter(x => x !== v.id) : [...prev, v.id]
                )}
                className={`px-3 py-1.5 text-[11px] border transition-colors ${
                  selectedCandleVenues.includes(v.id)
                    ? 'border-amber text-amber bg-amber-glow'
                    : 'border-border text-ghost hover:border-border-bright'
                }`}
                style={selectedCandleVenues.includes(v.id) ? { borderColor: v.color, color: v.color } : {}}
              >
                {v.label}
              </button>
            ))}
          </div>
          <p className="text-ghost/40 text-[9px] mt-1">{selectedCandleVenues.length} selected — candles stored per venue for comparison</p>
        </div>

        <div className="mb-6">
          <label className="text-ghost text-[10px] tracking-wider block mb-2">
            FUNDING VENUES <span className="text-ghost/40">— funding rate data for arb strategies</span>
          </label>
          <div className="flex flex-wrap gap-2">
            {FUNDING_VENUES.map(v => (
              <button
                key={v.id}
                onClick={() => setSelectedFundingVenues(prev =>
                  prev.includes(v.id) ? prev.filter(x => x !== v.id) : [...prev, v.id]
                )}
                className={`px-3 py-1.5 text-[11px] border transition-colors ${
                  selectedFundingVenues.includes(v.id)
                    ? 'border-amber text-amber bg-amber-glow'
                    : 'border-border text-ghost hover:border-border-bright'
                }`}
                style={selectedFundingVenues.includes(v.id) ? { borderColor: v.color, color: v.color } : {}}
              >
                {v.label} <span className="text-[9px] opacity-60">{v.freq}</span>
              </button>
            ))}
          </div>
          <p className="text-ghost/40 text-[9px] mt-1">{selectedFundingVenues.length} selected — needed for funding arb and mean reversion strategies</p>
        </div>

        <div className="flex items-center justify-between mt-8 pt-4 border-t border-border">
          <button onClick={() => setStep('markets')} className="text-ghost text-xs hover:text-terminal transition-colors">
            BACK
          </button>
          <button
            onClick={() => setStep('keys')}
            disabled={selectedCandleVenues.length === 0}
            className="px-6 py-2 border border-amber text-amber text-xs tracking-[0.15em] hover:bg-amber-glow transition-colors disabled:opacity-30"
          >
            NEXT
          </button>
        </div>
      </div>
    )
  }

  // -- Keys --
  if (step === 'keys') {
    return (
      <div className="max-w-lg mx-auto">
        <h2 className="text-amber text-sm tracking-[0.2em] mb-1">API KEYS</h2>
        <p className="text-ghost text-xs mb-6">Optional — most data sources work without keys. You can add these later.</p>

        <button
          onClick={() => setShowKeys(!showKeys)}
          className="text-ghost text-[11px] hover:text-terminal transition-colors mb-4"
        >
          {showKeys ? '\u25be' : '\u25b8'} Configure API keys
        </button>

        {showKeys && (
          <div className="space-y-4 mb-6 p-4 border border-border">
            <div>
              <label className="text-ghost text-[10px] tracking-wider block mb-1">BIRDEYE API KEY</label>
              <input
                type="text"
                value={birdeyeKey}
                onChange={e => setBirdeyeKey(e.target.value)}
                placeholder="For any Solana token OHLCV data"
                className="w-full bg-void border border-border px-3 py-2 text-xs text-terminal placeholder:text-ghost/40 focus:border-amber focus:outline-none"
              />
              <p className="text-ghost/50 text-[10px] mt-1">Free at birdeye.so/developers</p>
            </div>
            <div>
              <label className="text-ghost text-[10px] tracking-wider block mb-1">HELIUS API KEY</label>
              <input
                type="text"
                value={heliusKey}
                onChange={e => setHeliusKey(e.target.value)}
                placeholder="For liquidations & whale tracking"
                className="w-full bg-void border border-border px-3 py-2 text-xs text-terminal placeholder:text-ghost/40 focus:border-amber focus:outline-none"
              />
              <p className="text-ghost/50 text-[10px] mt-1">Free at helius.dev</p>
            </div>
          </div>
        )}

        <div className="flex items-center justify-between mt-8 pt-4 border-t border-border">
          <button
            onClick={() => setStep('venues')}
            className="text-ghost text-xs hover:text-terminal transition-colors"
          >
            BACK
          </button>
          <button
            onClick={handleKeysNext}
            className="px-6 py-2 border border-amber text-amber text-xs tracking-[0.15em] hover:bg-amber-glow transition-colors"
          >
            {selectedMarkets.length > 0 ? 'DOWNLOAD DATA' : 'SKIP'}
          </button>
        </div>
      </div>
    )
  }

  // -- Downloading --
  if (step === 'downloading') {
    const completed = downloadProgress.filter(p => p.status === 'done' || p.status === 'failed').length
    return (
      <div className="max-w-lg mx-auto">
        <h2 className="text-amber text-sm tracking-[0.2em] mb-1">DOWNLOADING</h2>
        <p className="text-ghost text-xs mb-6">
          Fetching {backfillDays} days of data for {selectedMarkets.length} market{selectedMarkets.length !== 1 ? 's' : ''}...
        </p>

        <div className="space-y-2">
          {downloadProgress.map(p => (
            <div key={p.market} className="flex items-center justify-between py-1.5 px-3 border border-border text-xs">
              <span className="text-terminal">{p.market}</span>
              <span className={
                p.status === 'done' ? 'text-gain' :
                p.status === 'failed' ? 'text-loss' :
                p.status === 'downloading' ? 'text-amber animate-pulse' :
                'text-ghost/40'
              }>
                {p.status === 'pending' ? 'waiting' :
                 p.status === 'downloading' ? 'downloading...' :
                 p.detail}
              </span>
            </div>
          ))}
        </div>

        <div className="mt-4">
          <div className="h-px bg-border relative">
            <div
              className="absolute inset-y-0 left-0 bg-amber transition-all duration-500"
              style={{ width: `${selectedMarkets.length > 0 ? (completed / selectedMarkets.length) * 100 : 0}%` }}
            />
          </div>
          <p className="text-ghost/50 text-[10px] mt-2 text-right">{completed}/{selectedMarkets.length}</p>
        </div>
      </div>
    )
  }

  // -- Done --
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-6">
      <div className="text-center">
        <h2 className="text-gain text-sm tracking-[0.2em] mb-2">SETUP COMPLETE</h2>
        <p className="text-ghost text-xs">
          {downloadProgress.filter(p => p.status === 'done').length} market{downloadProgress.filter(p => p.status === 'done').length !== 1 ? 's' : ''} ready for backtesting
        </p>
      </div>

      <div className="flex gap-3">
        <button
          onClick={() => navigate('/backtest')}
          className="px-6 py-2 border border-amber text-amber text-xs tracking-[0.15em] hover:bg-amber-glow transition-colors"
        >
          RUN A BACKTEST
        </button>
        <button
          onClick={() => navigate('/')}
          className="px-6 py-2 border border-border text-ghost text-xs tracking-[0.15em] hover:border-border-bright transition-colors"
        >
          GO TO DASHBOARD
        </button>
      </div>
    </div>
  )
}
