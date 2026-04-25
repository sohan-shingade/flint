"""Live PnL price poller — walks a fallback chain of price sources.

Originally polled Drift DLOB directly. Drift is offline post-hack
(see CLAUDE.md), so single-source dependency = ticker dies whenever
its venue does. The chain layer (`flint/paper/price_sources/`) makes
the source set pluggable: each tick we ask each source in priority
order, the first quote per market wins. UI gets a `stale=True` flag
when only the DuckDB last-candle fallback can serve.

Backwards-compatible API: `PriceTicker(markets, interval_s=5.0)`,
`get_price(market)`, `get_all_prices()` — all unchanged for existing
callers (`flint/paper/engine.py`, `flint/api/main.py`).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Sequence

from .price_sources import PriceQuote, PriceSource, default_chain

logger = logging.getLogger("flint.paper")


class PriceTicker:
    """Polls a fallback chain of price sources at a fixed interval.

    The first source in `sources` to return a quote for a given market
    wins for that market on that tick. Sources that fail just record
    on `health` and the chain falls through.

    `sources=None` invokes `default_chain()` — that gives HL → Drift
    → (no-cache) by default. Pass `store` via `default_chain(store)`
    explicitly when constructing if you want the DuckDB fallback.
    """

    def __init__(
        self,
        markets: List[str],
        interval_s: float = 5.0,
        sources: Optional[Sequence[PriceSource]] = None,
    ):
        self.markets = list(markets)
        self.interval_s = interval_s
        self.sources: List[PriceSource] = list(
            sources if sources is not None else default_chain()
        )
        self.quotes: Dict[str, PriceQuote] = {}
        self._running = False

    @property
    def prices(self) -> Dict[str, float]:
        """Back-compat shim: callers that used `ticker.prices[mkt] = …`
        directly in tests still work, but the canonical surface is
        now `quotes` (which carries source + stale flag)."""
        return {m: q.price for m, q in self.quotes.items()}

    async def run(self) -> None:
        """Main async loop — poll until cancelled."""
        self._running = True
        while self._running:
            try:
                await asyncio.to_thread(self._poll_once)
            except Exception as e:
                logger.debug("price ticker poll failed: %s", e)
            await asyncio.sleep(self.interval_s)

    def _poll_once(self) -> None:
        """One tick: walk sources, accumulate first-quote-wins per
        market. Synchronous so we can hand it to `to_thread` cleanly."""
        remaining = set(self.markets)
        for source in self.sources:
            if not remaining:
                return
            wanted = [m for m in remaining if source.supports(m)]
            if not wanted:
                continue
            try:
                got = source.fetch(wanted)
            except Exception as e:
                source.health.record_error(str(e))
                logger.debug("source %s raised: %s", source.name, e)
                continue
            for market, quote in got.items():
                if market in remaining:
                    self.quotes[market] = quote
                    remaining.discard(market)

    def stop(self) -> None:
        self._running = False

    def add_market(self, market: str) -> None:
        if market not in self.markets:
            self.markets.append(market)

    def get_price(self, market: str) -> Optional[float]:
        q = self.quotes.get(market)
        return q.price if q is not None else None

    def get_quote(self, market: str) -> Optional[PriceQuote]:
        return self.quotes.get(market)

    def get_all_prices(self) -> Dict[str, float]:
        return {m: q.price for m, q in self.quotes.items()}

    def get_health(self) -> List[dict]:
        """Per-source health snapshot for `/api/v1/system/price-sources`."""
        return [s.health.to_dict() for s in self.sources]
