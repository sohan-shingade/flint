import { useMemo } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'

interface Props {
  drawdown: [number, number][]
  height?: number
}

const TICK = { fill: '#555560', fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }

const tooltipStyle = {
  background: '#111114',
  border: '1px solid #2a2a32',
  borderRadius: 0,
  fontSize: 11,
  fontFamily: "'JetBrains Mono', monospace",
}

function downsample(arr: any[], maxPoints: number): any[] {
  if (arr.length <= maxPoints) return arr
  const step = arr.length / maxPoints
  const result: any[] = [arr[0]]
  for (let i = 1; i < maxPoints - 1; i++) {
    const start = Math.floor(i * step)
    const end = Math.floor((i + 1) * step)
    let best = start
    for (let j = start + 1; j < end && j < arr.length; j++) {
      if (Math.abs(arr[j].drawdown) > Math.abs(arr[best].drawdown)) best = j
    }
    result.push(arr[best])
  }
  result.push(arr[arr.length - 1])
  return result
}

export default function DrawdownChart({ drawdown, height = 180 }: Props) {
  const data = useMemo(() => {
    const full = drawdown.map(([ts, val]) => ({
      date: new Date(ts * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' }),
      drawdown: +(val * -100).toFixed(2),
    }))
    return downsample(full, 600)
  }, [drawdown])

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data}>
        <CartesianGrid strokeDasharray="2 6" stroke="#1a1a1f" />
        <XAxis dataKey="date" tick={TICK} interval="preserveStartEnd" minTickGap={60} />
        <YAxis tick={TICK} domain={['auto', 0]} width={50} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: unknown) => [`${Number(v).toFixed(2)}%`, 'Drawdown']} />
        <Area type="monotone" dataKey="drawdown" stroke="#e84d4d" fill="rgba(232, 77, 77, 0.08)" strokeWidth={1} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}
