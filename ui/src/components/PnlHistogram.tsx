import { useMemo } from 'react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine } from 'recharts'

interface Trade {
  pnl: number
}

interface Props {
  trades: Trade[]
  height?: number
  bins?: number
}

export default function PnlHistogram({ trades, height = 160, bins = 20 }: Props) {
  const data = useMemo(() => {
    if (!trades?.length) return []

    const pnls = trades.map(t => t.pnl)
    const min = Math.min(...pnls)
    const max = Math.max(...pnls)
    const range = max - min
    if (range === 0) return [{ bin: '0', count: trades.length, center: 0 }]

    const binSize = range / bins
    const buckets: { bin: string; count: number; center: number }[] = []

    for (let i = 0; i < bins; i++) {
      const lo = min + i * binSize
      const hi = lo + binSize
      const center = (lo + hi) / 2
      const count = pnls.filter(p => p >= lo && (i === bins - 1 ? p <= hi : p < hi)).length
      buckets.push({
        bin: center.toFixed(0),
        count,
        center,
      })
    }
    return buckets.filter(b => b.count > 0)
  }, [trades, bins])

  if (!data.length) return null

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
        <XAxis dataKey="bin" tick={{ fill: '#555560', fontSize: 9 }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fill: '#555560', fontSize: 9 }} axisLine={false} tickLine={false} width={30} />
        <ReferenceLine x="0" stroke="#555560" strokeDasharray="3 3" />
        <Tooltip
          formatter={(v: any) => [v, 'Trades']}
          labelFormatter={(l) => `PnL: $${l}`}
          contentStyle={{ background: '#111114', border: '1px solid #2a2a32', borderRadius: 0, fontSize: 11, fontFamily: 'JetBrains Mono' }}
        />
        <Bar dataKey="count" radius={[2, 2, 0, 0]} isAnimationActive={false}>
          {data.map((entry, i) => (
            <Cell key={i} fill={entry.center >= 0 ? '#57c84d' : '#e84d4d'} opacity={0.8} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
