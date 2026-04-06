import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { EXECUTION_VENUES, getVenue } from '../constants/venues'
import { useBacktest } from '../hooks/useBacktest'
import { useStrategies } from '../hooks/useStrategies'
import { useOptimize } from '../hooks/useOptimize'
import { useJournal } from '../hooks/useJournal'
import CodeEditor from '../components/CodeEditor'
import EquityCurve from '../components/EquityCurve'
import DrawdownChart from '../components/DrawdownChart'
import MetricsCard from '../components/MetricsCard'
import TradeTable from '../components/TradeTable'
import PriceChart from '../components/PriceChart'
import PnlHistogram from '../components/PnlHistogram'
import ExposureTimeline from '../components/ExposureTimeline'
import InstrumentExposure from '../components/InstrumentExposure'
import SplitMetrics from '../components/SplitMetrics'

/* ── all supported markets ───────────────────────────── */

const PERP_MARKETS = [
  'SOL-PERP', 'BTC-PERP', 'ETH-PERP', 'APT-PERP', '1MBONK-PERP',
  'POL-PERP', 'ARB-PERP', 'DOGE-PERP', 'BNB-PERP', 'SUI-PERP',
  '1MPEPE-PERP', 'OP-PERP', 'RENDER-PERP', 'XRP-PERP', 'HNT-PERP',
  'INJ-PERP', 'LINK-PERP', 'RLB-PERP', 'PYTH-PERP', 'TIA-PERP',
  'JTO-PERP', 'SEI-PERP', 'AVAX-PERP', 'WIF-PERP', 'JUP-PERP',
  'DYM-PERP', 'TAO-PERP', 'W-PERP', 'KMNO-PERP', 'TNSR-PERP',
  'DRIFT-PERP', 'CLOUD-PERP', 'IO-PERP', 'ZEX-PERP', 'POPCAT-PERP',
  '1KWEN-PERP', 'MOTHER-PERP', 'LTC-PERP', 'RAY-PERP', 'PENGU-PERP',
  'ME-PERP', 'PNUT-PERP',
]

const SPOT_MARKETS = ['SOL', 'JTO', 'WIF', 'JUP', 'DRIFT', 'POPCAT']

const RESOLUTIONS: Record<string, number> = {
  '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400,
}

const FEE_PRESETS: Record<string, { label: string; rate: number; venue: string }> = {
  // Drift Protocol
  'drift_taker':     { label: 'Drift Taker (10bps)',   rate: 0.001,   venue: 'drift' },
  'drift_maker':     { label: 'Drift Maker (-2bps)',   rate: -0.0002, venue: 'drift' },
  'drift_vip':       { label: 'Drift VIP (6bps)',      rate: 0.0006,  venue: 'drift' },
  // Hyperliquid
  'hl_taker':        { label: 'Hyperliquid Taker (3.5bps)', rate: 0.00035, venue: 'hyperliquid' },
  'hl_maker':        { label: 'Hyperliquid Maker (1bps)',   rate: 0.0001,  venue: 'hyperliquid' },
  // Binance Futures
  'binance_taker':   { label: 'Binance Taker (4.5bps)', rate: 0.00045, venue: 'binance' },
  'binance_maker':   { label: 'Binance Maker (2bps)',   rate: 0.0002,  venue: 'binance' },
  // OKX
  'okx_taker':       { label: 'OKX Taker (5bps)',       rate: 0.0005,  venue: 'okx' },
  'okx_maker':       { label: 'OKX Maker (2bps)',       rate: 0.0002,  venue: 'okx' },
  // Bybit
  'bybit_taker':     { label: 'Bybit Taker (5.5bps)',   rate: 0.00055, venue: 'bybit' },
  'bybit_maker':     { label: 'Bybit Maker (2bps)',      rate: 0.0002,  venue: 'bybit' },
  // Jupiter Perps
  'jupiter':         { label: 'Jupiter Perps (6bps)',    rate: 0.0006,  venue: 'jupiter' },
  // Generic
  'flat_5bps':       { label: 'Flat 5bps',              rate: 0.0005,  venue: 'generic' },
  'flat_1bps':       { label: 'Flat 1bps',              rate: 0.0001,  venue: 'generic' },
  'zero':            { label: 'Zero Fees',              rate: 0,       venue: 'generic' },
}

/* ── strategy templates ────────────────────────────────── */
import { TEMPLATES } from '../templates'

/* ── run history entry ───────────────────────────────── */

interface RunRecord {
  id: string
  strategy: string
  market: string
  resolution: string
  pnl: number
  sharpe: number
  trades: number
  ts: number
}

/* ── helpers ────────────────────────────────────────────── */

