// Shared coverage loader for the funding heatmap (screen 2) and data explorer
// (screen 3). The 7.1 API exposes coverage one (market, venue) at a time
// (GET /data/coverage), so we fan out across the markets × venues grid and
// assemble the matrix client-side. A per-cell failure is captured as that cell's
// error rather than failing the whole grid — a partially-covered universe is a
// normal, first-class state (§19.1), not an all-or-nothing load.

import { useEffect, useState } from 'react'
import { apiGet, ApiError, encodeQuery } from '../api/client'
import type { Coverage } from '../api/types'

export interface Cell {
  market: string
  venue: string
  coverage?: Coverage['coverage']
  error?: ApiError | Error
}

export interface CoverageMatrix {
  cells: Record<string, Cell> // key `${venue}/${market}`
  loading: boolean
}

export function cellKey(venue: string, market: string): string {
  return `${venue}/${market}`
}

export function useCoverageMatrix(markets: string[], venues: string[]): CoverageMatrix {
  const [cells, setCells] = useState<Record<string, Cell>>({})
  const [loading, setLoading] = useState(false)
  const key = `${markets.join(',')}|${venues.join(',')}`

  useEffect(() => {
    let live = true
    setLoading(true)
    setCells({})
    const pairs: Array<{ market: string; venue: string }> = []
    for (const market of markets) for (const venue of venues) pairs.push({ market, venue })

    Promise.all(
      pairs.map(async ({ market, venue }) => {
        const k = cellKey(venue, market)
        try {
          const c = await apiGet<Coverage>(`/data/coverage${encodeQuery({ market, venue })}`)
          return [k, { market, venue, coverage: c.coverage }] as const
        } catch (e) {
          return [k, { market, venue, error: e as ApiError }] as const
        }
      }),
    ).then((entries) => {
      if (!live) return
      setCells(Object.fromEntries(entries))
      setLoading(false)
    })

    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return { cells, loading }
}
