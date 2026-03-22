import { useMemo } from 'react'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'

interface Props {
  equity: [number, number][]
  buyHold?: [number, number][]
  height?: number
}

const TICK = { fill: '#555560', fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }

const tooltipStyle = {
  background: '#111114',
  border: '1px solid #2a2a32',
  borderRadius: 0,
  fontSize: 11,
  fontFamily: "'JetBrains Mono', monospace",
  color: '#a0a0a8',
}

// Downsample to max N points using LTTB-like peak preservation
function downsample(arr: any[], maxPoints: number): any[] {
  if (arr.length <= maxPoints) return arr
  const step = arr.length / maxPoints
  const result: any[] = [arr[0]]
  for (let i = 1; i < maxPoints - 1; i++) {
    const start = Math.floor(i * step)
    const end = Math.floor((i + 1) * step)
    // Pick the point with the max equity value in this bucket (preserves peaks)
    let best = start
    for (let j = start + 1; j < end && j < arr.length; j++) {
      if (Math.abs(arr[j].equity) > Math.abs(arr[best].equity)) best = j
    }
    result.push(arr[best])
  }
  result.push(arr[arr.length - 1])
  return result
}

export default function EquityCurve({ equity, buyHold, height = 300 }: Props) {
  const data = useMemo(() => {
    const full = equity.map(([ts, val], i) => ({
      ts,
      date: new Date(ts * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' }),
      equity: +val.toFixed(2),
      buyHold: buyHold?.[i]?.[1] ? +buyHold[i][1].toFixed(2) : undefined,
    }))
    return downsample(full, 200)
  }, [equity, buyHold])

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data}>
        <CartesianGrid strokeDasharray="2 6" stroke="#1a1a1f" />
        <XAxis dataKey="date" tick={TICK} interval="preserveStartEnd" minTickGap={60} />
        <YAxis tick={TICK} domain={['auto', 'auto']} width={65} />
        <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: '#a0a0a8' }} />
        <Line type="monotone" dataKey="equity" stroke="#e8a849" strokeWidth={1.5} dot={false} name="Strategy" isAnimationActive={false} />
        {buyHold && (
          <Line type="monotone" dataKey="buyHold" stroke="#707078" strokeWidth={1} dot={false} strokeDasharray="4 4" name="Buy & Hold" isAnimationActive={false} />
        )}
      </LineChart>
    </ResponsiveContainer>
  )
}
