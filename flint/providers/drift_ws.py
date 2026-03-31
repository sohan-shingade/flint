"""DriftWebSocketFeed — real-time trade and funding data from Drift Protocol.

Subscribes to trade events (aggregated into candles via CandleAggregator)
and funding rate updates. Falls back to REST polling on disconnect.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from ..models import Candle
from .candle_aggregator import CandleAggregator
from .websocket import WebSocketFeed

logger = logging.getLogger("flint.drift_ws")

_DRIFT_WS_URL = "wss://dlob.drift.trade/ws"


class DriftWebSocketFeed(WebSocketFeed):
    """WebSocket feed for Drift Protocol trade and funding data.

    Args:
        markets: List of market symbols to subscribe to.
        resolution_s: Candle aggregation resolution in seconds.
        on_candle_close: Callback fired when a candle bar closes.
        store: Optional FlintStore for persisting candles and funding.
        url: Override WebSocket URL (for testing).
    """

    def __init__(
        self,
        markets: List[str],
        resolution_s: int = 60,
        on_candle_close: Optional[Callable[[Candle], None]] = None,
        store=None,
        url: str = _DRIFT_WS_URL,
        **kwargs,
    ):
        super().__init__(url=url, name="drift", **kwargs)
        self._markets = markets
        self._resolution_s = resolution_s
        self._on_candle_close = on_candle_close or (lambda c: None)
        self._store = store

        self._aggregators: Dict[str, CandleAggregator] = {}
        for market in markets:
            self._aggregators[market] = CandleAggregator(
                market=market,
                venue="drift",
                resolution_s=resolution_s,
                on_candle_close=self._on_candle_close,
                store=store,
            )

    async def _connect_ws(self):
        import websockets
        ws = await websockets.connect(self._url)
        return ws

    async def _subscribe(self, ws) -> None:
        import json
        for market in self._markets:
            await ws.send(json.dumps({
                "type": "subscribe",
                "channel": "trades",
                "market": market,
            }))
            await ws.send(json.dumps({
                "type": "subscribe",
                "channel": "funding",
                "market": market,
            }))
        logger.info("Subscribed to %d markets", len(self._markets))

    async def _handle_message(self, raw: dict) -> None:
        channel = raw.get("channel", "")
        if channel == "trades":
            self._handle_trade(raw)
        elif channel == "funding":
            self._handle_funding(raw)

    def _handle_trade(self, msg: dict) -> None:
        market = msg.get("market", "")
        data = msg.get("data", {})
        agg = self._aggregators.get(market)
        if agg is None:
            return
        price = float(data.get("price", 0))
        size = float(data.get("size", 0))
        ts = int(data.get("ts", 0))
        if price > 0 and size > 0 and ts > 0:
            agg.process_trade(price, size, ts)

    def _handle_funding(self, msg: dict) -> None:
        if not self._store:
            return
        market = msg.get("market", "")
        data = msg.get("data", {})
        rate = float(data.get("rate", 0))
        mark_price = float(data.get("mark_price", 0))
        index_price = float(data.get("index_price", 0))
        ts = int(data.get("ts", 0))
        if ts > 0:
            try:
                self._store.upsert_venue_funding([{
                    "venue": "drift",
                    "market": market,
                    "ts": ts,
                    "rate_hourly": rate,
                    "mark_price": mark_price,
                    "index_price": index_price,
                }])
            except Exception as e:
                logger.error("Failed to persist funding: %s", e)

    async def _fallback_poll(self) -> None:
        import asyncio
        import time as _time
        try:
            from .drift_candles import DriftCandleProvider
            provider = DriftCandleProvider()
            now = int(_time.time())
            for market in self._markets:
                candles = await asyncio.to_thread(
                    provider.fetch_candles,
                    market, self._resolution_s,
                    start_ts=now - self._resolution_s * 3,
                    end_ts=now,
                )
                for candle in candles:
                    agg = self._aggregators.get(market)
                    if agg:
                        current = agg.current_bar()
                        if current is None or candle.ts > current.ts:
                            self._on_candle_close(candle)
                            if self._store:
                                self._store.upsert_candles([candle])
        except Exception as e:
            logger.error("Drift fallback poll failed: %s", e)

    async def _backfill_gap(self, disconnect_ts: int, reconnect_ts: int) -> None:
        import asyncio
        try:
            from .drift_candles import DriftCandleProvider
            provider = DriftCandleProvider()
            for market in self._markets:
                candles = await asyncio.to_thread(
                    provider.fetch_candles,
                    market, self._resolution_s,
                    start_ts=disconnect_ts,
                    end_ts=reconnect_ts,
                )
                if candles and self._store:
                    self._store.upsert_candles(candles)
                logger.info("Backfilled %d candles for %s", len(candles), market)
        except Exception as e:
            logger.error("Drift backfill failed: %s", e)
