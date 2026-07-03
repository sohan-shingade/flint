// Screen 2 — Funding / basis heatmap (§10, §12, carry-forward e).
//
// A read-only lab view of funding carry across the markets × venues grid. It reads
// GET /api/v1/lab/funding (services.funding_lab): each cell carries the venue's mean
// hourly rate + its linear annualization for that market, and each market carries the
// widest cross-venue dislocation (the arb headline). The lab is degrade-not-reject —
// a venue with no observations is a null cell, never a rejection.
//
// FALLBACK: if the lab returns NO observations at all (no funding recorded yet in the
// window), we fall back to the coverage view (GET /data/coverage) so the screen still
// shows which market × venue funding *exists*, rather than an empty grid (§19.1).

import { useEffect, useState } from 'react'
import { apiGet, ApiError, encodeMulti } from '../api/client'
import type { FundingLab } from '../api/types'
import { GridControls } from '../components/GridControls'
import { ErrorState, Loading } from '../components/states'
import { cellKey, useCoverageMatrix } from '../hooks/useCoverageMatrix'
import { fmtNum, fmtPct, fmtRange } from '../lib/format'

const DEFAULT_MARKETS = ['SOL-PERP', 'BTC-PERP', 'ETH-PERP']
// hyperliquid is the only executable venue (D28); the rest are lab/expansion legs.
const DEFAULT_VENUES = ['hyperliquid', 'binance', 'okx']

// bps/hour from an hourly rate ratio (0.0002 → 2.0 bps/h).
const bpsPerHour = (r: number) => r * 1e4

function cellBg(annualized: number): string {
  const mag = Math.min(Math.abs(annualized), 1) // clamp at 100%/yr for intensity
  if (annualized > 0) return `rgba(87, 200, 77, ${0.08 + 0.32 * mag})` // gain tint
  if (annualized < 0) return `rgba(232, 77, 77, ${0.08 + 0.32 * mag})` // loss tint
  return 'transparent'
}

export default function FundingHeatmap() {
  const [markets, setMarkets] = useState(DEFAULT_MARKETS)
  const [venues, setVenues] = useState(DEFAULT_VENUES)
  const [lab, setLab] = useState<FundingLab | null>(null)
  const [error, setError] = useState<ApiError | Error | null>(null)
  const [loading, setLoading] = useState(false)
  const key = `${markets.join(',')}|${venues.join(',')}`

  useEffect(() => {
    let live = true
    setLoading(true)
    setError(null)
    setLab(null)
    apiGet<FundingLab>(`/lab/funding${encodeMulti({ market: markets, venue: venues })}`)
      .then((r) => live && setLab(r))
      .catch((e) => live && setError(e))
      .finally(() => live && setLoading(false))
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  const hasObservations = lab ? Object.values(lab.cells).some((c) => c !== null) : false
  // Only fetch the coverage fallback when the lab came back with nothing.
  const fbActive = !!lab && !hasObservations
  const coverage = useCoverageMatrix(fbActive ? markets : [], fbActive ? venues : [])

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
        funding carry per market × venue — annualized (linear, never compounded) with the mean hourly rate.
        read-only lab: legs degrade rather than reject, and cross-venue dislocation flags where one venue's
        funding deviates most from the benchmark.
      </p>

      {loading && <Loading label="loading funding lab…" />}
      {error && <ErrorState error={error} />}

      {lab && !loading && hasObservations && (
        <div className="overflow-x-auto p-4">
          <table className="border-collapse font-mono text-sm">
            <thead>
              <tr>
                <th className="border border-border bg-panel px-3 py-2 text-left text-ghost">market \ venue</th>
                {lab.venues.map((v) => (
                  <th key={v} className="border border-border bg-panel px-3 py-2 text-left text-terminal">
                    {v}
                    {v !== 'hyperliquid' ? <span className="ml-1 text-xs text-ghost">(lab)</span> : null}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {lab.markets.map((market) => {
                const disloc = lab.dislocation[market]
                return (
                  <tr key={market}>
                    <th className="border border-border bg-panel px-3 py-2 text-left align-top text-terminal">
                      {market}
                      {disloc ? (
                        <div className="mt-1 text-xs font-normal text-amber">
                          widest: {disloc.venue} {fmtNum(bpsPerHour(disloc.dislocation_hourly), 3)} bps/h
                        </div>
                      ) : null}
                    </th>
                    {lab.venues.map((venue) => {
                      const c = lab.cells[cellKey(venue, market)]
                      return (
                        <td
                          key={venue}
                          data-testid={`fund-${venue}-${market}`}
                          data-observed={c ? 'yes' : 'no'}
                          className="border border-border px-3 py-2 align-top"
                          style={{ background: c ? cellBg(c.annualized) : undefined }}
                        >
                          {c ? (
                            <div>
                              <div className="text-terminal">{fmtPct(c.annualized)}/yr</div>
                              <div className="text-xs text-ghost">
                                {fmtNum(bpsPerHour(c.mean_hourly), 3)} bps/h · n={c.n}
                              </div>
                            </div>
                          ) : (
                            <span className="text-ghost">no observations</span>
                          )}
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
            </tbody>
          </table>
          <div className="mt-3 font-mono text-xs text-ghost">
            evaluated {fmtRange(lab.effective_range)} · rate type: {lab.rate_type ?? 'all'}
          </div>
        </div>
      )}

      {/* Fallback: the lab has no observations in this window — show funding-DATA coverage. */}
      {fbActive && !loading && (
        <div className="p-4">
          <div className="mb-3 rounded border border-amber/40 bg-amber-glow p-3 font-mono text-xs text-amber">
            no funding observations in this window — showing funding-data coverage instead.
          </div>
          {coverage.loading ? (
            <Loading label="loading coverage…" />
          ) : (
            <table className="border-collapse font-mono text-sm">
              <thead>
                <tr>
                  <th className="border border-border bg-panel px-3 py-2 text-left text-ghost">market \ venue</th>
                  {venues.map((v) => (
                    <th key={v} className="border border-border bg-panel px-3 py-2 text-left text-terminal">
                      {v}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {markets.map((market) => (
                  <tr key={market}>
                    <th className="border border-border bg-panel px-3 py-2 text-left text-terminal">{market}</th>
                    {venues.map((venue) => {
                      const cell = coverage.cells[cellKey(venue, market)]
                      const funding = cell?.coverage?.funding
                      return (
                        <td
                          key={venue}
                          data-testid={`cov-${venue}-${market}`}
                          data-covered={cell?.error ? 'error' : funding ? 'yes' : 'no'}
                          className="border border-border px-3 py-2"
                        >
                          {cell?.error ? (
                            <span className="text-loss">unavailable</span>
                          ) : funding ? (
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
          )}
        </div>
      )}
    </div>
  )
}
