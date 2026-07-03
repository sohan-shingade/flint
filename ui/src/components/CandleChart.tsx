import { useEffect, useRef } from 'react'
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  createSeriesMarkers,
} from 'lightweight-charts'
import type { IChartApi, Time, SeriesMarker } from 'lightweight-charts'

export interface OHLC {
  ts: number
  open: number
  high: number
  low: number
  close: number
  volume?: number
}

export interface TradeMarker {
  ts: number
  side: 'long' | 'short'
  type: 'entry' | 'exit'
  pnl?: number
}

interface Props {
  candles: OHLC[]
  markers?: TradeMarker[]
  showVolume?: boolean
  height?: number
}

const C = {
  bg: '#09090b',
  grid: '#1a1a1f',
  text: '#555560',
  up: '#57c84d',
  down: '#e84d4d',
}

/** lightweight-charts requires strictly ascending, de-duplicated timestamps. */
function clean(candles: OHLC[]): OHLC[] {
  const byTs = new Map<number, OHLC>()
  for (const c of candles) byTs.set(c.ts, c) // last write wins on dup ts
  return [...byTs.values()].sort((a, b) => a.ts - b.ts)
}

function toMarkers(markers: TradeMarker[]): SeriesMarker<Time>[] {
  return [...markers]
    .sort((a, b) => a.ts - b.ts)
    .map((m) => {
      const isEntry = m.type === 'entry'
      const long = m.side === 'long'
      const loss = m.pnl != null && m.pnl < 0
      return {
        time: m.ts as Time,
        position: long ? 'belowBar' : 'aboveBar',
        color: isEntry ? (long ? C.up : C.down) : loss ? C.down : C.up,
        shape: long ? 'arrowUp' : 'arrowDown',
        text: isEntry ? '' : m.pnl != null ? (m.pnl >= 0 ? '+' : '') + m.pnl.toFixed(0) : '',
      } as SeriesMarker<Time>
    })
}

/** Lean candlestick chart (lightweight-charts) with optional volume + trade markers. */
export default function CandleChart({ candles, markers, showVolume = true, height = 260 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    const el = containerRef.current
    const data = clean(candles)
    if (!el || !data.length) return

    if (chartRef.current) {
      chartRef.current.remove()
      chartRef.current = null
    }

    const chart = createChart(el, {
      width: el.clientWidth,
      height,
      layout: { background: { color: C.bg }, textColor: C.text, fontSize: 10 },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      crosshair: { mode: 0 },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderColor: C.grid,
        barSpacing: Math.max(3, Math.min(12, el.clientWidth / data.length)),
      },
      rightPriceScale: { borderColor: C.grid },
    })
    chartRef.current = chart

    const cs = chart.addSeries(CandlestickSeries, {
      upColor: C.up,
      downColor: C.down,
      borderUpColor: C.up,
      borderDownColor: C.down,
      wickUpColor: C.up,
      wickDownColor: C.down,
    })
    cs.setData(
      data.map((c) => ({ time: c.ts as Time, open: c.open, high: c.high, low: c.low, close: c.close })),
    )

    if (showVolume && data.some((c) => c.volume != null)) {
      const vs = chart.addSeries(HistogramSeries, { priceScaleId: 'vol' })
      chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
      vs.setData(
        data.map((c) => ({
          time: c.ts as Time,
          value: c.volume ?? 0,
          color: (c.close >= c.open ? C.up : C.down) + '33',
        })),
      )
    }

    if (markers?.length) {
      createSeriesMarkers(cs, toMarkers(markers))
    }

    chart.timeScale().fitContent()

    const ro = new ResizeObserver(() => {
      if (chartRef.current && el) chartRef.current.applyOptions({ width: el.clientWidth })
    })
    ro.observe(el)

    return () => {
      ro.disconnect()
      if (chartRef.current) {
        chartRef.current.remove()
        chartRef.current = null
      }
    }
  }, [candles, markers, showVolume, height])

  return <div ref={containerRef} style={{ width: '100%' }} />
}
