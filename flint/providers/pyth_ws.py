"""PythWebSocketFeed — real-time oracle prices from Pyth Hermes.

Streams sub-second price updates and maintains an in-memory price cache.
Batch-persists to FlintStore every batch_interval_s seconds.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Tuple

from ..models import OraclePrice
from .websocket import WebSocketFeed

logger = logging.getLogger("flint.pyth_ws")

_PYTH_WS_URL = "wss://hermes.pyth.network/ws"

_MARKET_TO_PAIR: Dict[str, str] = {
    "SOL-PERP": "SOL/USD", "BTC-PERP": "BTC/USD", "ETH-PERP": "ETH/USD",
    "DOGE-PERP": "DOGE/USD", "SUI-PERP": "SUI/USD", "ARB-PERP": "ARB/USD",
    "LINK-PERP": "LINK/USD", "AVAX-PERP": "AVAX/USD", "XRP-PERP": "XRP/USD",
    "WIF-PERP": "WIF/USD", "JUP-PERP": "JUP/USD", "INJ-PERP": "INJ/USD",
    "RENDER-PERP": "RENDER/USD", "OP-PERP": "OP/USD", "TIA-PERP": "TIA/USD",
    "SEI-PERP": "SEI/USD", "BNB-PERP": "BNB/USD", "DRIFT-PERP": "DRIFT/USD",
    "PYTH-PERP": "PYTH/USD", "1MBONK-PERP": "BONK/USD",
}
_PAIR_TO_MARKET = {v: k for k, v in _MARKET_TO_PAIR.items()}


class PythWebSocketFeed(WebSocketFeed):
    """WebSocket feed for Pyth Hermes oracle price updates.

    Args:
        markets: List of market symbols to track.
        store: Optional FlintStore for batch-persisting prices.
        batch_interval_s: How often to flush price cache to store.
        url: Override WebSocket URL (for testing).
    """

    def __init__(
        self,
        markets: List[str],
        store=None,
        batch_interval_s: float = 10.0,
        url: str = _PYTH_WS_URL,
        **kwargs,
    ):
        super().__init__(url=url, name="pyth", **kwargs)
        self._markets = markets
        self._store = store
        self._batch_interval_s = batch_interval_s

        self._price_cache: Dict[str, Tuple[float, int]] = {}
        self._pending_prices: List[OraclePrice] = []
        self._last_flush_ts: float = 0.0

        self._pairs: Dict[str, str] = {}
        for market in markets:
            pair = _MARKET_TO_PAIR.get(market)
            if pair:
                self._pairs[pair] = market

    def get_price(self, market: str) -> Optional[Tuple[float, int]]:
        return self._price_cache.get(market)

    def get_all_prices(self) -> Dict[str, Tuple[float, int]]:
        return dict(self._price_cache)

    async def _connect_ws(self):
        import websockets
        ws = await websockets.connect(self._url)
        return ws

    async def _subscribe(self, ws) -> None:
        import json
        from .pyth import FEED_IDS
        feed_ids = []
        for pair in self._pairs:
            fid = FEED_IDS.get(pair)
            if fid:
                feed_ids.append(fid)
        if feed_ids:
            await ws.send(json.dumps({
                "type": "subscribe",
                "ids": feed_ids,
            }))
        logger.info("Subscribed to %d Pyth price feeds", len(feed_ids))

    async def _handle_message(self, raw: dict) -> None:
        msg_type = raw.get("type", "")
        if msg_type != "price_update":
            return

        pair = raw.get("pair", "")
        market = self._pairs.get(pair) or _PAIR_TO_MARKET.get(pair)
        if not market:
            return

        price = float(raw.get("price", 0))
        ts = int(raw.get("ts", 0))
        if price <= 0 or ts <= 0:
            return

        self._price_cache[market] = (price, ts)
        self._pending_prices.append(OraclePrice(market=market, ts=ts, price=price))

        now = time.time()
        if self._store and (now - self._last_flush_ts) >= self._batch_interval_s:
            self._flush_to_store()

    def _flush_to_store(self) -> None:
        if not self._store or not self._pending_prices:
            return
        try:
            self._store.upsert_oracle_prices(self._pending_prices)
        except Exception as e:
            logger.error("Failed to flush oracle prices: %s", e)
        self._pending_prices.clear()
        self._last_flush_ts = time.time()

    async def _fallback_poll(self) -> None:
        import asyncio
        try:
            from .pyth import PythProvider
            provider = PythProvider()
            pairs = list(self._pairs.keys())
            prices = await asyncio.to_thread(provider.fetch_prices, pairs)
            for p in prices:
                market = _PAIR_TO_MARKET.get(p.get("pair", ""))
                if market and p.get("price"):
                    self._price_cache[market] = (p["price"], p.get("ts", int(time.time())))
        except Exception as e:
            logger.error("Pyth fallback poll failed: %s", e)

    async def _backfill_gap(self, disconnect_ts: int, reconnect_ts: int) -> None:
        pass  # Oracle prices are point-in-time, no backfill needed
