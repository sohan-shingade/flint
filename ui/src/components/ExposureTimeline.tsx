import { useMemo } from 'react'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'

interface Trade {
  entry_ts: number
  exit_ts: number
  side: string
}

interface Props {
  trades: Trade[]
  startTs: number
  endTs: number
  height?: number
}

export default function ExposureTimeline({ trades, startTs, endTs, height = 100 }: Props) {
  const data = useMemo(() => {
    if (!trades?.length || !startTs || !endTs) return []

    // Build exposure events
    const events: { ts: number; delta: number }[] = []
    for (const t of trades) {
      const dir = t.side === 'long' ? 1 : -1
      events.push({ ts: t.entry_ts, delta: dir })
      events.push({ ts: t.exit_ts, delta: -dir })
    }
    events.sort((a, b) => a.ts - b.ts)

    // Walk through time and build exposure curve
    const points: { ts: number; exposure: number }[] = [{ ts: startTs, exposure: 0 }]
    let exposure = 0
    for (const e of events) {
      if (e.ts < startTs || e.ts > endTs) continue
      points.push({ ts: e.ts, exposure })
      exposure += e.delta
      points.push({ ts: e.ts, exposure })
    }
    points.push({ ts: endTs, exposure })

    // Downsample if too many points
    if (points.length > 200) {
      const step = Math.ceil(points.length / 200)
      return points.filter((_, i) => i % step === 0 || i === points.length - 1)
    }
    return points
  }, [trades, startTs, endTs])

  if (!data.length) return null

  // Compute time-in-market percentage
  const timeInMarket = useMemo(() => {
    let inMarket = 0
    for (const t of trades) {
      inMarket += Math.min(t.exit_ts, endTs) - Math.max(t.entry_ts, startTs)
    }
    const total = endTs - startTs
    return total > 0 ? ((inMarket / total) * 100).toFixed(1) : '0'
  }, [trades, startTs, endTs])

  const fmt = (ts: number) => {
    const d = new Date(ts * 1000)
    return `${d.getUTCMonth() + 1}/${d.getUTCDate()}`
  }

  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <XAxis dataKey="ts" tickFormatter={fmt} tick={{ fill: '#555560', fontSize: 9 }} axisLine={false} tickLine={false} />
          <YAxis domain={[-1.2, 1.2]} ticks={[-1, 0, 1]} tick={{ fill: '#555560', fontSize: 9 }} axisLine={false} tickLine={false} width={30}
            tickFormatter={(v) => v === 1 ? 'LONG' : v === -1 ? 'SHORT' : 'FLAT'}
          />
          <ReferenceLine y={0} stroke="#555560" strokeDasharray="3 3" />
          <Tooltip
            labelFormatter={(ts) => new Date(Number(ts) * 1000).toUTCString()}
            formatter={(v: any) => [Number(v) > 0 ? 'LONG' : Number(v) < 0 ? 'SHORT' : 'FLAT', 'Exposure']}
            contentStyle={{ background: '#111114', border: '1px solid #2a2a32', borderRadius: 0, fontSize: 11, fontFamily: 'JetBrains Mono' }}
          />
          <Area type="stepAfter" dataKey="exposure" stroke="#e8a849" fill="#e8a849" fillOpacity={0.1} strokeWidth={1.5} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
      <div className="text-[9px] text-ghost/50 text-right mt-1">
        TIME_IN_MARKET: {timeInMarket}%
      </div>
    </div>
  )
}
