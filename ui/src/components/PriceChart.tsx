import { useMemo } from 'react'
import { ComposedChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceDot } from 'recharts'

interface Trade {
  entry_ts: number
  exit_ts: number
  side: string
  entry_price: number
  exit_price: number
  pnl: number
}

interface Props {
  candles: [number, number][]
  trades: Trade[]
  height?: number
}

const MAX_POINTS = 200
const MAX_MARKERS = 40  // limit trade markers to avoid SVG overload

function downsample(data: any[], maxPoints: number) {
  if (data.length <= maxPoints) return data
  const step = Math.ceil(data.length / maxPoints)
  const result = []
  for (let i = 0; i < data.length; i += step) {
    const chunk = data.slice(i, i + step)
    const maxItem = chunk.reduce((a: any, b: any) => Math.abs(a.close) > Math.abs(b.close) ? a : b)
    result.push(maxItem)
  }
  if (result[result.length - 1] !== data[data.length - 1]) result.push(data[data.length - 1])
  return result
}

export default function PriceChart({ candles, trades, height = 220 }: Props) {
  const data = useMemo(() => {
    if (!candles?.length) return []
    const pts = candles.map(([ts, price]) => ({ ts, close: price }))
    return downsample(pts, MAX_POINTS)
  }, [candles])

  // Limit trade markers — show only the N largest trades by absolute PnL
  const visibleTrades = useMemo(() => {
    if (trades.length <= MAX_MARKERS) return trades
    return [...trades]
      .sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl))
      .slice(0, MAX_MARKERS)
  }, [trades])

  if (!data.length) return null

  const fmt = (ts: number) => {
    const d = new Date(ts * 1000)
    return `${d.getUTCMonth() + 1}/${d.getUTCDate()}`
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <XAxis dataKey="ts" tickFormatter={fmt} tick={{ fill: '#555560', fontSize: 9 }} axisLine={false} tickLine={false} />
        <YAxis domain={['auto', 'auto']} tick={{ fill: '#555560', fontSize: 9 }} axisLine={false} tickLine={false} width={60} />
        <Tooltip
          labelFormatter={(ts) => new Date(Number(ts) * 1000).toUTCString()}
          formatter={(v: any) => [`$${Number(v).toFixed(2)}`, 'Price']}
          contentStyle={{ background: '#111114', border: '1px solid #2a2a32', borderRadius: 0, fontSize: 11, fontFamily: 'JetBrains Mono' }}
        />
        <Line type="monotone" dataKey="close" stroke="#e8a849" strokeWidth={1.2} dot={false} isAnimationActive={false} />

        {visibleTrades.map((t, i) => (
          <ReferenceDot
            key={`entry-${i}`}
            x={t.entry_ts}
            y={t.entry_price}
            r={3}
            fill={t.side === 'long' ? '#57c84d' : '#e84d4d'}
            stroke="none"
            ifOverflow="extendDomain"
          />
        ))}
        {visibleTrades.map((t, i) => (
          <ReferenceDot
            key={`exit-${i}`}
            x={t.exit_ts}
            y={t.exit_price}
            r={2}
            fill={t.pnl >= 0 ? '#57c84d' : '#e84d4d'}
            stroke="#111114"
            strokeWidth={1}
            ifOverflow="extendDomain"
          />
        ))}
      </ComposedChart>
    </ResponsiveContainer>
  )
}
