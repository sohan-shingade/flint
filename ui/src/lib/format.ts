// Display formatting shared across the five screens. Timestamps are integer
// unix-ms UTC (bar START, §5); money is shown to cents; ratios as plain numbers.

export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  const d = new Date(ms)
  if (Number.isNaN(d.getTime())) return '—'
  // YYYY-MM-DD HH:MM UTC — bar-start timestamps carry no sub-minute meaning here.
  return d.toISOString().slice(0, 16).replace('T', ' ') + ' UTC'
}

export function fmtDate(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  const d = new Date(ms)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toISOString().slice(0, 10)
}

export function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function fmtPct(ratio: number | null | undefined, digits = 1): string {
  if (ratio === null || ratio === undefined || Number.isNaN(ratio)) return '—'
  return `${(ratio * 100).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`
}

export function fmtUsd(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  const sign = n < 0 ? '-' : ''
  return `${sign}$${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function fmtRange(r: { start_ms: number; end_ms: number } | null | undefined): string {
  if (!r) return '—'
  return `${fmtDate(r.start_ms)} → ${fmtDate(r.end_ms)}`
}
