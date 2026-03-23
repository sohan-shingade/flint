import { useEffect, useRef, useMemo } from 'react'
import {
  createChart,
  CandlestickSeries, LineSeries, HistogramSeries,
} from 'lightweight-charts'
import type { IChartApi, Time } from 'lightweight-charts'

interface Candle { ts: number; open: number; high: number; low: number; close: number; volume: number }

interface FundingPoint { ts: number; rate: number }

interface IndicatorConfig {
  sma: boolean; smaPeriod: number
  ema: boolean; emaPeriod: number
  vwap: boolean
  bb: boolean; bbPeriod: number
  rsi: boolean; rsiPeriod: number
  volume: boolean
  funding: boolean
}

interface Props {
  candles: Candle[]; height?: number; indicators: IndicatorConfig
  fundingRates?: FundingPoint[]
}

const C = {
  bg: '#09090b', grid: '#1a1a1f', text: '#555560',
  up: '#57c84d', down: '#e84d4d',
  sma: '#57c84d', ema: '#8b5cf6', vwap: '#06b6d4', bb: '#e8a849', rsi: '#f59e0b',
}

function computeSMA(closes: number[], p: number): (number | null)[] {
  return closes.map((_, i) => {
    if (i < p - 1) return null
    let s = 0; for (let j = i - p + 1; j <= i; j++) s += closes[j]
    return s / p
  })
}

function computeEMA(closes: number[], p: number): (number | null)[] {
  const m = 2 / (p + 1), r: (number | null)[] = []
  let e = closes[0]
  for (let i = 0; i < closes.length; i++) {
    if (i < p - 1) { e = closes[i]; r.push(null); continue }
    e = closes[i] * m + e * (1 - m)
    r.push(e)
  }
  return r
}

function computeVWAP(candles: Candle[], p: number = 20): (number | null)[] {
  return candles.map((_, i) => {
    if (i < p - 1) return null
    let vS = 0, pvS = 0
    for (let j = i - p + 1; j <= i; j++) { vS += candles[j].volume; pvS += candles[j].close * candles[j].volume }
    return vS > 0 ? pvS / vS : candles[i].close
  })
}

function computeBB(closes: number[], p: number) {
  const u: (number | null)[] = [], l: (number | null)[] = [], m: (number | null)[] = []
  for (let i = 0; i < closes.length; i++) {
    if (i < p - 1) { u.push(null); l.push(null); m.push(null); continue }
    let s = 0; for (let j = i - p + 1; j <= i; j++) s += closes[j]
    const mean = s / p
    let v = 0; for (let j = i - p + 1; j <= i; j++) v += (closes[j] - mean) ** 2
    const std = Math.sqrt(v / p)
    m.push(mean); u.push(mean + 2 * std); l.push(mean - 2 * std)
  }
  return { u, l, m }
}

function computeRSI(closes: number[], p: number): (number | null)[] {
  const r: (number | null)[] = [null]
  for (let i = 1; i < closes.length; i++) {
    if (i < p) { r.push(null); continue }
    let g = 0, lo = 0
    for (let j = i - p + 1; j <= i; j++) { const d = closes[j] - closes[j - 1]; if (d > 0) g += d; else lo -= d }
    r.push(lo === 0 ? 100 : 100 - 100 / (1 + (g / p) / (lo / p)))
  }
  return r
}

function toLD(candles: Candle[], values: (number | null)[]) {
  return candles.map((c, i) => ({ time: c.ts as Time, value: values[i]! })).filter(d => d.value != null)
}

// Stringify indicators config for stable dependency tracking
function indicatorKey(ind: IndicatorConfig): string {
  return JSON.stringify(ind)
}

