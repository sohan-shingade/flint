"""DuckDB last-candle price source.

Final-fallback source. Reads the most recent candle close for the
requested markets from `FlintStore.query_candles(..., limit=1)`.
Marks the quote `stale=True` so the UI can render a "as of N min ago"
banner when every live source is down.

Refusing to ever lie about freshness is the point — without this
final fallback the UI shows empty cells during a Drift+HL double
outage. With it, the user sees yesterday's close + a clear stale
flag and can make decisions accordingly.
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable

from .base import PriceQuote, PriceSource

logger = logging.getLogger("flint.price_sources.duckdb_cache")

# Default candle resolution to query when looking for "the most
# recent close". 60s gives the freshest data the collector might
# have written; falls through to coarser resolutions automatically
# (see `_RESOLUTIONS`).
_RESOLUTIONS = (60, 300, 900, 3600, 86400)


class LocalDuckDBSource(PriceSource):
    name = "duckdb_cache"

    def __init__(self, store) -> None:
        super().__init__()
        self._store = store

    def fetch(self, markets: Iterable[str]) -> Dict[str, PriceQuote]:
        if self._store is None:
            return {}
        out: Dict[str, PriceQuote] = {}
        any_success = False
        for market in markets:
            quote = self._latest_for(market)
            if quote is not None:
                out[market] = quote
                any_success = True
        if any_success:
            self.health.record_success()
        return out

    def _latest_for(self, market: str) -> PriceQuote | None:
        """Walk resolutions from finest to coarsest; return the
        first non-empty result. Always marks `stale=True`."""
        for res in _RESOLUTIONS:
            try:
                candles = self._store.query_candles(market, res, limit=1)
            except Exception as e:
                self.health.record_error(str(e))
                logger.debug("DuckDB fetch failed for %s @ %ds: %s",
                             market, res, e)
                return None
            if not candles:
                continue
            # query_candles returns oldest-first; with limit=1 the
            # one row may be either side depending on filtering. Be
            # safe: pick max ts.
            latest = max(candles, key=lambda c: c.ts)
            return PriceQuote(
                market=market,
                price=float(latest.close),
                ts=int(latest.ts),
                source=self.name,
                stale=True,
            )
        return None
