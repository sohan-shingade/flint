// Equity curve for the result panel (§11.1). v3 hands us the per-sample equity as
// [ts_ms, value] pairs (§B7 wire change), so the x-axis is real domain time — bar
// runs come regularly spaced, tick runs irregularly, and the same chart reads both.
// Wrapped in a role="img" element so the curve is announced (and asserted) even
// where the SVG has no measured size.

import { useMemo } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { EquityPoint } from '../api/types'

const TICK = { fill: '#555560', fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }

const tooltipStyle = {
  background: '#111114',
  border: '1px solid #2a2a32',
  borderRadius: 0,
  fontSize: 11,
  fontFamily: "'JetBrains Mono', monospace",
  color: '#a0a0a8',
}

// UTC day-and-time label for a ts axis tick; time is meaningful on intraday runs.
function fmtTick(ms: number): string {
  const d = new Date(ms)
  const day = d.toISOString().slice(5, 10)
  const hm = d.toISOString().slice(11, 16)
  return hm === '00:00' ? day : `${day} ${hm}`
}

// Peak-preserving downsample to keep the DOM light on long runs.
function downsample<T extends { equity: number }>(arr: T[], maxPoints: number): T[] {
  if (arr.length <= maxPoints) return arr
  const step = arr.length / maxPoints
  const out: T[] = [arr[0]]
  for (let i = 1; i < maxPoints - 1; i++) {
    const start = Math.floor(i * step)
    const end = Math.floor((i + 1) * step)
    let best = start
    for (let j = start + 1; j < end && j < arr.length; j++) {
      if (Math.abs(arr[j].equity) > Math.abs(arr[best].equity)) best = j
    }
    out.push(arr[best])
  }
  out.push(arr[arr.length - 1])
  return out
}

export function EquityCurve({ series, height = 260 }: { series: EquityPoint[]; height?: number }) {
  const data = useMemo(
    () => downsample(series.map(([ts, equity]) => ({ ts, equity: +equity.toFixed(2) })), 300),
    [series],
  )

  if (!series || series.length < 2) {
    return (
      <div className="p-4 font-mono text-sm text-ghost" role="img" aria-label="equity curve">
        equity curve unavailable (need ≥2 points)
      </div>
    )
  }

  const up = series[series.length - 1][1] >= series[0][1]
  const stroke = up ? 'var(--color-gain)' : 'var(--color-loss)'

  return (
    <div role="img" aria-label="equity curve">
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={data}>
          <CartesianGrid strokeDasharray="2 6" stroke="#1a1a1f" />
          <XAxis
            dataKey="ts"
            type="number"
            scale="time"
            domain={['dataMin', 'dataMax']}
            tickFormatter={fmtTick}
            tick={TICK}
            interval="preserveStartEnd"
            minTickGap={60}
          />
          <YAxis tick={TICK} domain={['auto', 'auto']} width={65} />
          <Tooltip
            contentStyle={tooltipStyle}
            labelStyle={{ color: '#a0a0a8' }}
            labelFormatter={(label) => fmtTick(Number(label))}
          />
          <Area
            type="monotone"
            dataKey="equity"
            stroke={stroke}
            fill="rgba(232, 168, 73, 0.06)"
            strokeWidth={1.5}
            isAnimationActive={false}
            name="equity"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
