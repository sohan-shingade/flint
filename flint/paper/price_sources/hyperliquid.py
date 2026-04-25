"""Hyperliquid /info allMids price source.

One HTTP POST returns mid-prices for every Hyperliquid perp at once,
so this source covers the whole tracked-market list in a single
fetch regardless of how many markets the ticker watches.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Iterable

import httpx

from .base import PriceQuote, PriceSource

logger = logging.getLogger("flint.price_sources.hyperliquid")

_HL_INFO_URL = "https://api.hyperliquid.xyz/info"

# Flint market symbol → Hyperliquid coin code. Mirror of the table in
# `flint/providers/hyperliquid_candles.py:_FLINT_TO_HL`. Update both
# when adding markets.
_FLINT_TO_HL = {
    "SOL-PERP": "SOL", "BTC-PERP": "BTC", "ETH-PERP": "ETH",
    "DOGE-PERP": "DOGE", "AVAX-PERP": "AVAX", "LINK-PERP": "LINK",
    "ARB-PERP": "ARB", "SUI-PERP": "SUI", "XRP-PERP": "XRP",
    "OP-PERP": "OP", "INJ-PERP": "INJ", "TIA-PERP": "TIA",
    "SEI-PERP": "SEI", "WIF-PERP": "WIF", "JUP-PERP": "JUP",
    "RENDER-PERP": "RENDER", "BNB-PERP": "BNB",
}


class HyperliquidInfoSource(PriceSource):
    name = "hyperliquid"

    def __init__(self, timeout_s: float = 5.0) -> None:
        super().__init__()
        self._timeout = timeout_s

    def supports(self, market: str) -> bool:
        return market in _FLINT_TO_HL

    def fetch(self, markets: Iterable[str]) -> Dict[str, PriceQuote]:
        wanted = [m for m in markets if m in _FLINT_TO_HL]
        if not wanted:
            return {}
        try:
            resp = httpx.post(
                _HL_INFO_URL,
                json={"type": "allMids"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                self.health.record_error("non-dict response")
                return {}
        except Exception as e:
            self.health.record_error(str(e))
            logger.debug("HL allMids fetch failed: %s", e)
            return {}

        now = int(time.time())
        out: Dict[str, PriceQuote] = {}
        for market in wanted:
            coin = _FLINT_TO_HL[market]
            mid_str = data.get(coin)
            if mid_str is None:
                continue
            try:
                price = float(mid_str)
            except (TypeError, ValueError):
                continue
            out[market] = PriceQuote(
                market=market, price=price, ts=now, source=self.name,
            )
        if out:
            self.health.record_success()
        return out
