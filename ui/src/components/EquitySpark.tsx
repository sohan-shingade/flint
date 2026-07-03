// A hand-rolled SVG equity curve — no chart dependency, deterministic DOM so the
// vitest assertions are stable. Renders the per-bar equity series (carry-forward h:
// equity_series_from_events folded over EQUITY events) as a single polyline.

export function EquitySpark({ series, height = 160 }: { series: number[]; height?: number }) {
  if (!series || series.length < 2) {
    return (
      <div className="p-4 text-ghost font-mono text-sm" role="note">
        equity curve unavailable (need ≥2 points)
      </div>
    )
  }
  const w = 600
  const h = height
  const pad = 4
  const min = Math.min(...series)
  const max = Math.max(...series)
  const span = max - min || 1
  const n = series.length
  const points = series
    .map((v, i) => {
      const x = pad + (i / (n - 1)) * (w - 2 * pad)
      const y = pad + (1 - (v - min) / span) * (h - 2 * pad)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
  const up = series[n - 1] >= series[0]
  const stroke = up ? 'var(--color-gain)' : 'var(--color-loss)'
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      className="w-full"
      role="img"
      aria-label="equity curve"
      preserveAspectRatio="none"
    >
      <polyline points={points} fill="none" stroke={stroke} strokeWidth={1.5} />
    </svg>
  )
}
