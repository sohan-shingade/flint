// The per-market granularity ladder (§B7): one coverage bar per tier — candles,
// ticks, book — the fidelity rungs a backtest can run at. Each bar's filled
// segments come from the tier's covered RangeSet (`tiers`), colored by the
// provenance that would serve them (`detail`): hl_rest/free, tardis, recorder,
// local_cache. An empty rung reads "none" — that market can't run at that tier
// yet, which is information, not a failure.

import type { Coverage, Range } from '../api/types'

const TIERS = ['candles', 'ticks', 'book'] as const

// The representative kind whose provenance colors each tier's segments (funding
// is required by every tier, so the market-data kind is the discriminating one).
const TIER_KIND: Record<string, string> = { candles: 'candles', ticks: 'trades', book: 'trades' }

function provColor(source: string): string {
  switch (source) {
    case 'tardis':
      return '#e8a849' // amber — the paid deep-history vendor
    case 'recorder':
      return '#4ea86a' // green — the live forward-only recorder
    case 'hl_rest':
    case 'free_venue_provider':
      return '#5aa0e8' // blue — the free venue REST tier
    case 'local_cache':
      return '#8a8a96' // gray — served from the local durable cache
    default:
      return '#555560'
  }
}

function sourceFor(
  detail: Coverage['detail'],
  kind: string,
  range: Range,
): string {
  const pieces = detail?.[kind] ?? []
  for (const p of pieces) {
    // The tier range is an intersection, so any detail piece that spans it is the
    // one serving it; a start-inside test is enough for these non-overlapping pieces.
    if (p.start_ms <= range.start_ms && p.end_ms >= range.end_ms) return p.source
    if (range.start_ms < p.end_ms && range.end_ms > p.start_ms) return p.source
  }
  return ''
}

function bounds(tiers: Coverage['tiers']): { min: number; span: number } {
  let min = Infinity
  let max = -Infinity
  for (const ranges of Object.values(tiers ?? {})) {
    for (const r of ranges) {
      if (r.start_ms < min) min = r.start_ms
      if (r.end_ms > max) max = r.end_ms
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return { min: 0, span: 1 }
  return { min, span: max - min }
}

export function CoverageLadder({
  tiers,
  detail,
  testid,
}: {
  tiers: Coverage['tiers']
  detail: Coverage['detail']
  testid?: string
}) {
  const { min, span } = bounds(tiers)

  return (
    <div className="space-y-1" data-testid={testid ?? 'coverage-ladder'}>
      {TIERS.map((name) => {
        const ranges = tiers?.[name] ?? []
        return (
          <div key={name} className="flex items-center gap-2" data-testid={`tier-${name}`}>
            <span className="w-12 shrink-0 text-[10px] uppercase tracking-wide text-ghost">{name}</span>
            <div className="relative h-3 flex-1 overflow-hidden rounded border border-border bg-void">
              {ranges.length === 0 ? (
                <span className="absolute inset-0 flex items-center pl-2 text-[9px] text-ghost/50">none</span>
              ) : (
                ranges.map((r, i) => {
                  const source = sourceFor(detail, TIER_KIND[name], r)
                  const left = ((r.start_ms - min) / span) * 100
                  const width = Math.max(((r.end_ms - r.start_ms) / span) * 100, 1)
                  return (
                    <div
                      key={i}
                      data-testid={`seg-${name}`}
                      data-source={source || 'unknown'}
                      title={source || 'unknown'}
                      className="absolute top-0 h-full"
                      style={{ left: `${left}%`, width: `${width}%`, background: provColor(source) }}
                    />
                  )
                })
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
