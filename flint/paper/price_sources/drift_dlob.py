"""Drift DLOB price source.

⚠️ Drift Protocol is offline post-hack (CLAUDE.md rule). This source
is kept registered in the default chain so the ticker automatically
picks Drift's tighter native mid up again when Drift comes back —
until then every fetch fails fast and the chain falls through to
the next source.

Endpoint: GET https://dlob.drift.trade/l2 — one request per market.
Returns Drift-protocol-scaled prices; we divide by `PRICE_PRECISION`.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Iterable

import httpx

from ...precision import PRICE_PRECISION
from .base import PriceQuote, PriceSource

logger = logging.getLogger("flint.price_sources.drift_dlob")

DLOB_BASE = "https://dlob.drift.trade"
_PRICE_SCALE = float(PRICE_PRECISION)


class DriftDLOBSource(PriceSource):
    name = "drift_dlob"

    def __init__(self, timeout_s: float = 5.0) -> None:
        super().__init__()
        self._timeout = timeout_s

    def fetch(self, markets: Iterable[str]) -> Dict[str, PriceQuote]:
        out: Dict[str, PriceQuote] = {}
        any_success = False
        for market in markets:
            try:
                resp = httpx.get(
                    f"{DLOB_BASE}/l2",
                    params={"marketName": market, "depth": 1, "marketType": "perp"},
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                self.health.record_error(str(e))
                logger.debug("DLOB fetch failed for %s: %s", market, e)
                continue

            bids = data.get("bids", [])
            asks = data.get("asks", [])
            if not (bids and asks):
                continue
            try:
                best_bid = float(bids[0]["price"]) / _PRICE_SCALE
                best_ask = float(asks[0]["price"]) / _PRICE_SCALE
            except (KeyError, TypeError, ValueError):
                continue
            mid = (best_bid + best_ask) / 2.0
            out[market] = PriceQuote(
                market=market, price=mid, ts=int(time.time()), source=self.name,
            )
            any_success = True
        if any_success:
            self.health.record_success()
        return out
