"""Lightweight DLOB mid-price poller for live PnL display.

Polls Drift DLOB every N seconds for each tracked market.
Display-only — doesn't affect strategy logic.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger("flint.paper")

DLOB_BASE = "https://dlob.drift.trade"


def _fetch_mid_price(market: str) -> Optional[float]:
    """Fetch mid-price from Drift DLOB. Synchronous."""
    try:
        resp = httpx.get(
            f"{DLOB_BASE}/l2",
            params={"marketName": market, "depth": 1, "marketType": "perp"},
            timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        if bids and asks:
            best_bid = float(bids[0]["price"])
            best_ask = float(asks[0]["price"])
            return (best_bid + best_ask) / 2
    except Exception as e:
        logger.debug("DLOB fetch failed for %s: %s", market, e)
    return None


class PriceTicker:
    """Polls Drift DLOB for mid-prices at a configurable interval."""

    def __init__(self, markets: List[str], interval_s: float = 5.0):
        self.markets = list(markets)
        self.interval_s = interval_s
        self.prices: Dict[str, float] = {}
        self._running = False

    async def run(self) -> None:
        """Main async loop — poll prices forever until cancelled."""
        self._running = True
        while self._running:
            for market in self.markets:
                try:
                    price = await asyncio.to_thread(_fetch_mid_price, market)
                    if price is not None:
                        self.prices[market] = price
                except Exception:
                    pass
            await asyncio.sleep(self.interval_s)

    def stop(self) -> None:
        self._running = False

    def add_market(self, market: str) -> None:
        if market not in self.markets:
            self.markets.append(market)

    def get_price(self, market: str) -> Optional[float]:
        return self.prices.get(market)

    def get_all_prices(self) -> Dict[str, float]:
        return dict(self.prices)
