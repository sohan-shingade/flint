// Underwater (drawdown) curve for the result panel (§11.1). We derive it from the
// equity pairs — running peak, then (equity/peak − 1) as a percent — rather than
// asking for a second series; this is a deterministic transform of real equity, not
// fabricated data (D26). The x-axis is domain time, matching the equity curve (§B7).

import { useMemo } from 'react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { EquityPoint } from '../api/types'

const TICK = { fill: '#555560', fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }

const tooltipStyle = {
  background: '#111114',
  border: '1px solid #2a2a32',
  borderRadius: 0,
  fontSize: 11,
  fontFamily: "'JetBrains Mono', monospace",
}

function fmtTick(ms: number): string {
  const d = new Date(ms)
  const day = d.toISOString().slice(5, 10)
  const hm = d.toISOString().slice(11, 16)
  return hm === '00:00' ? day : `${day} ${hm}`
}

function drawdownSeries(series: EquityPoint[]): { ts: number; drawdown: number }[] {
  let peak = series[0]?.[1] ?? 0
  return series.map(([ts, v]) => {
    if (v > peak) peak = v
    const dd = peak > 0 ? (v / peak - 1) * 100 : 0
    return { ts, drawdown: +dd.toFixed(2) }
  })
}

export function DrawdownChart({ series, height = 160 }: { series: EquityPoint[]; height?: number }) {
  const data = useMemo(() => drawdownSeries(series), [series])

  if (!series || series.length < 2) {
    return (
      <div className="p-4 font-mono text-sm text-ghost" role="note">
        drawdown unavailable (need ≥2 equity points)
      </div>
    )
  }

  return (
    <div role="img" aria-label="drawdown curve">
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
          <YAxis tick={TICK} domain={['auto', 0]} width={50} />
          <Tooltip
            contentStyle={tooltipStyle}
            labelFormatter={(label) => fmtTick(Number(label))}
            formatter={(v: unknown) => [`${Number(v).toFixed(2)}%`, 'drawdown']}
          />
          <Area
            type="monotone"
            dataKey="drawdown"
            stroke="var(--color-loss)"
            fill="rgba(232, 77, 77, 0.08)"
            strokeWidth={1}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
