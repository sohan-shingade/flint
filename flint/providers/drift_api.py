"""Drift Data API provider for real-time and recent data.

Uses two free, public endpoints:
  - data.api.drift.trade  — funding rates (historical query)
  - dlob.drift.trade      — L2/L3 orderbook, live market data
"""
from __future__ import annotations


# Phase 1 T1.3.a + D-1.3-providers — point-in-time declaration.
# Defaults are conservative — callers should verify against the
# specific source API when using this data in parity/PIT-sensitive
# contexts. Review date: 2026-04-24.
PIT_METADATA = {  # noqa: E402
    "candle_ts": "bar-close",
    "funding_ts": "accrual-time",
    "orderbook_ts": "exchange-time",
    "oi_ts": "exchange-time",
    "reviewed": "2026-04-24",
}

from typing import List, Optional

import httpx

from ..models import FundingRate, OrderbookLevel, OrderbookSnapshot

_DATA_API = "https://data.api.drift.trade"
_DLOB_API = "https://dlob.drift.trade"

# Use centralized precision constants
from ..precision import PRICE_PRECISION as _PRICE_PRECISION_INT, BASE_PRECISION as _BASE_PRECISION_INT
_PRICE_PRECISION = float(_PRICE_PRECISION_INT)
_BASE_PRECISION = float(_BASE_PRECISION_INT)


class DriftDataProvider:
    """Fetches real-time data from Drift's public APIs."""

    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._client = client or httpx.Client(timeout=30)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- funding rates --------------------------------------------------------

    def fetch_funding_rates(
        self,
        market_index: int = 0,
        market_name: str = "SOL-PERP",
        limit: int = 100,
    ) -> List[FundingRate]:
        """DEPRECATED: This endpoint (/fundingRates?marketIndex=) returns 404.
        Use DriftFundingProvider from providers/funding_rates.py instead.
        """
        import logging
        logging.getLogger("flint.providers.drift_api").warning(
            "fetch_funding_rates() is deprecated — Drift removed /fundingRates endpoint. "
            "Use DriftFundingProvider instead."
        )
        return []

    # -- orderbook ------------------------------------------------------------

    def fetch_orderbook(
        self,
        market_name: str = "SOL-PERP",
        depth: int = 10,
    ) -> OrderbookSnapshot:
        resp = self._client.get(
            f"{_DLOB_API}/l2",
            params={"marketName": market_name, "depth": depth},
        )
        resp.raise_for_status()
        data = resp.json()

        bids = tuple(
            OrderbookLevel(
                price=int(b["price"]) / _PRICE_PRECISION,
                size=int(b["size"]) / _BASE_PRECISION,
            )
            for b in data.get("bids", [])
        )
        asks = tuple(
            OrderbookLevel(
                price=int(a["price"]) / _PRICE_PRECISION,
                size=int(a["size"]) / _BASE_PRECISION,
            )
            for a in data.get("asks", [])
        )
        slot = int(data.get("slot", 0))
        import time
        return OrderbookSnapshot(
            market=market_name,
            ts=int(time.time()),
            bids=bids,
            asks=asks,
            slot=slot,
        )

    def fetch_l3_orderbook(
        self,
        market_name: str = "SOL-PERP",
        depth: int = 10,
    ) -> OrderbookSnapshot:
        resp = self._client.get(
            f"{_DLOB_API}/l3",
            params={"marketName": market_name, "depth": depth},
        )
        resp.raise_for_status()
        data = resp.json()

        bids = tuple(
            OrderbookLevel(
                price=int(b["price"]) / _PRICE_PRECISION,
                size=int(b["size"]) / _BASE_PRECISION,
            )
            for b in data.get("bids", [])
        )
        asks = tuple(
            OrderbookLevel(
                price=int(a["price"]) / _PRICE_PRECISION,
                size=int(a["size"]) / _BASE_PRECISION,
            )
            for a in data.get("asks", [])
        )
        import time
        return OrderbookSnapshot(
            market=market_name,
            ts=int(time.time()),
            bids=bids,
            asks=asks,
            slot=int(data.get("slot", 0)),
        )

    # -- mid price convenience ------------------------------------------------

    def fetch_mid_price(self, market_name: str = "SOL-PERP") -> float:
        ob = self.fetch_orderbook(market_name, depth=1)
        if ob.bids and ob.asks:
            return (ob.bids[0].price + ob.asks[0].price) / 2
        raise ValueError(f"Empty orderbook for {market_name}")