function extractMarketsFromCode(code: string): string[] {
  const markets: string[] = []
  // Match ctx.get_candles("MARKET-NAME") patterns
  const regex = /get_candles\s*\(\s*["']([A-Z0-9]+-(?:PERP|[A-Z]+))["']/g
  let match
  while ((match = regex.exec(code)) !== null) {
    if (!markets.includes(match[1])) {
      markets.push(match[1])
    }
  }
  return markets
}

/* ── strategy profile detection ─────────────────────── */

interface StrategyProfile {
  type: 'single' | 'multi_venue' | 'multi_market' | 'cross_venue_multi_market'
  venues: string[]              // Specific venues detected (e.g., ["drift", "jupiter"])
  markets: string[]             // Extra markets detected
  needsFunding: boolean
  needsCrossVenueFunding: boolean
  needsBorrowRate: boolean      // uses get_borrow_rate (needs Jupiter)
  needsOracle: boolean
  needsOrderbook: boolean
  usesAllVenues: boolean        // Uses get_funding_by_venue without specific venues
  venueRequirement: 'specific' | 'any_dex' | 'any_cex' | 'any' | 'none'
}

function detectStrategyProfile(code: string): StrategyProfile {
  // Detect explicit venues from venue="xxx" patterns
  const venueMatches = [...code.matchAll(/venue\s*=\s*["']([a-z_]+)["']/g)]
  const venues = [...new Set(venueMatches.map(m => m[1]).filter(v => v !== 'default'))]

  // Detect markets from get_candles calls
  const marketMatches = [...code.matchAll(/get_candles\s*\(\s*["']([A-Z0-9]+-(?:PERP|[A-Z]+))["']/g)]
  const markets = [...new Set(marketMatches.map(m => m[1]))]

  // Detect data needs
  const needsFunding = /get_funding_rates?\s*\(/.test(code)
  const needsCrossVenueFunding = /get_funding_by_venue\s*\(/.test(code)
  const needsBorrowRate = /get_borrow_rate/.test(code)
  const needsOracle = /get_oracle_price\s*\(/.test(code)
  const needsOrderbook = /get_orderbook\s*\(/.test(code)

  // Detect if strategy uses venue-agnostic cross-venue data (get_funding_by_venue without specific venue= calls)
  const usesAllVenues = needsCrossVenueFunding && venues.length === 0

  // Classify
  const isMultiVenue = venues.length > 1 || needsCrossVenueFunding
  const isMultiMarket = markets.length > 0

  let type: StrategyProfile['type'] = 'single'
  if (isMultiVenue && isMultiMarket) type = 'cross_venue_multi_market'
  else if (isMultiVenue) type = 'multi_venue'
  else if (isMultiMarket) type = 'multi_market'

  // Determine venue requirement
  let venueRequirement: StrategyProfile['venueRequirement'] = 'none'
  if (venues.length > 0) {
    venueRequirement = 'specific'
  } else if (usesAllVenues) {
    venueRequirement = 'any'
  } else if (needsBorrowRate) {
    venueRequirement = 'specific' // needs Jupiter
    if (!venues.includes('jupiter')) venues.push('jupiter')
  }

  return { type, venues, markets, needsFunding, needsCrossVenueFunding, needsBorrowRate, needsOracle, needsOrderbook, usesAllVenues, venueRequirement }
}

/* ── date range presets ─────────────────────────────────── */

type RangePreset = '1M' | '3M' | '6M' | '1Y' | '3Y' | 'CUSTOM'

function getPresetDates(preset: RangePreset): { start: string; end: string } | null {
  if (preset === 'CUSTOM') return null
  const end = new Date()
  const start = new Date()
  switch (preset) {
    case '1M': start.setMonth(start.getMonth() - 1); break
    case '3M': start.setMonth(start.getMonth() - 3); break
    case '6M': start.setMonth(start.getMonth() - 6); break
    case '1Y': start.setFullYear(start.getFullYear() - 1); break
    case '3Y': start.setFullYear(start.getFullYear() - 3); break
  }
  return {
    start: start.toISOString().split('T')[0],
    end: end.toISOString().split('T')[0],
  }
}

/* ── component ─────────────────────────────────────────── */

export default function BacktestLab() {
  const { run, status, results, error, progress } = useBacktest()
  const { run: runOptimize, status: optStatus, results: optResults, error: optError, progress: optProgress } = useOptimize()
  const { runs: journalRuns, refresh: refreshJournal, deleteRun: deleteJournalRun } = useJournal()
  const [showJournal, setShowJournal] = useState(false)
  const [optTrials, setOptTrials] = useState(50)
  const [optMetric, setOptMetric] = useState('sharpe_ratio')
  const { strategies: savedStrategies, save, load, remove, validate, loading: savingStrategy, refresh } = useStrategies()

  // Editor state
  const [code, setCode] = useState(TEMPLATES.blank.code)
  const [currentName, setCurrentName] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [validationError, setValidationError] = useState<string | null>(null)

  // Config state
  const [market, setMarket] = useState('SOL-PERP')
  const [venue, setVenue] = useState('drift')
  const [resolution, setResolution] = useState('1h')
  const [startDate, setStartDate] = useState('2025-01-01')
  const [endDate, setEndDate] = useState('2025-01-31')
  const [capital, setCapital] = useState(10000)
  const [feePreset, setFeePreset] = useState('drift_taker')
  const [feeRate, setFeeRate] = useState(0.001)

  // Run history (session-local)
  const [runHistory, setRunHistory] = useState<RunRecord[]>([])
  const [showHistory, setShowHistory] = useState(false)

  // Available markets from API
  const [availableMarkets, setAvailableMarkets] = useState<string[]>([])

  // Data availability
  const [dataCheck, setDataCheck] = useState<{
    has_data: boolean, covers_range: boolean, will_download: boolean,
    candle_count: number, total_in_db: number,
    first_ts?: number | null, last_ts?: number | null,
  } | null>(null)
  const [dataCheckLoading, setDataCheckLoading] = useState(false)
  const [configError, setConfigError] = useState<string | null>(null)

  // Active template hint
  const [activeTemplateKey, setActiveTemplateKey] = useState<string | null>(null)

  // Date range preset
  const [rangePreset, setRangePreset] = useState<RangePreset>('CUSTOM')

  // Multi-market data check
  const [multiMarketCheck, setMultiMarketCheck] = useState<{
    ready: boolean
    markets: Record<string, { has_data: boolean; covers_range: boolean; candle_count: number }>
    missing: string[]
  } | null>(null)

  // Syncing strategies from folder
  const [syncing, setSyncing] = useState(false)

  // Execution features
  const [marginTracking, setMarginTracking] = useState(false)

  // Strategy profile detection
  const [stratProfile, setStratProfile] = useState<StrategyProfile | null>(null)

  // Deploy to paper trading
  const [showDeploy, setShowDeploy] = useState(false)
  const [deployCapital, setDeployCapital] = useState(10000)
  const [deployMaxDD, setDeployMaxDD] = useState(15)
  const [deployDailyLoss, setDeployDailyLoss] = useState(500)
  const [deployVenue, setDeployVenue] = useState('drift')
  const [deploying, setDeploying] = useState(false)

  // Venue data availability per venue
  const [venueDataStatus, setVenueDataStatus] = useState<Record<string, { has_candles: boolean; has_funding: boolean; has_orderbook: boolean }>>({})

  const codeRef = useRef(code)
  codeRef.current = code

  // Detect strategy profile when template changes
  useEffect(() => {
    const profile = detectStrategyProfile(codeRef.current)
    setStratProfile(profile)
  }, [activeTemplateKey])

  // Debounced strategy re-detection on code edits
  useEffect(() => {
    const timer = setTimeout(() => {
      const profile = detectStrategyProfile(code)
      setStratProfile(profile)
    }, 800)
    return () => clearTimeout(timer)
  }, [code])

  // Auto-enable margin tracking for multi-venue strategies
  useEffect(() => {
    if (stratProfile && (stratProfile.type === 'multi_venue' || stratProfile.type === 'cross_venue_multi_market')) {
      setMarginTracking(true)
    }
  }, [stratProfile])

  // Check venue data availability for the selected market
  useEffect(() => {
    const checkVenueData = async () => {
      try {
        // Check funding data per venue
        const fRes = await fetch(`/api/v1/data/funding?market=${encodeURIComponent(market)}`)
        const fData = await fRes.json()
        const fundingVenues = Object.keys(fData.venues || {})

        // Check volume/candle data per venue
        const vRes = await fetch(`/api/v1/data/volume?market=${encodeURIComponent(market)}`)
        const vData = await vRes.json()
        const volumeVenues = Object.keys(vData.venues || {})

        // Check Jupiter borrow rate data
        let hasBorrowData = false
        try {
          const bRes = await fetch(`/api/v1/data/borrow-rates?market=${encodeURIComponent(market)}`)
          const bData = await bRes.json()
          hasBorrowData = (bData.count || 0) > 0
        } catch {}

        const status: Record<string, { has_candles: boolean; has_funding: boolean; has_orderbook: boolean }> = {}
        for (const v of EXECUTION_VENUES) {
          status[v.id] = {
            has_candles: volumeVenues.includes(v.id),
            has_funding: v.id === 'jupiter' ? hasBorrowData : fundingVenues.includes(v.id),
            has_orderbook: volumeVenues.includes(v.id),
          }
        }
        setVenueDataStatus(status)
      } catch { /* ignore — data status is advisory */ }
    }
    if (market) checkVenueData()

    // Re-check when page becomes visible (user returns from Data Explorer after downloading)
    const onFocus = () => { if (market) checkVenueData() }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [market, availableMarkets])

  // Auto-set venue when strategy profile changes
  useEffect(() => {
    if (!stratProfile) return

    if (stratProfile.venueRequirement === 'specific' && stratProfile.venues.length > 0) {
      // Auto-select the first required venue
      setVenue(stratProfile.venues[0])
      // Auto-set fee rate
      const info = getVenue(stratProfile.venues[0])
      if (info) {
        const bps = parseFloat(info.takerFee)
        if (!isNaN(bps)) setFeeRate(bps / 10000)
      }
    }

    // Auto-set market if strategy declares specific markets
    if (stratProfile.markets.length > 0 && !stratProfile.markets.includes(market)) {
      // Current market not in strategy — switch to first detected
      const firstMarket = stratProfile.markets[0]
      if (availableMarkets.includes(firstMarket)) {
        setMarket(firstMarket)
      }
    }
  }, [stratProfile])

  // Fetch available markets on mount + auto-detect best date range
  useEffect(() => {
    fetch('/api/v1/data/markets')
      .then(r => r.json())
      .then(data => {
        if (data.markets && data.markets.length > 0) {
          const nameSet: Set<string> = new Set(data.markets.map((m: any) => m.market))
          setAvailableMarkets(Array.from(nameSet))

          // Auto-set date range to match available data for current market
          const marketData = data.markets.find((m: any) => m.market === market && m.resolution_s === (RESOLUTIONS[resolution] || 3600))
          if (marketData && marketData.first_ts && marketData.last_ts) {
            const first = new Date(marketData.first_ts * 1000)
            const last = new Date(marketData.last_ts * 1000)
            // Use last 90 days of available data, or full range if shorter
            const ninetyDaysAgo = new Date(last.getTime() - 90 * 86400000)
            const autoStart = ninetyDaysAgo > first ? ninetyDaysAgo : first
            setStartDate(autoStart.toISOString().split('T')[0])
            setEndDate(last.toISOString().split('T')[0])
          }
        }
      })
      .catch(() => {})
  }, [])

  // Validate config and check data availability on every change
  useEffect(() => {
    const startTs = Math.floor(new Date(startDate + 'T00:00:00Z').getTime() / 1000)
    const endTs = Math.floor(new Date(endDate + 'T23:59:59Z').getTime() / 1000)
    const res_s = RESOLUTIONS[resolution] || 3600

    // Validate fields
    if (!startDate || !endDate) {
      setConfigError('Enter start and end dates')
      setDataCheck(null)
      setMultiMarketCheck(null)
      return
    }
    if (isNaN(startTs) || isNaN(endTs)) {
      setConfigError('Invalid date format — use YYYY-MM-DD')
      setDataCheck(null)
      setMultiMarketCheck(null)
      return
    }
    if (startTs >= endTs) {
      setConfigError('Start date must be before end date')
      setDataCheck(null)
      setMultiMarketCheck(null)
      return
    }
    const durationH = (endTs - startTs) / 3600
    if (durationH < 10 * (res_s / 3600)) {
      setConfigError(`Range too short — need at least ${(10 * res_s / 3600).toFixed(0)}h for ${resolution} candles`)
      setDataCheck(null)
      setMultiMarketCheck(null)
      return
    }
    if (capital <= 0) {
      setConfigError('Capital must be > 0')
      setDataCheck(null)
      setMultiMarketCheck(null)
      return
    }

    setConfigError(null)
    setDataCheckLoading(true)

    const controller = new AbortController()

    // Extract all markets from code (primary + extras)
    const extraMarkets = extractMarketsFromCode(codeRef.current)
    const allMarkets = [market, ...extraMarkets.filter(m => m !== market)]

    if (allMarkets.length > 1) {
      // Multi-market check
      fetch('/api/v1/data/check-markets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ markets: allMarkets, resolution_s: res_s, start_ts: startTs, end_ts: endTs }),
        signal: controller.signal,
      })
        .then(r => r.json())
        .then(d => {
          setMultiMarketCheck(d)
          // Also set primary market data check for backwards compat
          const primary = d.markets?.[market]
          if (primary) {
            setDataCheck({
              has_data: primary.has_data,
              covers_range: primary.covers_range,
              will_download: !primary.covers_range,
              candle_count: primary.candle_count,
              total_in_db: primary.candle_count,
            })
          }
          setDataCheckLoading(false)
        })
        .catch(() => {
          setMultiMarketCheck(null)
          setDataCheck(null)
          setDataCheckLoading(false)
        })
    } else {
      // Single market — use existing endpoint
      setMultiMarketCheck(null)
      fetch(`/api/v1/data/check?market=${encodeURIComponent(market)}&resolution_s=${res_s}&start_ts=${startTs}&end_ts=${endTs}`, { signal: controller.signal })
        .then(r => r.json())
        .then(d => {
          if (d && typeof d.candle_count === 'number' && typeof d.has_data === 'boolean') {
            setDataCheck(d)
          } else {
            setDataCheck(null)
          }
          setDataCheckLoading(false)
        })
        .catch(() => {
          setDataCheck(null)
          setDataCheckLoading(false)
        })
    }

    return () => controller.abort()
  }, [market, resolution, startDate, endDate, capital, code])

  // Sync fee rate with preset
  useEffect(() => {
    const preset = FEE_PRESETS[feePreset]
    if (preset) setFeeRate(preset.rate)
  }, [feePreset])

  // Save to run history when results arrive
  useEffect(() => {
    if (status === 'complete' && results) {
      setRunHistory(prev => [{
        id: Math.random().toString(36).slice(2, 8),
        strategy: results.strategy_name || 'custom',
        market: results.market || market,
        resolution,
        pnl: results.metrics?.total_pnl ?? 0,
        sharpe: results.metrics?.sharpe_ratio ?? 0,
        trades: results.trades?.length ?? 0,
        ts: Date.now(),
      }, ...prev].slice(0, 20))
    }
  }, [status, results])

  /* ── handlers ──────────────────────────────────────── */

  const handleCodeChange = useCallback((value: string) => {
    setCode(value)
    setDirty(true)
    setValidationError(null)
  }, [])

  const handleTemplateSelect = useCallback((key: string) => {
    if (dirty && !window.confirm('You have unsaved changes. Discard?')) return
    const tpl = TEMPLATES[key]
    if (tpl) {
      setCode(tpl.code)
      setCurrentName(null)
      setDirty(false)
      setValidationError(null)
      setActiveTemplateKey(key)
    }
  }, [dirty])

  const handleLoadStrategy = useCallback(async (name: string) => {
    if (dirty && !window.confirm('You have unsaved changes. Discard?')) return
    const loaded = await load(name)
    setCode(loaded)
    setCurrentName(name)
    setDirty(false)
    setValidationError(null)
    setActiveTemplateKey(null)
    setStratProfile(detectStrategyProfile(loaded))
  }, [load, dirty])

  const handleDeleteStrategy = useCallback(async (name: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (window.confirm(`Delete strategy "${name}"?`)) {
      await remove(name)
      if (currentName === name) {
        setCurrentName(null)
        setCode(TEMPLATES.blank.code)
        setDirty(false)
      }
    }
  }, [remove, currentName])

  const handleSave = useCallback(async () => {
    let name = currentName
    if (!name) {
      const input = window.prompt('Strategy name:')
      if (!input) return
      name = input.trim().replace(/\s+/g, '_').toLowerCase()
      if (!name) return
    }
    await save(name, codeRef.current)
    setCurrentName(name)
    setDirty(false)
  }, [currentName, save])

  const handleValidate = useCallback(async () => {
    const result = await validate(codeRef.current)
    if (result.valid) {
      setValidationError(null)
    } else {
      setValidationError(result.error || 'Validation failed')
    }
    return result.valid
  }, [validate])

  const handleRun = useCallback(async () => {
    // Re-detect strategy profile before submitting
    setStratProfile(detectStrategyProfile(codeRef.current))

    setValidationError(null)
    const startTs = Math.floor(new Date(startDate + 'T00:00:00Z').getTime() / 1000)
    const endTs = Math.floor(new Date(endDate + 'T23:59:59Z').getTime() / 1000)

    // Client-side date validation
    if (isNaN(startTs) || isNaN(endTs)) {
      setValidationError('Invalid date format')
      return
    }
    if (startTs >= endTs) {
      setValidationError('Start date must be before end date')
      return
    }
    const res_s = RESOLUTIONS[resolution] || 3600
    const durationH = (endTs - startTs) / 3600
    const minCandles = 10
    if (durationH < minCandles * (res_s / 3600)) {
      setValidationError(`Date range too short — need at least ${minCandles} candles (${(minCandles * res_s / 3600).toFixed(0)}h for ${resolution})`)
      return
    }

    const extraMarkets = extractMarketsFromCode(codeRef.current)

    run({
      strategy: 'custom',
      code: codeRef.current,
      market,
      markets: extraMarkets.length > 0 ? [market, ...extraMarkets] : undefined,
      resolution_s: res_s,
      start_ts: startTs,
      end_ts: endTs,
      initial_capital: capital,
      fee_rate: Math.max(feeRate, 0),
      params: {},
      margin_tracking: marginTracking,
    })
  }, [run, market, resolution, startDate, endDate, capital, feeRate, marginTracking])

  const handleOptimize = useCallback(async () => {
    setValidationError(null)
    const startTs = Math.floor(new Date(startDate + 'T00:00:00Z').getTime() / 1000)
    const endTs = Math.floor(new Date(endDate + 'T23:59:59Z').getTime() / 1000)
    if (isNaN(startTs) || isNaN(endTs) || startTs >= endTs) {
      setValidationError('Invalid date range for optimization')
      return
    }
    runOptimize({
      code: codeRef.current,
      market,
      resolution_s: RESOLUTIONS[resolution] || 3600,
      start_ts: startTs,
      end_ts: endTs,
      initial_capital: capital,
      fee_rate: Math.max(feeRate, 0),
      metric: optMetric,
      trials: optTrials,
    })
  }, [runOptimize, market, resolution, startDate, endDate, capital, feeRate, optMetric, optTrials])

  // Refresh journal when a backtest completes
  useEffect(() => {
    if (status === 'complete') refreshJournal()
  }, [status, refreshJournal])

  const handleExport = useCallback(() => {
    if (!results) return
    const blob = new Blob([JSON.stringify(results, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `flint-${results.strategy_name}-${results.market}-${Date.now()}.json`
    a.click()
    URL.revokeObjectURL(url)
  }, [results])

  const handleSync = useCallback(async () => {
    setSyncing(true)
    try {
      await refresh()
    } finally {
      setSyncing(false)
    }
  }, [refresh])

  const handleRangePreset = useCallback((preset: RangePreset) => {
    setRangePreset(preset)
    const dates = getPresetDates(preset)
    if (dates) {
      setStartDate(dates.start)
      setEndDate(dates.end)
    }
  }, [])

  const handleBacktestBestParams = useCallback(() => {
    if (!optResults) return
    setValidationError(null)
    const startTs = Math.floor(new Date(startDate + 'T00:00:00Z').getTime() / 1000)
    const endTs = Math.floor(new Date(endDate + 'T23:59:59Z').getTime() / 1000)
    if (isNaN(startTs) || isNaN(endTs) || startTs >= endTs) return

    const extraMarkets = extractMarketsFromCode(codeRef.current)
    run({
      strategy: 'custom',
      code: codeRef.current,
      market,
      markets: extraMarkets.length > 0 ? [market, ...extraMarkets] : undefined,
      resolution_s: RESOLUTIONS[resolution] || 3600,
      start_ts: startTs,
      end_ts: endTs,
      initial_capital: capital,
      fee_rate: Math.max(feeRate, 0),
      params: optResults.best_params,
      margin_tracking: marginTracking,
    })
  }, [optResults, run, market, resolution, startDate, endDate, capital, feeRate, marginTracking])

  /* ── keyboard shortcuts ────────────────────────────── */

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault()
        handleSave()
      }
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault()
        handleRun()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [handleSave, handleRun])

  /* ── style helpers ─────────────────────────────────── */

  /* ── computed venue / market sets ───────────────────── */

  const validVenues = useMemo(() => {
    if (!stratProfile) return EXECUTION_VENUES.map(v => v.id)

    if (stratProfile.venueRequirement === 'specific' && stratProfile.venues.length > 0) {
      // Strategy requires specific venues — those are the only options
      return stratProfile.venues
    }

    // Filter venues that have data for the selected market
    return EXECUTION_VENUES
      .filter(v => {
        const status = venueDataStatus[v.id]
        if (!status) return true // no data check yet, allow
        // Venue should have at least candle/price data
        return true // always valid — synthetic depth handles missing orderbook
      })
      .map(v => v.id)
  }, [stratProfile, venueDataStatus])

  const validMarkets = useMemo(() => {
    if (!stratProfile || stratProfile.markets.length === 0) {
      return availableMarkets
    }
    // Strategy declares specific markets — only show those + available ones
    const required = [market, ...stratProfile.markets]
    return availableMarkets.length > 0 ? availableMarkets : required
  }, [stratProfile, availableMarkets, market])

  const inputClass = 'w-full bg-void border border-border text-terminal text-xs px-2.5 py-2 focus:border-amber/50 focus:outline-none transition-colors'
  const labelClass = 'block text-[10px] text-ghost tracking-[0.15em] mb-1.5'

  return (
    <div className="space-y-4">
      {/* ── header ─────────────────────────────────────── */}
      <div className="flex items-baseline gap-4 flex-wrap">
        <h1 className="font-[var(--font-display)] text-2xl text-white/90 italic">Strategy Lab</h1>
        <span className="text-[10px] text-ghost tracking-[0.2em]">
          // {currentName ? currentName.toUpperCase() : 'UNSAVED'}
          {dirty && <span className="text-amber ml-1">*</span>}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setShowHistory(!showHistory)}
            className={`px-2.5 py-1 text-[10px] tracking-[0.1em] border transition-all ${
              showHistory ? 'border-amber/50 text-amber' : 'border-border text-ghost hover:text-terminal'
            }`}
          >
            HISTORY ({runHistory.length})
          </button>
          <select
            onChange={(e) => handleTemplateSelect(e.target.value)}
            value=""
            className="bg-void border border-border text-ghost text-[11px] px-2 py-1.5 focus:border-amber/50 focus:outline-none"
          >
            <option value="" disabled>Start from template...</option>
            <optgroup label="Basic">
              {Object.entries(TEMPLATES).filter(([, t]) => t.category === 'basic').map(([key, tpl]) => (
                <option key={key} value={key}>{tpl.label}</option>
              ))}
            </optgroup>
            <optgroup label="Trend Following">
              {Object.entries(TEMPLATES).filter(([, t]) => t.category === 'trend').map(([key, tpl]) => (
                <option key={key} value={key}>{tpl.label}</option>
              ))}
            </optgroup>
            <optgroup label="Mean Reversion">
              {Object.entries(TEMPLATES).filter(([, t]) => t.category === 'mean-rev').map(([key, tpl]) => (
                <option key={key} value={key}>{tpl.label}</option>
              ))}
            </optgroup>
            <optgroup label="Solana Native">
              {Object.entries(TEMPLATES).filter(([, t]) => t.category === 'solana').map(([key, tpl]) => (
                <option key={key} value={key}>{tpl.label}</option>
              ))}
            </optgroup>
            <optgroup label="Advanced (v2 API)">
              {Object.entries(TEMPLATES).filter(([, t]) => t.category === 'advanced').map(([key, tpl]) => (
                <option key={key} value={key}>{tpl.label}</option>
              ))}
            </optgroup>
          </select>
        </div>
      </div>

      {/* ── run history panel ──────────────────────────── */}
      {showHistory && runHistory.length > 0 && (
        <div className="border border-border bg-surface/60 backdrop-blur" style={{ animation: 'fadeUp 0.3s ease' }}>
          <div className="px-3 py-2 border-b border-border flex items-center gap-2">
            <span className="w-2 h-2 bg-amber/60" />
            <span className="text-[10px] text-ghost tracking-[0.2em]">RUN.HISTORY</span>
            <span className="text-[10px] text-ghost/40 ml-auto">{runHistory.length} runs this session</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-[11px] font-mono">
              <thead>
                <tr className="text-ghost/60 text-[9px] tracking-wider">
                  <th className="text-left px-3 py-1.5">STRATEGY</th>
                  <th className="text-left px-3 py-1.5">MARKET</th>
                  <th className="text-left px-3 py-1.5">TF</th>
                  <th className="text-right px-3 py-1.5">PNL</th>
                  <th className="text-right px-3 py-1.5">SHARPE</th>
                  <th className="text-right px-3 py-1.5">TRADES</th>
                  <th className="text-right px-3 py-1.5">TIME</th>
                </tr>
              </thead>
              <tbody>
                {runHistory.map((r) => (
                  <tr key={r.id} className="border-t border-border/50 hover:bg-amber-glow transition-colors">
                    <td className="px-3 py-1.5 text-terminal">{r.strategy}</td>
                    <td className="px-3 py-1.5 text-ghost">{r.market}</td>
                    <td className="px-3 py-1.5 text-ghost">{r.resolution}</td>
                    <td className={`px-3 py-1.5 text-right ${r.pnl >= 0 ? 'text-gain' : 'text-loss'}`}>
                      {r.pnl >= 0 ? '+' : ''}{r.pnl.toFixed(2)}
                    </td>
                    <td className="px-3 py-1.5 text-right text-terminal">{r.sharpe.toFixed(2)}</td>
                    <td className="px-3 py-1.5 text-right text-ghost">{r.trades}</td>
                    <td className="px-3 py-1.5 text-right text-ghost/50">
                      {new Date(r.ts).toLocaleTimeString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── strategy tabs ──────────────────────────────── */}
      <div className="flex items-center gap-0.5 overflow-x-auto pb-1">
        <button
          onClick={() => {
            if (dirty && !window.confirm('You have unsaved changes. Discard?')) return
            setCurrentName(null)
            setCode(TEMPLATES.blank.code)
            setDirty(false)
            setActiveTemplateKey(null)
            setValidationError(null)
            setStratProfile(detectStrategyProfile(TEMPLATES.blank.code))
          }}
          className={`px-3 py-1.5 text-[10px] tracking-[0.12em] border transition-all whitespace-nowrap ${
            currentName === null && !activeTemplateKey
              ? 'border-amber/50 bg-amber-glow text-amber'
              : 'border-border text-ghost hover:text-terminal hover:border-border-bright'
          }`}
        >
          + NEW
        </button>
        <button
          onClick={handleSync}
          disabled={syncing}
          className="px-2.5 py-1.5 text-[10px] tracking-[0.12em] border border-border text-ghost hover:text-amber hover:border-amber/30 transition-all whitespace-nowrap disabled:opacity-40"
          title="Sync strategies from strategies/user/ folder"
        >
          {syncing ? 'SYNCING...' : 'SYNC'}
        </button>
        {savedStrategies.map((s) => (
          <button
            key={s.name}
            onClick={() => handleLoadStrategy(s.name)}
            className={`group px-3 py-1.5 text-[10px] tracking-[0.12em] border transition-all whitespace-nowrap flex items-center gap-2 ${
              currentName === s.name
                ? 'border-amber/50 bg-amber-glow text-amber'
                : 'border-border text-ghost hover:text-terminal hover:border-border-bright'
            }`}
          >
            {s.name}
            {currentName === s.name && dirty && <span className="text-amber">*</span>}
            <span
              onClick={(e) => handleDeleteStrategy(s.name, e)}
              className="opacity-0 group-hover:opacity-60 hover:!opacity-100 text-loss cursor-pointer"
            >
              x
            </span>
          </button>
        ))}
      </div>

      {/* ── main split layout ──────────────────────────── */}
      <div className="flex gap-4" style={{ minHeight: 'calc(100vh - 180px)' }}>
        {/* LEFT — editor */}
        <div className="flex flex-col border border-border bg-surface/60 backdrop-blur" style={{ flex: '0 0 55%', minWidth: 0 }}>
          {/* editor toolbar */}
          <div className="px-3 py-2 border-b border-border flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 bg-amber/60" />
              <span className="text-[10px] text-ghost tracking-[0.2em]">EDITOR</span>
              <span className="text-[10px] text-ghost/40 ml-2">python</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleValidate}
                className="px-2.5 py-1 text-[10px] tracking-[0.1em] border border-border text-ghost hover:text-terminal hover:border-border-bright transition-all"
              >
                VALIDATE
              </button>
              <button
                onClick={handleSave}
                disabled={savingStrategy}
                className="px-2.5 py-1 text-[10px] tracking-[0.1em] border border-border text-ghost hover:text-amber hover:border-amber/30 transition-all disabled:opacity-40"
                title="Ctrl+S"
              >
                {savingStrategy ? 'SAVING...' : 'SAVE'}
              </button>
            </div>
          </div>

          {/* Monaco editor */}
          <div className="flex-1 min-h-0">
            <CodeEditor value={code} onChange={handleCodeChange} />
          </div>

          {/* validation errors */}
          {validationError && (
            <div className="px-3 py-2 border-t border-loss/30 bg-loss/5 text-loss text-[11px] font-mono">
              <span className="text-loss/60 mr-2">[ERR]</span>{validationError}
            </div>
          )}
        </div>

        {/* RIGHT — config + results */}
        <div className="flex-1 min-w-0 flex flex-col gap-4 overflow-y-auto" style={{ maxHeight: 'calc(100vh - 140px)' }}>
          {/* config panel */}
          <div className="border border-border bg-surface/60 backdrop-blur">
            <div className="px-3 py-2 border-b border-border flex items-center gap-2">
              <span className="w-2 h-2 bg-amber/60" />
              <span className="text-[10px] text-ghost tracking-[0.2em]">CONFIG.PARAMS</span>
            </div>

            {/* Strategy profile detection panel */}
            {stratProfile && stratProfile.type !== 'single' && (
              <div className="mx-3 mt-3 border border-amber/30 bg-amber/5 p-3 space-y-2" style={{ animation: 'fadeUp 0.3s ease' }}>
                <div className="text-[9px] px-2 py-1 border border-border/50 text-ghost/60 inline-block">
                  {stratProfile.type === 'multi_venue' ? 'MULTI-VENUE' :
                   stratProfile.type === 'multi_market' ? 'MULTI-MARKET' :
                   stratProfile.type === 'cross_venue_multi_market' ? 'CROSS-VENUE MULTI-MARKET' : ''}
                  {stratProfile.venues.length > 0 && ` — ${stratProfile.venues.join(', ').toUpperCase()}`}
                  {stratProfile.markets.length > 0 && ` — ${stratProfile.markets.join(', ')}`}
                </div>

                {stratProfile.usesAllVenues && (
                  <div className="text-[9px] text-ghost">
                    Uses cross-venue funding data (all downloaded venues)
                  </div>
                )}

                {/* Data requirement warnings */}
                {stratProfile.needsFunding && (
                  <div className="text-[9px] text-amber/70">Requires funding rate data -- download in Data Explorer</div>
                )}
                {stratProfile.needsBorrowRate && (
                  <div className="text-[9px] text-amber/70">Requires borrow rate data (Jupiter)</div>
                )}
                {stratProfile.needsOracle && (
                  <div className="text-[9px] text-amber/70">Uses oracle price (Drift only)</div>
                )}
                {stratProfile.needsOrderbook && (
                  <div className="text-[9px] text-amber/70">Requires orderbook snapshots</div>
                )}
              </div>
            )}

            <div className="p-3 grid grid-cols-2 gap-3">
              <div className="col-span-2 bg-panel/80 border border-border/50 px-3 py-2">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[10px] text-amber tracking-wider font-medium">MARKETS</span>
                </div>
                <p className="text-[10px] text-terminal/70 leading-relaxed">
                  Define markets in your strategy code. The primary market is passed via <code className="text-amber/70 bg-void/50 px-1">candle.market</code>.
                  Access additional markets with <code className="text-amber/70 bg-void/50 px-1">ctx.get_candles("BTC-PERP")</code>.
                  Default: <span className="text-white/80">SOL-PERP</span>. Set in config below.
                </p>
                <div className="mt-2 flex items-center gap-2">
                  <label className="text-[9px] text-ghost tracking-wider">PRIMARY</label>
                  <select value={market} onChange={(e) => setMarket(e.target.value)}
                    className="bg-void border border-border text-[11px] text-terminal px-2 py-0.5 w-36">
                    {(validMarkets.length > 0 ? validMarkets : availableMarkets.length > 0 ? availableMarkets : [...PERP_MARKETS, ...SPOT_MARKETS]).map(m =>
                      <option key={m} value={m}>{m}</option>
                    )}
                  </select>
                </div>
                {/* Single-venue strategy: show venue selector */}
                {(!stratProfile || stratProfile.type === 'single' || stratProfile.type === 'multi_market') && (
                  <div className="mt-2 flex items-center gap-2">
                    <label className="text-[9px] text-ghost tracking-wider">VENUE</label>
                    <select value={venue} onChange={(e) => {
                      setVenue(e.target.value)
                      // Auto-select matching fee preset
                      const presetMap: Record<string, string> = {
                        'drift': 'drift_taker',
                        'hyperliquid': 'hl_taker',
                        'binance': 'binance_taker',
                        'okx': 'okx_taker',
                        'bybit': 'bybit_taker',
                      }
                      if (presetMap[e.target.value]) {
                        setFeePreset(presetMap[e.target.value])
                        setFeeRate(FEE_PRESETS[presetMap[e.target.value]].rate)
                      }
                    }}
                      className="bg-void border border-border text-[11px] text-terminal px-2 py-0.5 w-36">
                      <option value="default">Default</option>
                      <option value="drift">Drift</option>
                      <option value="hyperliquid">Hyperliquid</option>
                      <option value="binance">Binance</option>
                      <option value="okx">OKX</option>
                      <option value="bybit">Bybit</option>
                    </select>
                  </div>
                )}

                {/* Multi-venue strategy: show per-venue fee info */}
                {stratProfile && (stratProfile.type === 'multi_venue' || stratProfile.type === 'cross_venue_multi_market') && (
                  <div className="mt-2 p-2 border border-border bg-surface/30">
                    <div className="text-[9px] text-ghost tracking-wider mb-1">VENUE FEES (per venue config)</div>
                    {(stratProfile.venues.length > 0 ? stratProfile.venues : ['drift', 'hyperliquid']).map(v => {
                      const info = getVenue(v)
                      return (
                        <div key={v} className="text-[9px] text-ghost/50">
                          {v.toUpperCase()}: {info?.takerFee || '?'} taker / {info?.fillModel || 'default'}
                        </div>
                      )
                    })}
                    <div className="text-[8px] text-ghost/50 mt-1">Multi-venue strategies use per-venue fee configs automatically</div>
                  </div>
                )}
              </div>
              <div>
                <label className={labelClass}>TIMEFRAME</label>
                <select value={resolution} onChange={(e) => setResolution(e.target.value)} className={inputClass}>
                  {Object.keys(RESOLUTIONS).map(r => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
              <div>
                <label className={labelClass}>CAPITAL</label>
                <input type="number" value={capital} onChange={(e) => setCapital(+e.target.value)} className={inputClass} />
              </div>
              {/* Execution Venue */}
              <div>
                <label className="text-[9px] text-ghost tracking-wider">EXECUTION VENUE</label>
                {stratProfile && stratProfile.venueRequirement === 'specific' && stratProfile.venues.length > 1 ? (
                  // Multi-venue: show all required venues (auto-detected, read-only)
                  <div className="flex flex-wrap gap-1 mt-1">
                    {stratProfile.venues.map(v => {
                      const info = getVenue(v)
                      const status = venueDataStatus[v]
                      const hasData = status?.has_candles || status?.has_funding
                      return (
                        <span key={v} className="px-2 py-1 text-[10px] border" style={{ borderColor: info?.color || '#666', color: info?.color || '#9ca3af' }}>
                          {info?.label || v}
                          {status && (
                            <span className={`ml-1 text-[8px] ${hasData ? 'text-green-500' : 'text-red-400'}`}>
                              {hasData ? '\u25CF' : '\u25CB'}
                            </span>
                          )}
                        </span>
                      )
                    })}
                  </div>
                ) : (
                  // Single venue: dropdown with valid options
                  <select
                    value={venue}
                    onChange={(e) => {
                      const v = e.target.value
                      setVenue(v)
                      const info = getVenue(v)
                      if (info) {
                        const bps = parseFloat(info.takerFee)
                        if (!isNaN(bps)) setFeeRate(bps / 10000)
                      }
                    }}
                    className={inputClass}
                  >
                    <optgroup label="DEX">
                      {EXECUTION_VENUES.filter(v => v.type === 'dex' && validVenues.includes(v.id)).map(v => {
                        const status = venueDataStatus[v.id]
                        const indicator = status ? (status.has_candles || status.has_funding ? ' \u25CF' : ' \u25CB') : ''
                        return <option key={v.id} value={v.id}>{v.label}{indicator}</option>
                      })}
                    </optgroup>
                    <optgroup label="CEX">
                      {EXECUTION_VENUES.filter(v => v.type === 'cex' && validVenues.includes(v.id)).map(v => {
                        const status = venueDataStatus[v.id]
                        const indicator = status ? (status.has_candles || status.has_funding ? ' \u25CF' : ' \u25CB') : ''
                        return <option key={v.id} value={v.id}>{v.label}{indicator}</option>
                      })}
                    </optgroup>
                  </select>
                )}
              </div>
              {(() => {
                const venueInfo = getVenue(venue)
                if (!venueInfo) return null
                return (
                  <div className="col-span-2 p-2 border border-border/30 bg-panel/50 text-[9px]">
                    <div className="text-ghost/60 mb-1">EXECUTION PROFILE</div>
                    <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-ghost/40">
                      <span>Fill Model:</span><span className="text-ghost/70">{venueInfo.fillModel}</span>
                      <span>Taker Fee:</span><span className="text-ghost/70">{venueInfo.takerFee}</span>
                      <span>Maker Fee:</span><span className="text-ghost/70">{venueInfo.makerFee}</span>
                      <span>Latency:</span><span className="text-ghost/70">{venueInfo.latency}</span>
                      <span>Depth:</span><span className="text-ghost/70">{venueInfo.dataSource}</span>
                      <span>Funding:</span><span className="text-ghost/70">{venueInfo.fundingType === 'borrow' ? 'Borrow fees (continuous)' : `Funding rates (${venueInfo.fundingType})`}</span>
                      <span>{venue === 'jupiter' ? 'Borrow Rates:' : 'Funding:'}</span>
                      <span className={venueDataStatus[venue]?.has_funding ? 'text-green-400' : 'text-amber/60'}>
                        {venueDataStatus[venue]?.has_funding
                          ? (venue === 'jupiter' ? 'Borrow rate data available' : 'Funding data available')
                          : (venue === 'jupiter' ? 'No data — download with Jupiter selected in Data Explorer' : 'No data — download from Data Explorer')}
                      </span>
                      {venue !== 'jupiter' && (
                        <>
                          <span>Orderbook:</span>
                          <span className={venueDataStatus[venue]?.has_orderbook ? 'text-green-400' : 'text-ghost/50'}>
                            {venueDataStatus[venue]?.has_orderbook
                              ? 'Real orderbook data'
                              : (venueInfo.type === 'cex' ? 'Synthetic depth (set FLINT_TARDIS_API_KEY for real data)' : 'Synthetic depth')}
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                )
              })()}
              <details className="col-span-2">
                <summary className="text-[9px] text-ghost/40 cursor-pointer hover:text-ghost/60">
                  Advanced: Override fee rate manually
                </summary>
                <div className="mt-2">
                  <select value={feePreset} onChange={(e) => setFeePreset(e.target.value)} className={inputClass}>
                    <optgroup label="Drift Protocol">
                      {Object.entries(FEE_PRESETS).filter(([,p]) => p.venue === 'drift').map(([key, p]) => (
                        <option key={key} value={key}>{p.label}</option>
                      ))}
                    </optgroup>
                    <optgroup label="Hyperliquid">
                      {Object.entries(FEE_PRESETS).filter(([,p]) => p.venue === 'hyperliquid').map(([key, p]) => (
                        <option key={key} value={key}>{p.label}</option>
                      ))}
                    </optgroup>
                    <optgroup label="Binance Futures">
                      {Object.entries(FEE_PRESETS).filter(([,p]) => p.venue === 'binance').map(([key, p]) => (
                        <option key={key} value={key}>{p.label}</option>
                      ))}
                    </optgroup>
                    <optgroup label="OKX">
                      {Object.entries(FEE_PRESETS).filter(([,p]) => p.venue === 'okx').map(([key, p]) => (
                        <option key={key} value={key}>{p.label}</option>
                      ))}
                    </optgroup>
                    <optgroup label="Bybit">
                      {Object.entries(FEE_PRESETS).filter(([,p]) => p.venue === 'bybit').map(([key, p]) => (
                        <option key={key} value={key}>{p.label}</option>
                      ))}
                    </optgroup>
                    <optgroup label="Jupiter Perps">
                      {Object.entries(FEE_PRESETS).filter(([,p]) => p.venue === 'jupiter').map(([key, p]) => (
                        <option key={key} value={key}>{p.label}</option>
                      ))}
                    </optgroup>
                    <optgroup label="Generic">
                      {Object.entries(FEE_PRESETS).filter(([,p]) => p.venue === 'generic').map(([key, p]) => (
                        <option key={key} value={key}>{p.label}</option>
                      ))}
                    </optgroup>
                  </select>
                </div>
              </details>
              <div className="col-span-2 flex items-center gap-4 bg-panel/80 border border-border/50 px-3 py-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={marginTracking}
                    onChange={(e) => setMarginTracking(e.target.checked)}
                    className="accent-amber"
                  />
                  <span className="text-[10px] text-ghost tracking-wider">MARGIN TRACKING</span>
                </label>
                {marginTracking && (
                  <span className="text-[9px] text-amber/60">Enforces per-venue leverage limits + liquidations</span>
                )}
              </div>
              <div className="col-span-2">
                <label className={labelClass}>DATE RANGE</label>
                <div className="flex gap-1 mb-2">
                  {(['1M', '3M', '6M', '1Y', '3Y', 'CUSTOM'] as RangePreset[]).map(p => (
                    <button
                      key={p}
                      onClick={() => handleRangePreset(p)}
                      className={`px-2 py-1 text-[9px] tracking-[0.1em] border transition-all ${
                        rangePreset === p
                          ? 'border-amber/50 bg-amber-glow text-amber'
                          : 'border-border text-ghost hover:text-terminal hover:border-border-bright'
                      }`}
                    >
                      {p}
                    </button>
                  ))}
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <input
                    type="date"
                    value={startDate}
                    onChange={(e) => { setStartDate(e.target.value); setRangePreset('CUSTOM') }}
                    className={`${inputClass} ${configError && configError.includes('date') ? 'border-loss/50' : ''}`}
                  />
                  <input
                    type="date"
                    value={endDate}
                    onChange={(e) => { setEndDate(e.target.value); setRangePreset('CUSTOM') }}
                    className={`${inputClass} ${configError && configError.includes('date') ? 'border-loss/50' : ''}`}
                  />
                </div>
              </div>
            </div>

            {/* ── status indicator: validation + data check + run readiness ── */}
            <div className="mx-3 mb-2 space-y-1.5">
              {/* config validation error */}
              {configError && (
                <div className="px-2.5 py-1.5 text-[10px] tracking-wider border border-loss/20 bg-loss/5 text-loss/80">
                  {configError}
                </div>
              )}

              {/* data check result */}
              {!configError && dataCheckLoading && (
                <div className="px-2.5 py-1.5 text-[10px] tracking-wider border border-border text-ghost/50">
                  Checking data availability...
                </div>
              )}
              {/* Multi-market data check */}
              {!configError && !dataCheckLoading && multiMarketCheck && (
                <div className={`px-2.5 py-1.5 text-[10px] tracking-wider border ${
                  multiMarketCheck.ready
                    ? 'border-gain/20 bg-gain/5 text-gain/80'
                    : 'border-loss/20 bg-loss/5 text-loss/80'
                }`}>
                  {multiMarketCheck.ready ? (
                    <>{Object.keys(multiMarketCheck.markets).length} markets ready</>
                  ) : (
                    <div className="space-y-1">
                      <div>Missing data for: <span className="text-loss font-semibold">{multiMarketCheck.missing.join(', ')}</span></div>
                      <div className="text-ghost/60">Go to <span className="text-amber">Data Explorer</span> and download these markets for your date range before running.</div>
                    </div>
                  )}
                  <div className="mt-1 space-y-0.5">
                    {Object.entries(multiMarketCheck.markets).map(([m, info]) => (
                      <div key={m} className="flex items-center gap-2 text-[9px]">
                        <span className={`w-1.5 h-1.5 ${info.covers_range ? 'bg-gain' : 'bg-loss'}`} />
                        <span className="text-ghost">{m}</span>
                        <span className="text-ghost/40">
                          {info.covers_range
                            ? `${info.candle_count.toLocaleString()} candles`
                            : info.has_data ? `partial (${info.candle_count})` : 'no data'}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {/* Single-market data check (when no extra markets) */}
              {!configError && !dataCheckLoading && !multiMarketCheck && dataCheck && (
                <div className={`px-2.5 py-1.5 text-[10px] tracking-wider border ${
                  dataCheck.covers_range
                    ? 'border-gain/20 bg-gain/5 text-gain/80'
                    : 'border-loss/30 bg-loss/5 text-loss/80'
                }`}>
                  {dataCheck.covers_range ? (
                    <>{dataCheck.candle_count.toLocaleString()} candles ready — instant backtest</>
                  ) : dataCheck.has_data ? (
                    <>
                      DATA.GAP — only {dataCheck.candle_count.toLocaleString()} candles available
                      {dataCheck.first_ts && dataCheck.last_ts && (
                        <> ({new Date(dataCheck.first_ts * 1000).toLocaleDateString('en-US', {month:'short', day:'numeric', year:'2-digit'})} → {new Date(dataCheck.last_ts * 1000).toLocaleDateString('en-US', {month:'short', day:'numeric', year:'2-digit'})})</>
                      )}
                      . Download the full range from the Data tab first.
                    </>
                  ) : (
                    <>NO.DATA — download {market} from the Data tab before backtesting</>
                  )}
                </div>
              )}

              {/* template hint */}
              {activeTemplateKey && TEMPLATES[activeTemplateKey]?.hint && (
                <div className="px-2.5 py-1.5 text-[10px] text-ghost/50 border border-border/30 tracking-wider">
                  {TEMPLATES[activeTemplateKey].hint}
                </div>
              )}
            </div>

            <div className="px-3 pb-3">
              {status === 'running' && progress ? (
                /* ── progress bar ─────────────────────── */
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-[10px] tracking-wider">
                    <span className={progress.phase === 's3' ? 'text-amber animate-pulse' : 'text-amber'}>
                      {progress.phase === 's3' ? 'DOWNLOADING' :
                       progress.phase === 'cached' ? 'CACHING' :
                       progress.phase === 'backtest' ? 'BACKTESTING' :
                       progress.phase === 'tearsheet' ? 'ANALYZING' :
                       progress.phase.toUpperCase()}
                    </span>
                    <span className="text-ghost/60">
                      {progress.elapsed_s.toFixed(1)}s
                      {progress.candles > 0 && ` · ${progress.candles.toLocaleString()} candles`}
                    </span>
                  </div>
                  <div className="w-full h-2.5 bg-border overflow-hidden">
                    <div
                      className={`h-full transition-all duration-300 ease-out ${
                        progress.phase === 's3' ? 'bg-amber/80' : 'bg-amber'
                      }`}
                      style={{ width: `${progress.pct}%` }}
                    />
                  </div>
                  <div className="text-[10px] text-ghost/60 text-center tracking-wider">
                    {progress.detail}
                  </div>
                  {progress.pct > 0 && progress.pct < 100 && (
                    <div className="text-[9px] text-ghost/30 text-center">
                      {progress.pct}%
                    </div>
                  )}
                </div>
              ) : optStatus === 'running' && optProgress ? (
                /* ── optimization progress ─────────────── */
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-[10px] tracking-wider">
                    <span className="text-amber animate-pulse">OPTIMIZING</span>
                    <span className="text-ghost/60">{optProgress.elapsed_s?.toFixed(1)}s</span>
                  </div>
                  <div className="w-full h-2.5 bg-border overflow-hidden">
                    <div className="h-full bg-amber transition-all duration-500" style={{ width: `${optProgress.pct}%` }} />
                  </div>
                  <div className="text-[10px] text-ghost/60 text-center">{optProgress.detail}</div>
                </div>
              ) : (
                /* ── run + optimize buttons ────────────── */
                <>
                  <div className="flex gap-2">
                    <button
                      onClick={handleRun}
                      disabled={!!configError || (multiMarketCheck !== null && !multiMarketCheck.ready) || (dataCheck !== null && !dataCheck.covers_range)}
                      className={`flex-1 px-4 py-3 text-sm font-semibold tracking-[0.15em] transition-all duration-200 ${
                        configError || (multiMarketCheck && !multiMarketCheck.ready) || (dataCheck && !dataCheck.covers_range)
                          ? 'bg-border text-ghost/40 cursor-not-allowed'
                          : 'bg-amber text-void hover:bg-amber-dim'
                      }`}
                      title={configError || (dataCheck && !dataCheck.covers_range ? 'Download market data from the Data tab first' : (multiMarketCheck && !multiMarketCheck.ready ? 'Download missing market data first' : 'Ctrl+Enter'))}
                    >
                      {configError ? 'FIX CONFIG'
                        : multiMarketCheck && !multiMarketCheck.ready ? 'MISSING DATA'
                        : dataCheck && !dataCheck.covers_range ? 'NEED DATA'
                        : '> RUN_BACKTEST'}
                    </button>
                    <button
                      onClick={handleOptimize}
                      disabled={!!configError || (multiMarketCheck !== null && !multiMarketCheck.ready) || (dataCheck !== null && !dataCheck.covers_range)}
                      className="px-4 py-3 text-[11px] font-semibold tracking-[0.15em] border border-amber/40 text-amber hover:bg-amber/10 disabled:border-border disabled:text-ghost/30 transition-all"
                      title="Optimize strategy parameters"
                    >
                      OPTIMIZE
                    </button>
                  </div>
                  <div className="flex items-center gap-2 mt-2">
                    <select value={optMetric} onChange={e => setOptMetric(e.target.value)}
                      className="bg-void border border-border text-ghost text-[10px] px-2 py-1 flex-1">
                      <option value="sharpe_ratio">Sharpe</option>
                      <option value="total_pnl">PnL</option>
                      <option value="calmar">Calmar</option>
                      <option value="win_rate">Win Rate</option>
                    </select>
                    <input type="number" value={optTrials} onChange={e => setOptTrials(+e.target.value)}
                      className="bg-void border border-border text-ghost text-[10px] px-2 py-1 w-16 text-center" min={5} max={500} />
                    <span className="text-[9px] text-ghost/40">trials</span>
                  </div>
                  <div className="text-[9px] text-ghost/40 mt-1.5 text-center tracking-wider">
                    Ctrl+Enter to run &middot; Ctrl+S to save
                  </div>
                </>
              )}
            </div>
          </div>

          {/* error display */}
          {error && (
            <div className="border border-loss/30 bg-loss/5 px-3 py-2.5 text-loss text-xs">
              <span className="text-loss/60 mr-2">[ERR]</span>{error}
            </div>
          )}

          {/* optimization results (in right panel for visibility) */}
          {optResults && (
            <div className="border border-amber/30 bg-surface/60 p-3" style={{ animation: 'fadeUp 0.3s ease' }}>
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] text-amber tracking-[0.2em]">OPTIMIZATION COMPLETE</span>
                <span className="text-[9px] text-ghost/40">{optResults.n_trials} trials</span>
              </div>
              <div className="mb-2">
                <span className="text-[9px] text-ghost/50">BEST {optResults.metric.toUpperCase()}</span>
                <span className="text-lg text-amber font-semibold ml-2">{optResults.best_value}</span>
              </div>
              <div className="grid grid-cols-2 gap-1.5 mb-2">
                {Object.entries(optResults.best_params).map(([k, v]) => (
                  <div key={k} className="bg-void/50 px-2 py-1 border border-border/30">
                    <div className="text-[8px] text-ghost/40">{k}</div>
                    <div className="text-[11px] text-amber font-mono">{String(v)}</div>
                  </div>
                ))}
              </div>
              {/* Top 3 trials */}
              {optResults.trials.slice(0, 3).map((t, i) => (
                <div key={i} className="flex items-center justify-between text-[10px] py-0.5 border-t border-border/20">
                  <span className="text-ghost/50">#{i+1}</span>
                  <span className="text-terminal">{t.metric_value.toFixed(3)}</span>
                  <span className={t.total_pnl >= 0 ? 'text-gain' : 'text-loss'}>${t.total_pnl >= 0 ? '+' : ''}{t.total_pnl.toFixed(0)}</span>
                  <span className="text-ghost/40">{t.total_trades}t</span>
                </div>
              ))}
              <button
                onClick={handleBacktestBestParams}
                disabled={status === 'running'}
                className="w-full mt-2 px-3 py-2 text-[10px] tracking-[0.15em] bg-amber text-void font-semibold hover:bg-amber-dim transition-all disabled:opacity-40"
              >
                {status === 'running' ? 'RUNNING...' : '> BACKTEST BEST PARAMS'}
              </button>
            </div>
          )}

          {/* optimization error */}
          {optError && (
            <div className="border border-loss/30 bg-loss/5 px-3 py-2 text-loss text-[10px]">
              <span className="text-loss/60 mr-1">[OPT]</span>{optError}
            </div>
          )}

          {/* compact metrics summary (stays in right panel) */}
          {results && (
            <div className="border border-border bg-surface/60 backdrop-blur p-3" style={{ animation: 'fadeUp 0.3s ease' }}>
              <div className="flex items-baseline gap-3 mb-2">
                <span className="font-[var(--font-display)] text-base text-white/90 italic">Results</span>
                <span className="text-[10px] text-amber tracking-wider">{results.strategy_name}</span>
                <div className="ml-auto flex items-center gap-2">
                  <button
                    onClick={() => setShowDeploy(true)}
                    className="px-2.5 py-1 text-[9px] tracking-[0.1em] border border-purple-500/50 text-purple-400 hover:text-purple-300 hover:border-purple-400/70 transition-all"
                  >
                    DEPLOY
                  </button>
                  <button
                    onClick={handleExport}
                    className="px-2 py-0.5 text-[9px] tracking-[0.1em] border border-border text-ghost hover:text-amber hover:border-amber/30 transition-all"
                  >
                    EXPORT
                  </button>
                </div>
              </div>

              {/* key metrics in compact grid */}
              <div className="grid grid-cols-3 gap-2 text-[11px] font-mono">
                <div>
                  <div className="text-[8px] text-ghost/50 tracking-wider">PNL</div>
                  <div className={results.metrics?.total_pnl >= 0 ? 'text-gain' : 'text-loss'}>
                    ${results.metrics?.total_pnl >= 0 ? '+' : ''}{results.metrics?.total_pnl?.toFixed(2)}
                  </div>
                </div>
                <div>
                  <div className="text-[8px] text-ghost/50 tracking-wider">SHARPE</div>
                  <div className="text-terminal">{results.metrics?.sharpe_ratio?.toFixed(2)}</div>
                </div>
                <div>
                  <div className="text-[8px] text-ghost/50 tracking-wider">TRADES</div>
                  <div className="text-terminal">{results.trades?.length || 0}</div>
                </div>
                <div>
                  <div className="text-[8px] text-ghost/50 tracking-wider">WIN.RATE</div>
                  <div className="text-terminal">{(results.metrics?.win_rate * 100)?.toFixed(1)}%</div>
                </div>
                <div>
                  <div className="text-[8px] text-ghost/50 tracking-wider">MAX.DD</div>
                  <div className="text-loss">{(results.metrics?.max_drawdown * 100)?.toFixed(1)}%</div>
                </div>
                <div>
                  <div className="text-[8px] text-ghost/50 tracking-wider">SORTINO</div>
                  <div className="text-terminal">{results.metrics?.sortino_ratio?.toFixed(2)}</div>
                </div>
                <div>
                  <div className="text-[8px] text-ghost/50 tracking-wider">PROFIT.FACTOR</div>
                  <div className={`${results.metrics?.profit_factor >= 1 ? 'text-gain' : 'text-loss'}`}>
                    {results.metrics?.profit_factor === Infinity ? '∞' : results.metrics?.profit_factor?.toFixed(2)}
                  </div>
                </div>
                <div>
                  <div className="text-[8px] text-ghost/50 tracking-wider">AVG.WIN</div>
                  <div className="text-gain">${results.metrics?.avg_win?.toFixed(2)}</div>
                </div>
                <div>
                  <div className="text-[8px] text-ghost/50 tracking-wider">AVG.LOSS</div>
                  <div className="text-loss">${results.metrics?.avg_loss?.toFixed(2)}</div>
                </div>
              </div>

              {/* Monte Carlo summary */}
              {results.monte_carlo && (
                <div className="mt-2 pt-2 border-t border-border/30">
                  <div className="text-[8px] text-ghost/50 tracking-wider mb-1">MONTE CARLO (500 sims)</div>
                  <div className="grid grid-cols-3 gap-2 text-[10px] font-mono">
                    <div>
                      <span className="text-ghost/40">P5 </span>
                      <span className={results.monte_carlo.ci_5 >= 0 ? 'text-gain' : 'text-loss'}>
                        ${results.monte_carlo.ci_5?.toFixed(0)}
                      </span>
                    </div>
                    <div>
                      <span className="text-ghost/40">MED </span>
                      <span className={results.monte_carlo.median >= 0 ? 'text-gain' : 'text-loss'}>
                        ${results.monte_carlo.median?.toFixed(0)}
                      </span>
                    </div>
                    <div>
                      <span className="text-ghost/40">P95 </span>
                      <span className={results.monte_carlo.ci_95 >= 0 ? 'text-gain' : 'text-loss'}>
                        ${results.monte_carlo.ci_95?.toFixed(0)}
                      </span>
                    </div>
                  </div>
                </div>
              )}

              {progress && progress.phase === 'done' && (
                <div className="mt-2 text-[9px] text-ghost/40 tracking-wider">
                  {progress.elapsed_s.toFixed(1)}s &middot; {progress.candles?.toLocaleString()} candles &middot; {results.market} &middot; {resolution}
                </div>
              )}

              {results.trades?.length === 0 && (
                <div className="mt-2 text-[10px] text-amber/70">
                  No trades — extend date range or adjust parameters
                </div>
              )}

              {/* Strategy / data warnings */}
              {results.data_quality?.warnings?.length > 0 && (
                <div className="mt-2 pt-2 border-t border-amber/20 space-y-1">
                  {results.data_quality.warnings.map((w: string, i: number) => (
                    <div key={i} className="text-[10px] text-amber/80 flex items-start gap-1.5">
                      <span className="text-amber/50 shrink-0">!</span>
                      <span>{w.replace(/^\[\d+\]\s*/, '')}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ── FULL-WIDTH RESULTS BELOW ───────────────────── */}
      {results && results.trades?.length > 0 && (
        <div className="space-y-4 mt-4" style={{ animation: 'fadeUp 0.5s ease' }}>

          {/* Equity + Drawdown side by side */}
          <div className="grid grid-cols-2 gap-4">
            <div className="border border-border bg-surface/60 backdrop-blur p-3">
              <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">EQUITY.CURVE</div>
              <EquityCurve equity={results.equity_curve} buyHold={results.buy_hold_equity} height={220} />
            </div>
            <div className="border border-border bg-surface/60 backdrop-blur p-3">
              <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">DRAWDOWN</div>
              <DrawdownChart drawdown={results.drawdown_curve} height={220} />
            </div>
          </div>

          {/* Price chart with trade markers */}
          {results.equity_curve?.length > 0 && (
            <div className="border border-border bg-surface/60 backdrop-blur p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] text-ghost tracking-[0.2em]">PRICE.ACTION + TRADES</span>
                <span className="text-[9px] text-ghost/40">
                  <span className="inline-block w-2 h-2 bg-gain rounded-full mr-1" />entry
                  <span className="inline-block w-2 h-2 bg-loss ml-2 mr-1" />exit (loss)
                </span>
              </div>
              <PriceChart candles={results.buy_hold_equity || results.equity_curve} trades={results.trades} height={260} />
            </div>
          )}

          {/* Metrics + Split side by side */}
          <div className="grid grid-cols-2 gap-4">
            <div className="border border-border bg-surface/60 backdrop-blur p-3">
              <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">ALL.METRICS</div>
              <MetricsCard metrics={results.metrics} />
            </div>
            <div className="border border-border bg-surface/60 backdrop-blur p-3">
              <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">LONG.SHORT.BREAKDOWN</div>
              <SplitMetrics trades={results.trades} />
            </div>
          </div>

          {/* Per-instrument exposure (multi-market strategies) */}
          {results.instrument_exposure && Object.keys(results.instrument_exposure).length > 0 && (
            <div className="border border-border bg-surface/60 backdrop-blur p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] text-ghost tracking-[0.2em]">INSTRUMENT.EXPOSURE</span>
                <span className="text-[9px] text-ghost/40">
                  {Object.keys(results.instrument_exposure).join(' + ')}
                </span>
              </div>
              <InstrumentExposure
                exposure={results.instrument_exposure}
                startTs={results.period_start || results.equity_curve?.[0]?.[0] || 0}
                endTs={results.period_end || results.equity_curve?.[results.equity_curve.length - 1]?.[0] || 0}
                height={180}
              />
            </div>
          )}

          {/* PnL distribution + Exposure side by side */}
          {results.trades?.length > 1 && (
            <div className="grid grid-cols-2 gap-4">
              <div className="border border-border bg-surface/60 backdrop-blur p-3">
                <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">PNL.DISTRIBUTION</div>
                <PnlHistogram trades={results.trades} height={160} />
              </div>
              <div className="border border-border bg-surface/60 backdrop-blur p-3">
                <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">EXPOSURE.TIMELINE</div>
                <ExposureTimeline
                  trades={results.trades}
                  startTs={results.period_start || results.equity_curve?.[0]?.[0] || 0}
                  endTs={results.period_end || results.equity_curve?.[results.equity_curve.length - 1]?.[0] || 0}
                  height={160}
                />
              </div>
            </div>
          )}

          {/* Monthly returns */}
          {results.monthly_returns && Object.keys(results.monthly_returns).length > 0 && (
            <div className="border border-border bg-surface/60 backdrop-blur p-3">
              <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">MONTHLY.RETURNS</div>
              <div className="grid grid-cols-13 gap-px text-[9px] text-center">
                <div className="text-ghost py-1">YR</div>
                {['J','F','M','A','M','J','J','A','S','O','N','D'].map(m =>
                  <div key={m} className="text-ghost/50 py-1">{m}</div>
                )}
                {Object.entries(results.monthly_returns as Record<string, Record<number, number>>).map(([year, months]) => (
                  <div key={year} className="contents">
                    <div className="text-ghost py-1">{year.slice(2)}</div>
                    {Array.from({ length: 12 }, (_, i) => {
                      const ret = months[i + 1]
                      return (
                        <div key={i} className={`py-1 ${
                          ret === undefined ? 'bg-border/30' :
                          ret >= 0 ? 'bg-gain/10 text-gain' : 'bg-loss/10 text-loss'
                        }`}>
                          {ret !== undefined ? `${ret > 0 ? '+' : ''}${ret}` : ''}
                        </div>
                      )
                    })}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Trade log */}
          <div className="border border-border bg-surface/60 backdrop-blur">
            <div className="px-3 py-2 border-b border-border flex items-center justify-between">
              <span className="text-[10px] text-ghost tracking-[0.2em]">TRADE.LOG</span>
              <span className="text-[10px] text-amber/60">{results.trades.length} executions</span>
            </div>
            <div className="p-3">
              <TradeTable trades={results.trades} />
            </div>
          </div>

          <div className="text-[10px] text-ghost/40 text-center tracking-wider pb-4">
            {results.benchmark_label || 'BUY_HOLD'}: {results.buy_hold_return_pct?.toFixed(2)}%
            {results.markets_used?.length > 1 && (
              <> &middot; MARKETS: {results.markets_used.join(', ')}</>
            )}
            &middot; FEE: {FEE_PRESETS[feePreset]?.label || 'custom'}
          </div>
        </div>
      )}

      {/* ── OPTIMIZATION RESULTS ───────────────────── */}
      {optStatus === 'running' && optProgress && (
        <div className="mt-4 border border-amber/30 bg-amber/5 p-4">
          <div className="flex items-center justify-between text-[10px] tracking-wider mb-2">
            <span className="text-amber">OPTIMIZING — {optProgress.detail}</span>
            <span className="text-ghost/50">{optProgress.elapsed_s?.toFixed(1)}s</span>
          </div>
          <div className="w-full h-2 bg-border overflow-hidden">
            <div className="h-full bg-amber transition-all duration-500" style={{ width: `${optProgress.pct}%` }} />
          </div>
        </div>
      )}
      {optError && (
        <div className="mt-4 border border-loss/30 bg-loss/5 px-3 py-2.5 text-loss text-xs">
          <span className="text-loss/60 mr-2">[OPT ERR]</span>{optError}
        </div>
      )}
      {optResults && (
        <div className="mt-4 space-y-4" style={{ animation: 'fadeUp 0.3s ease' }}>
          <div className="border border-amber/30 bg-surface/60 p-4">
            <div className="flex items-baseline gap-3 mb-3">
              <span className="font-[var(--font-display)] text-base text-white/90 italic">Optimization Results</span>
              <span className="text-[10px] text-amber">{optResults.strategy_name}</span>
              <span className="text-[10px] text-ghost">{optResults.n_trials} trials &middot; {optResults.metric}</span>
            </div>
            <div className="mb-3">
              <span className="text-[10px] text-ghost/50 tracking-wider">BEST {optResults.metric.toUpperCase()}: </span>
              <span className="text-lg text-amber font-semibold">{optResults.best_value}</span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-3">
              {Object.entries(optResults.best_params).map(([k, v]) => (
                <div key={k} className="bg-void/50 px-2 py-1.5 border border-border/50">
                  <div className="text-[8px] text-ghost/50 tracking-wider">{k}</div>
                  <div className="text-[12px] text-amber font-mono">{String(v)}</div>
                </div>
              ))}
            </div>
            {optResults.trials.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-[11px] font-mono">
                  <thead>
                    <tr className="text-[9px] text-ghost/50 tracking-wider">
                      <th className="text-left px-2 py-1">#</th>
                      <th className="text-right px-2 py-1">{optResults.metric.toUpperCase()}</th>
                      <th className="text-right px-2 py-1">PNL</th>
                      <th className="text-right px-2 py-1">SHARPE</th>
                      <th className="text-right px-2 py-1">MAX DD</th>
                      <th className="text-right px-2 py-1">WIN%</th>
                      <th className="text-right px-2 py-1">TRADES</th>
                      <th className="text-left px-2 py-1">PARAMS</th>
                    </tr>
                  </thead>
                  <tbody>
                    {optResults.trials.slice(0, 10).map((t, i) => (
                      <tr key={i} className="border-t border-border/20 hover:bg-amber-glow/30">
                        <td className="px-2 py-1 text-ghost/50">{i + 1}</td>
                        <td className="px-2 py-1 text-right text-terminal">{t.metric_value.toFixed(4)}</td>
                        <td className={`px-2 py-1 text-right ${t.total_pnl >= 0 ? 'text-gain' : 'text-loss'}`}>
                          ${t.total_pnl >= 0 ? '+' : ''}{t.total_pnl.toFixed(0)}
                        </td>
                        <td className="px-2 py-1 text-right text-terminal">{t.sharpe_ratio?.toFixed(2) ?? '-'}</td>
                        <td className="px-2 py-1 text-right text-loss">{((t.max_drawdown ?? 0) * 100).toFixed(1)}%</td>
                        <td className="px-2 py-1 text-right text-ghost">{((t.win_rate ?? 0) * 100).toFixed(0)}%</td>
                        <td className="px-2 py-1 text-right text-ghost">{t.total_trades}</td>
                        <td className="px-2 py-1 text-ghost/60 text-[10px]">
                          {Object.entries(t.params).map(([k, v]) => `${k}=${v}`).join(', ')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <button
              onClick={handleBacktestBestParams}
              disabled={status === 'running'}
              className="mt-3 px-6 py-2.5 text-xs tracking-[0.15em] bg-amber text-void font-semibold hover:bg-amber-dim transition-all disabled:opacity-40"
            >
              {status === 'running' ? 'RUNNING...' : '> BACKTEST WITH BEST PARAMS'}
            </button>
          </div>
        </div>
      )}

      {/* ── JOURNAL — past backtest runs ────────────── */}
      <div className="mt-6 mb-4">
        <button
          onClick={() => { setShowJournal(!showJournal); if (!showJournal) refreshJournal() }}
          className={`text-[10px] tracking-[0.15em] px-3 py-1.5 border transition-all ${
            showJournal ? 'border-amber/40 text-amber bg-amber-glow' : 'border-border text-ghost hover:text-terminal'
          }`}
        >
          JOURNAL ({journalRuns.length} runs)
        </button>
      </div>
      {showJournal && journalRuns.length > 0 && (
        <div className="border border-border bg-surface/60 mb-8" style={{ animation: 'fadeUp 0.3s ease' }}>
          <div className="px-4 py-2 border-b border-border flex items-center justify-between">
            <span className="text-[10px] text-ghost tracking-[0.2em]">BACKTEST.JOURNAL</span>
            <span className="text-[10px] text-ghost/40">{journalRuns.length} saved runs</span>
          </div>
          <div className="overflow-x-auto max-h-[300px] overflow-y-auto">
            <table className="w-full text-[11px] font-mono">
              <thead className="sticky top-0 bg-surface">
                <tr className="text-[9px] text-ghost/50 tracking-wider border-b border-border">
                  <th className="text-left px-3 py-1.5">STRATEGY</th>
                  <th className="text-left px-3 py-1.5">MARKET</th>
                  <th className="text-right px-3 py-1.5">PNL</th>
                  <th className="text-right px-3 py-1.5">SHARPE</th>
                  <th className="text-right px-3 py-1.5">TRADES</th>
                  <th className="text-right px-3 py-1.5">MAX DD</th>
                  <th className="text-left px-3 py-1.5">DATE</th>
                  <th className="px-3 py-1.5"></th>
                </tr>
              </thead>
              <tbody>
                {journalRuns.map((r) => (
                  <tr key={r.run_id} className="border-t border-border/20 hover:bg-amber-glow/30">
                    <td className="px-3 py-1.5 text-terminal">{r.strategy_name}</td>
                    <td className="px-3 py-1.5 text-ghost/60">{r.market}</td>
                    <td className={`px-3 py-1.5 text-right ${r.total_pnl >= 0 ? 'text-gain' : 'text-loss'}`}>
                      ${r.total_pnl >= 0 ? '+' : ''}{r.total_pnl?.toFixed(0)}
                    </td>
                    <td className="px-3 py-1.5 text-right text-terminal">{r.sharpe_ratio?.toFixed(2)}</td>
                    <td className="px-3 py-1.5 text-right text-ghost">{r.total_trades}</td>
                    <td className="px-3 py-1.5 text-right text-loss">{(r.max_drawdown * 100)?.toFixed(1)}%</td>
                    <td className="px-3 py-1.5 text-ghost/40 text-[10px]">
                      {new Date(r.created_at * 1000).toLocaleDateString()}
                    </td>
                    <td className="px-3 py-1.5">
                      <button onClick={() => deleteJournalRun(r.run_id)} className="text-ghost/30 hover:text-loss text-[10px]">x</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* results with 0 trades — show equity only, full width */}
      {results && (!results.trades || results.trades.length === 0) && results.equity_curve?.length > 0 && (
        <div className="mt-4 border border-border bg-surface/60 backdrop-blur p-3" style={{ animation: 'fadeUp 0.3s ease' }}>
          <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">EQUITY.CURVE (no trades)</div>
          <EquityCurve equity={results.equity_curve} buyHold={results.buy_hold_equity} height={200} />
        </div>
      )}

      {/* ── Deploy to Paper Trading dialog ────────────── */}
      {showDeploy && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowDeploy(false)}>
          <div className="bg-zinc-800 border border-zinc-700 rounded-xl p-6 w-[420px] space-y-4" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold text-white">Deploy to Paper Trading</h2>
            <p className="text-sm text-zinc-400">Strategy will replay historical data then trade live with simulated execution.</p>

            <div className="space-y-3">
              <div>
                <label className="text-xs text-zinc-400 block mb-1">Capital ($)</label>
                <input type="number" value={deployCapital} onChange={e => setDeployCapital(Number(e.target.value))}
                       className="w-full bg-zinc-900 border border-zinc-700 rounded px-3 py-2 text-white text-sm" />
              </div>
              <div>
                <label className="text-xs text-zinc-400 block mb-1">Max Drawdown (%)</label>
                <input type="number" value={deployMaxDD} onChange={e => setDeployMaxDD(Number(e.target.value))}
                       className="w-full bg-zinc-900 border border-zinc-700 rounded px-3 py-2 text-white text-sm" />
              </div>
              <div>
                <label className="text-xs text-zinc-400 block mb-1">Daily Loss Limit ($)</label>
                <input type="number" value={deployDailyLoss} onChange={e => setDeployDailyLoss(Number(e.target.value))}
                       className="w-full bg-zinc-900 border border-zinc-700 rounded px-3 py-2 text-white text-sm" />
              </div>
              <div>
                <label className="text-xs text-zinc-400 block mb-1">Venue</label>
                <select value={deployVenue} onChange={e => setDeployVenue(e.target.value)}
                        className="w-full bg-zinc-900 border border-zinc-700 rounded px-3 py-2 text-white text-sm">
                  {EXECUTION_VENUES.map(v => (
                    <option key={v.id} value={v.id}>{v.label}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="flex gap-3 pt-2">
              <button onClick={() => setShowDeploy(false)}
                      className="flex-1 px-4 py-2 bg-zinc-700 hover:bg-zinc-600 text-white rounded-lg text-sm">
                Cancel
              </button>
              <button
                disabled={deploying}
                onClick={async () => {
                  setDeploying(true)
                  try {
                    const startTs = Math.floor(new Date(startDate + 'T00:00:00Z').getTime() / 1000)
                    const res = await fetch('/api/v1/paper/deploy', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({
                        strategy_code: code,
                        strategy_name: currentName || results?.strategy_name || 'Custom',
                        strategy_params: {},
                        market: market,
                        initial_capital: deployCapital,
                        replay_start_ts: startTs,
                        venue: deployVenue,
                        risk_config: {
                          max_drawdown_pct: deployMaxDD / 100,
                          daily_loss_limit: deployDailyLoss,
                          max_position_pct: 0.95,
                          liquidation_enabled: true,
                        },
                      }),
                    })
                    if (res.ok) {
                      setShowDeploy(false)
                      window.location.href = '/paper'
                    }
                  } catch (e) {
                    console.error('Deploy failed:', e)
                  } finally {
                    setDeploying(false)
                  }
                }}
                className="flex-1 px-4 py-2 bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white rounded-lg text-sm font-medium"
              >
                {deploying ? 'Deploying...' : 'Deploy'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
