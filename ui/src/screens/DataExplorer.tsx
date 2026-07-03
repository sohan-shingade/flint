// Screen 3 — Data explorer / coverage matrix (§9, §12).
//
// The read side of "what do I have before I run?" — GET /data/coverage per
// (market, venue), showing the covered range for each data kind (candles,
// funding, oi). No fetch, no gate: this is the coverage query, so an uncovered
// cell is information, not a failure. It renders as a first-class "none" state.
// A per-cell query fault shows inline without sinking the whole grid (§19.1).

import { useState } from 'react'
import { GridControls } from '../components/GridControls'
import { Loading } from '../components/states'
import { cellKey, useCoverageMatrix } from '../hooks/useCoverageMatrix'
import type { Range } from '../api/types'
import { fmtRange } from '../lib/format'

const DEFAULT_MARKETS = ['SOL-PERP', 'BTC-PERP']
const DEFAULT_VENUES = ['hyperliquid', 'jupiter']
const KINDS: Array<'candles' | 'funding' | 'oi'> = ['candles', 'funding', 'oi']

function KindRow({ label, range }: { label: string; range: Range | null | undefined }) {
  const covered = !!range
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-ghost">{label}</span>
      <span className={covered ? 'text-terminal' : 'text-ghost/50'}>{covered ? fmtRange(range) : 'none'}</span>
    </div>
  )
}

export default function DataExplorer() {
  const [markets, setMarkets] = useState(DEFAULT_MARKETS)
  const [venues, setVenues] = useState(DEFAULT_VENUES)
  const { cells, loading } = useCoverageMatrix(markets, venues)

  return (
    <div>
      <GridControls
        markets={markets}
        venues={venues}
        onApply={(m, v) => {
          setMarkets(m)
          setVenues(v)
        }}
      />

      <p className="px-4 pt-3 font-mono text-xs text-ghost">
        covered ranges per market × venue, by data kind. this is the coverage query — no fetch, no funding
        gate — so an uncovered kind reads "none" rather than erroring.
      </p>

      {loading ? (
        <Loading label="loading coverage…" />
      ) : (
        <div className="grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">
          {markets.map((market) =>
            venues.map((venue) => {
              const cell = cells[cellKey(venue, market)]
              return (
                <div
                  key={cellKey(venue, market)}
                  data-testid={`cov-${venue}-${market}`}
                  className="rounded border border-border bg-panel p-3 font-mono text-sm"
                >
                  <div className="mb-2 flex items-baseline justify-between">
                    <span className="text-terminal">{market}</span>
                    <span className="text-xs text-ghost">
                      {venue}
                      {venue !== 'hyperliquid' ? ' · lab' : ' · executable'}
                    </span>
                  </div>
                  {cell?.error ? (
                    <div className="text-loss">coverage query failed: {cell.error.message}</div>
                  ) : (
                    <div className="space-y-1">
                      {KINDS.map((k) => (
                        <KindRow key={k} label={k} range={cell?.coverage?.[k]} />
                      ))}
                    </div>
                  )}
                </div>
              )
            }),
          )}
        </div>
      )}
    </div>
  )
}