export default function InteractiveChart({ candles, height = 500, indicators, fundingRates = [] }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const rsiRef = useRef<HTMLDivElement>(null)
  const fundingRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const rsiChartRef = useRef<IChartApi | null>(null)
  const fundingChartRef = useRef<IChartApi | null>(null)
  const legendRef = useRef<HTMLDivElement>(null)

  // Stable indicator key to avoid unnecessary re-renders
  const indKey = useMemo(() => indicatorKey(indicators), [indicators])

  useEffect(() => {
    if (!containerRef.current || !candles.length) return

    // Cleanup previous
    if (chartRef.current) { chartRef.current.remove(); chartRef.current = null }
    if (rsiChartRef.current) { rsiChartRef.current.remove(); rsiChartRef.current = null }
    if (fundingChartRef.current) { fundingChartRef.current.remove(); fundingChartRef.current = null }

    const subCharts = (indicators.rsi ? 1 : 0) + (indicators.funding && fundingRates.length > 0 ? 1 : 0)
    const mainH = height - subCharts * 130
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth, height: mainH,
      layout: { background: { color: C.bg }, textColor: C.text },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      crosshair: { mode: 0 },
      timeScale: {
        timeVisible: true, secondsVisible: false, borderColor: C.grid,
        barSpacing: Math.max(3, Math.min(12, containerRef.current.clientWidth / candles.length)),
      },
      rightPriceScale: { borderColor: C.grid },
    })
    chartRef.current = chart

    // Candlesticks
    const cs = chart.addSeries(CandlestickSeries, {
      upColor: C.up, downColor: C.down,
      borderUpColor: C.up, borderDownColor: C.down,
      wickUpColor: C.up, wickDownColor: C.down,
    })
    cs.setData(candles.map(c => ({ time: c.ts as Time, open: c.open, high: c.high, low: c.low, close: c.close })))

    const closes = candles.map(c => c.close)

    // Volume
    if (indicators.volume) {
      const vs = chart.addSeries(HistogramSeries, { priceScaleId: 'vol' })
      chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })
      vs.setData(candles.map(c => ({
        time: c.ts as Time, value: c.volume,
        color: c.close >= c.open ? C.up + '33' : C.down + '33',
      })))
    }

    // Indicators
    if (indicators.sma) {
      chart.addSeries(LineSeries, { color: C.sma, lineWidth: 1, lineStyle: 2, title: `SMA(${indicators.smaPeriod})` })
        .setData(toLD(candles, computeSMA(closes, indicators.smaPeriod)))
    }
    if (indicators.ema) {
      chart.addSeries(LineSeries, { color: C.ema, lineWidth: 1, title: `EMA(${indicators.emaPeriod})` })
        .setData(toLD(candles, computeEMA(closes, indicators.emaPeriod)))
    }
    if (indicators.vwap) {
      chart.addSeries(LineSeries, { color: C.vwap, lineWidth: 1, lineStyle: 1, title: 'VWAP' })
        .setData(toLD(candles, computeVWAP(candles)))
    }
    if (indicators.bb) {
      chart.addSeries(LineSeries, { color: C.bb, lineWidth: 1, lineStyle: 2, title: 'BB↑' })
        .setData(toLD(candles, computeBB(closes, indicators.bbPeriod).u))
      chart.addSeries(LineSeries, { color: C.bb, lineWidth: 1, lineStyle: 2, title: 'BB↓' })
        .setData(toLD(candles, computeBB(closes, indicators.bbPeriod).l))
      chart.addSeries(LineSeries, { color: C.bb + '66', lineWidth: 1, lineStyle: 3, title: 'BB mid' })
        .setData(toLD(candles, computeBB(closes, indicators.bbPeriod).m))
    }

    // OHLCV legend on crosshair move
    chart.subscribeCrosshairMove(param => {
      if (!legendRef.current) return
      if (!param.time || !param.seriesData) {
        legendRef.current.innerHTML = ''
        return
      }
      const d = param.seriesData.get(cs) as any
      if (!d) { legendRef.current.innerHTML = ''; return }
      const color = d.close >= d.open ? C.up : C.down
      // Find matching candle for volume
      const ts = param.time as number
      const candle = candles.find(c => c.ts === ts)
      const vol = candle ? candle.volume.toLocaleString(undefined, { maximumFractionDigits: 0 }) : ''
      legendRef.current.innerHTML =
        `<span style="color:${C.text}">O</span> <span style="color:${color}">${d.open?.toFixed(2)}</span>` +
        `<span style="color:${C.text}; margin-left:8px">H</span> <span style="color:${color}">${d.high?.toFixed(2)}</span>` +
        `<span style="color:${C.text}; margin-left:8px">L</span> <span style="color:${color}">${d.low?.toFixed(2)}</span>` +
        `<span style="color:${C.text}; margin-left:8px">C</span> <span style="color:${color}">${d.close?.toFixed(2)}</span>` +
        (vol ? `<span style="color:${C.text}; margin-left:12px">V</span> <span style="color:#555560">${vol}</span>` : '')
    })

    chart.timeScale().fitContent()

    // Resize
    const ro = new ResizeObserver(e => { if (e[0]) chart.applyOptions({ width: e[0].contentRect.width }) })
    ro.observe(containerRef.current)

    // RSI sub-chart
    if (indicators.rsi && rsiRef.current) {
      const rsiChart = createChart(rsiRef.current, {
        width: rsiRef.current.clientWidth, height: 100,
        layout: { background: { color: C.bg }, textColor: C.text },
        grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
        crosshair: { mode: 0 },
        timeScale: { timeVisible: true, secondsVisible: false, borderColor: C.grid },
        rightPriceScale: { borderColor: C.grid },
      })
      rsiChartRef.current = rsiChart

      rsiChart.addSeries(LineSeries, { color: C.rsi, lineWidth: 2, title: `RSI(${indicators.rsiPeriod})` })
        .setData(toLD(candles, computeRSI(closes, indicators.rsiPeriod)))
      rsiChart.addSeries(LineSeries, { color: '#e84d4d33', lineWidth: 1, lineStyle: 2 })
        .setData(candles.map(c => ({ time: c.ts as Time, value: 70 })))
      rsiChart.addSeries(LineSeries, { color: '#57c84d33', lineWidth: 1, lineStyle: 2 })
        .setData(candles.map(c => ({ time: c.ts as Time, value: 30 })))

      rsiChart.timeScale().fitContent()

      // Sync time scales
      chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (range && rsiChartRef.current) rsiChartRef.current.timeScale().setVisibleLogicalRange(range)
      })
      rsiChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (range && chartRef.current) chartRef.current.timeScale().setVisibleLogicalRange(range)
      })

      const rsiRo = new ResizeObserver(e => { if (e[0] && rsiChartRef.current) rsiChartRef.current.applyOptions({ width: e[0].contentRect.width }) })
      rsiRo.observe(rsiRef.current)
    }

    // Funding rate sub-chart
    if (indicators.funding && fundingRates.length > 0 && fundingRef.current) {
      const fundingChart = createChart(fundingRef.current, {
        width: fundingRef.current.clientWidth, height: 100,
        layout: { background: { color: C.bg }, textColor: C.text },
        grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
        crosshair: { mode: 0 },
        timeScale: { timeVisible: true, secondsVisible: false, borderColor: C.grid },
        rightPriceScale: { borderColor: C.grid },
      })
      fundingChartRef.current = fundingChart

      // Funding rate as histogram (positive = green, negative = red)
      const fundingSeries = fundingChart.addSeries(HistogramSeries, {
        priceScaleId: 'funding',
        title: 'Funding (bps)',
      })
      fundingSeries.setData(fundingRates.map(r => ({
        time: r.ts as Time,
        value: r.rate * 10000, // convert to bps
        color: r.rate >= 0 ? C.up + 'aa' : C.down + 'aa',
      })))

      // Zero line
      fundingChart.addSeries(LineSeries, { color: '#ffffff15', lineWidth: 1, lineStyle: 2 })
        .setData(fundingRates.map(r => ({ time: r.ts as Time, value: 0 })))

      fundingChart.timeScale().fitContent()

      // Sync time scales with main chart
      chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (range && fundingChartRef.current) fundingChartRef.current.timeScale().setVisibleLogicalRange(range)
      })
      fundingChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (range && chartRef.current) chartRef.current.timeScale().setVisibleLogicalRange(range)
      })

      const fundingRo = new ResizeObserver(e => { if (e[0] && fundingChartRef.current) fundingChartRef.current.applyOptions({ width: e[0].contentRect.width }) })
      fundingRo.observe(fundingRef.current)
    }

    return () => {
      ro.disconnect()
      if (chartRef.current) { chartRef.current.remove(); chartRef.current = null }
      if (rsiChartRef.current) { rsiChartRef.current.remove(); rsiChartRef.current = null }
      if (fundingChartRef.current) { fundingChartRef.current.remove(); fundingChartRef.current = null }
    }
  }, [candles, indKey, height, fundingRates])

  if (!candles.length) return null

  return (
    <div className="border border-border bg-surface/60">
      {/* OHLCV legend */}
      <div ref={legendRef} className="px-3 py-1.5 text-[11px] font-mono h-6"
        style={{ fontFamily: "'JetBrains Mono', monospace" }} />
      <div ref={containerRef} />
      {indicators.rsi && (
        <div className="border-t border-border">
          <div className="text-[9px] text-ghost/40 tracking-wider px-2 py-1">RSI({indicators.rsiPeriod})</div>
          <div ref={rsiRef} />
        </div>
      )}
      {indicators.funding && fundingRates.length > 0 && (
        <div className="border-t border-border">
          <div className="text-[9px] text-amber/40 tracking-wider px-2 py-1">FUNDING RATE (bps)</div>
          <div ref={fundingRef} />
        </div>
      )}
    </div>
  )
}
