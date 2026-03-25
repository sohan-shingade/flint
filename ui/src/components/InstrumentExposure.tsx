import { useMemo } from 'react'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, Legend } from 'recharts'

const MARKET_COLORS: Record<string, string> = {
  'SOL-PERP': '#e8a849',
  'BTC-PERP': '#22d3ee',
  'ETH-PERP': '#a78bfa',
  'SOL': '#57c84d',
  'BTC': '#f472b6',
  'ETH': '#818cf8',
}

function getColor(market: string, i: number): string {
  if (MARKET_COLORS[market]) return MARKET_COLORS[market]
  const fallback = ['#fb923c', '#34d399', '#f87171', '#fbbf24', '#38bdf8']
  return fallback[i % fallback.length]
}

interface ExposureEvent {
  ts: number
  direction: number  // +1 long, -1 short, 0 flat
  size: number
  notional: number
}

interface Props {
  exposure: Record<string, ExposureEvent[]>
  startTs: number
  endTs: number
  height?: number
}

export default function InstrumentExposure({ exposure, startTs, endTs, height = 180 }: Props) {
  const { data, markets } = useMemo(() => {
    const mkts = Object.keys(exposure).sort()
    if (!mkts.length || !startTs || !endTs) return { data: [], markets: [] }

    // Collect all timestamps
    const allTs = new Set<number>()
    allTs.add(startTs)
    allTs.add(endTs)
    for (const events of Object.values(exposure)) {
      for (const e of events) allTs.add(e.ts)
    }
    const sortedTs = Array.from(allTs).sort((a, b) => a - b)

    // Build data points: at each timestamp, what's the exposure per market
    const points: Record<string, any>[] = []
    const currentState: Record<string, number> = {}
    for (const m of mkts) currentState[m] = 0

    // Index events by market
    const eventIdx: Record<string, number> = {}
    for (const m of mkts) eventIdx[m] = 0

    for (const ts of sortedTs) {
      // Advance each market's events up to this timestamp
      for (const m of mkts) {
        const events = exposure[m]
        while (eventIdx[m] < events.length && events[eventIdx[m]].ts <= ts) {
          const e = events[eventIdx[m]]
          currentState[m] = e.direction * e.size
          eventIdx[m]++
        }
      }
      const point: any = { ts }
      for (const m of mkts) {
        point[m] = currentState[m]
      }
      points.push(point)
    }

    // Downsample if too many points
    if (points.length > 300) {
      const step = Math.ceil(points.length / 300)
      return {
        data: points.filter((_, i) => i % step === 0 || i === points.length - 1),
        markets: mkts,
      }
    }
    return { data: points, markets: mkts }
  }, [exposure, startTs, endTs])

  if (!data.length || !markets.length) return null

  const fmt = (ts: number) => {
    const d = new Date(ts * 1000)
    return `${d.getUTCMonth() + 1}/${d.getUTCDate()}`
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
        <XAxis dataKey="ts" tickFormatter={fmt} tick={{ fill: '#555560', fontSize: 9 }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fill: '#555560', fontSize: 9 }} axisLine={false} tickLine={false} width={45}
          tickFormatter={(v) => {
            if (v === 0) return '0'
            return v > 0 ? `+${v.toFixed(1)}` : v.toFixed(1)
          }}
        />
        <ReferenceLine y={0} stroke="#555560" strokeDasharray="3 3" />
        <Tooltip
          labelFormatter={(ts) => new Date(Number(ts) * 1000).toLocaleString()}
          contentStyle={{ background: '#111114', border: '1px solid #2a2a32', borderRadius: 0, fontSize: 10, fontFamily: 'JetBrains Mono' }}
          formatter={(v: any, name: any) => {
            const val = Number(v)
            const label = val > 0 ? `LONG ${val.toFixed(2)}` : val < 0 ? `SHORT ${Math.abs(val).toFixed(2)}` : 'FLAT'
            return [label, String(name)]
          }}
        />
        <Legend
          wrapperStyle={{ fontSize: 9, fontFamily: 'JetBrains Mono' }}
          formatter={(value) => <span style={{ color: '#999' }}>{value}</span>}
        />
        {markets.map((m, i) => (
          <Area
            key={m}
            type="stepAfter"
            dataKey={m}
            stroke={getColor(m, i)}
            fill={getColor(m, i)}
            fillOpacity={0.15}
            strokeWidth={1.5}
            isAnimationActive={false}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  )
}
