// Screen 2 — Funding / basis heatmap (§6.5, §12, carry-forward e).
//
// A read-only lab view of funding across the markets × venues grid. The design
// (carry-forward e) describes cells carrying funding *rate* / basis values from
// research.funding_lab, exposed by a services-level query. The 7.1 REST surface
// does NOT yet expose that endpoint — the only coverage/funding signal on the API
// is GET /data/coverage. Per the fence we do NOT add an API field; we render what
// the API gives: each cell shows whether funding data is *present* for that
// (venue, market) and over what range. Cells with no funding coverage render as a
// first-class "no funding data" state (§19.1), never blank.
//
// CARRY-FORWARD (documented in the board DONE entry): when the API exposes a
// funding-rate/basis endpoint (services.funding_lab), swap the cell body from
// coverage-presence to the rate value + heatmap intensity. The grid, cell states,
// and degrade handling here are already the right shape for that upgrade.

import { useState } from 'react'
import { GridControls } from '../components/GridControls'
import { Loading } from '../components/states'
import { cellKey, useCoverageMatrix } from '../hooks/useCoverageMatrix'
import { fmtRange } from '../lib/format'

const DEFAULT_MARKETS = ['SOL-PERP', 'BTC-PERP', 'ETH-PERP']
// hyperliquid is the only executable venue (D28); jupiter is a lab/expansion leg.
const DEFAULT_VENUES = ['hyperliquid', 'jupiter']

export default function FundingHeatmap() {
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
        funding-data coverage per market × venue. cells show whether funding is available and over what
        range — funding is a hard gate for executable backtests, so a missing cell means that leg cannot
        be traded over an uncovered window.
      </p>

      {loading ? (
        <Loading label="loading funding coverage…" />
      ) : (
        <div className="overflow-x-auto p-4">
          <table className="border-collapse font-mono text-sm">
            <thead>
              <tr>
                <th className="border border-border bg-panel px-3 py-2 text-left text-ghost">market \ venue</th>
                {venues.map((v) => (
                  <th key={v} className="border border-border bg-panel px-3 py-2 text-left text-terminal">
                    {v}
                    {v !== 'hyperliquid' ? <span className="ml-1 text-xs text-ghost">(lab)</span> : null}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {markets.map((market) => (
                <tr key={market}>
                  <th className="border border-border bg-panel px-3 py-2 text-left text-terminal">{market}</th>
                  {venues.map((venue) => {
                    const cell = cells[cellKey(venue, market)]
                    const funding = cell?.coverage?.funding
                    const hasError = !!cell?.error
                    const covered = !!funding
                    const bg = hasError
                      ? 'bg-loss/10'
                      : covered
                        ? 'bg-gain/15'
                        : 'bg-panel'
                    return (
                      <td
                        key={venue}
                        data-testid={`cell-${venue}-${market}`}
                        data-covered={hasError ? 'error' : covered ? 'yes' : 'no'}
                        className={`border border-border px-3 py-2 align-top ${bg}`}
                      >
                        {hasError ? (
                          <span className="text-loss">unavailable</span>
                        ) : covered ? (
                          <span className="text-gain">{fmtRange(funding)}</span>
                        ) : (
                          <span className="text-ghost">no funding data</span>
                        )}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>

          <div className="mt-4 flex flex-wrap gap-4 font-mono text-xs text-ghost">
            <span>
              <span className="mr-1 inline-block h-3 w-3 rounded-sm bg-gain/40 align-middle" /> funding covered
            </span>
            <span>
              <span className="mr-1 inline-block h-3 w-3 rounded-sm bg-panel align-middle" /> no funding data
            </span>
            <span>
              <span className="mr-1 inline-block h-3 w-3 rounded-sm bg-loss/30 align-middle" /> query failed
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
