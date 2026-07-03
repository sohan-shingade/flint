// Comma-separated markets / venues inputs shared by the funding heatmap and the
// data explorer. Edits are debounced by an explicit "apply" so a half-typed
// symbol never fires a fan-out of coverage requests.

import { useState } from 'react'

export function parseCsv(s: string): string[] {
  return s
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean)
}

export function GridControls({
  markets,
  venues,
  onApply,
}: {
  markets: string[]
  venues: string[]
  onApply: (markets: string[], venues: string[]) => void
}) {
  const [m, setM] = useState(markets.join(', '))
  const [v, setV] = useState(venues.join(', '))
  return (
    <div className="flex flex-wrap items-end gap-3 border-b border-border p-4">
      <label className="flex flex-col gap-1">
        <span className="text-xs uppercase tracking-wide text-ghost">markets</span>
        <input
          aria-label="markets"
          value={m}
          onChange={(e) => setM(e.target.value)}
          className="w-64 rounded border border-border bg-void px-3 py-1.5 font-mono text-sm text-terminal outline-none focus:border-amber"
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs uppercase tracking-wide text-ghost">venues</span>
        <input
          aria-label="venues"
          value={v}
          onChange={(e) => setV(e.target.value)}
          className="w-64 rounded border border-border bg-void px-3 py-1.5 font-mono text-sm text-terminal outline-none focus:border-amber"
        />
      </label>
      <button
        onClick={() => onApply(parseCsv(m), parseCsv(v))}
        className="rounded border border-amber/50 bg-amber/10 px-4 py-1.5 font-mono text-sm text-amber"
      >
        apply
      </button>
    </div>
  )
}
