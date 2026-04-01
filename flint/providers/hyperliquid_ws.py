"""HyperliquidWebSocketFeed — real-time candle, orderbook, and order update data.

Subscribes to Hyperliquid's native candle channel (no CandleAggregator needed),
L2 orderbook snapshots, and user order update events.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Callable, Dict, List, Optional

from ..models import Candle
from .websocket import WebSocketFeed

logger = logging.getLogger("flint.hyperliquid_ws")

_NETWORK_WS_URLS = {
    "testnet": "wss://api.hyperliquid-testnet.xyz/ws",
    "mainnet": "wss://api.hyperliquid.xyz/ws",
}

_FLINT_TO_HL = {
    "SOL-PERP": "SOL", "BTC-PERP": "BTC", "ETH-PERP": "ETH",
    "DOGE-PERP": "DOGE", "AVAX-PERP": "AVAX", "LINK-PERP": "LINK",
    "ARB-PERP": "ARB", "SUI-PERP": "SUI", "XRP-PERP": "XRP",
    "OP-PERP": "OP", "INJ-PERP": "INJ", "TIA-PERP": "TIA",
    "SEI-PERP": "SEI", "WIF-PERP": "WIF", "JUP-PERP": "JUP",
    "RENDER-PERP": "RENDER", "BNB-PERP": "BNB",
}
_HL_TO_FLINT = {v: k for k, v in _FLINT_TO_HL.items()}

_INTERVAL_TO_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400,
}


class HyperliquidWebSocketFeed(WebSocketFeed):
    """WebSocket feed for Hyperliquid real-time candle, L2 book, and order data.

    Hyperliquid sends pre-built candles (no CandleAggregator needed). Candle close
    is detected by comparing open timestamps — when a new message has a different
    open timestamp than the cached one, the previous candle is considered closed.

    Args:
        markets: List of Flint-style market symbols (e.g. "SOL-PERP").
        network: "testnet" or "mainnet".
        candle_interval: Candle interval string ("1m", "5m", "15m", "1h", "4h", "1d").
        on_candle_close: Callback fired when a candle bar closes.
        on_order_update: Callback fired for each order update event.
        user_address: Hyperliquid wallet address for orderUpdates subscription.
        store: Optional FlintStore for persisting candles.
        l2_persist_interval_s: Minimum seconds between L2 book persistence writes.
    """

    def __init__(
        self,
        markets: List[str],
        network: str = "testnet",
        candle_interval: str = "1m",
        on_candle_close: Optional[Callable[[Candle], None]] = None,
        on_order_update: Optional[Callable[[dict], None]] = None,
        user_address: Optional[str] = None,
        store=None,
        l2_persist_interval_s: int = 60,
        **kwargs,
    ):
        url = _NETWORK_WS_URLS.get(network, _NETWORK_WS_URLS["testnet"])
        super().__init__(url=url, name="hyperliquid", **kwargs)
        self._markets = markets
        self._candle_interval = candle_interval
        self._on_candle_close = on_candle_close or (lambda c: None)
        self._on_order_update = on_order_update or (lambda u: None)
        self._user_address = user_address
        self._store = store
        self._l2_persist_interval_s = l2_persist_interval_s

        self._current_candles: Dict[str, dict] = {}
        self._orderbooks: Dict[str, dict] = {}
        self._last_l2_persist: float = 0.0

    def get_orderbook(self, market: str) -> Optional[dict]:
        """Return the latest L2 orderbook snapshot for a market, or None."""
        return self._orderbooks.get(market)

    async def _connect_ws(self):
        import websockets
        return await websockets.connect(self._url)

    async def _subscribe(self, ws) -> None:
        for market in self._markets:
            coin = _FLINT_TO_HL.get(market, market)
            await ws.send(json.dumps({
                "method": "subscribe",
                "subscription": {"type": "candle", "coin": coin, "interval": self._candle_interval},
            }))
            await ws.send(json.dumps({
                "method": "subscribe",
                "subscription": {"type": "l2Book", "coin": coin},
            }))
        if self._user_address:
            await ws.send(json.dumps({
                "method": "subscribe",
                "subscription": {"type": "orderUpdates", "user": self._user_address},
            }))

    async def _handle_message(self, raw: dict) -> None:
        channel = raw.get("channel", "")
        if channel == "candle":
            self._handle_candle(raw.get("data", {}))
        elif channel == "l2Book":
            self._handle_l2_book(raw.get("data", {}))
        elif channel == "orderUpdates":
            self._handle_order_updates(raw.get("data", []))

    def _handle_candle(self, data: dict) -> None:
        coin = data.get("s", "")
        market = _HL_TO_FLINT.get(coin)
        if market is None:
            return

        open_time = data.get("t", 0)
        prev = self._current_candles.get(market)

        if prev is not None and prev.get("t") != open_time:
            resolution_s = _INTERVAL_TO_SECONDS.get(self._candle_interval, 60)
            candle = Candle(
                ts=prev["t"] // 1000,
                open=float(prev["o"]),
                high=float(prev["h"]),
                low=float(prev["l"]),
                close=float(prev["c"]),
                volume=float(prev["v"]),
                market=market,
                resolution_s=resolution_s,
                venue="hyperliquid",
            )
            self._on_candle_close(candle)
            if self._store:
                try:
                    self._store.upsert_candles([candle])
                except Exception as e:
                    logger.error("Failed to persist candle: %s", e)

        self._current_candles[market] = data

    def _handle_l2_book(self, data: dict) -> None:
        coin = data.get("coin", "")
        market = _HL_TO_FLINT.get(coin)
        if market is None:
            return
        self._orderbooks[market] = data

    def _handle_order_updates(self, data: list) -> None:
        for update in data:
            self._on_order_update(update)

    async def _fallback_poll(self) -> None:
        try:
            import httpx
            resolution_s = _INTERVAL_TO_SECONDS.get(self._candle_interval, 60)
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - resolution_s * 3 * 1000
            base = self._url.replace("wss://", "https://").replace("/ws", "/info")
            async with httpx.AsyncClient(timeout=10) as client:
                for market in self._markets:
                    coin = _FLINT_TO_HL.get(market, market)
                    resp = await client.post(base, json={
                        "type": "candleSnapshot",
                        "req": {"coin": coin, "interval": self._candle_interval,
                                "startTime": start_ms, "endTime": now_ms},
                    })
                    if resp.status_code == 200:
                        candles = resp.json()
                        if candles:
                            last = candles[-1]
                            candle = Candle(
                                ts=last["t"] // 1000,
                                open=float(last["o"]), high=float(last["h"]),
                                low=float(last["l"]), close=float(last["c"]),
                                volume=float(last["v"]),
                                market=market, resolution_s=resolution_s,
                                venue="hyperliquid",
                            )
                            self._on_candle_close(candle)
        except Exception as e:
            logger.error("Hyperliquid fallback poll failed: %s", e)

    async def _backfill_gap(self, disconnect_ts: int, reconnect_ts: int) -> None:
        try:
            import httpx
            resolution_s = _INTERVAL_TO_SECONDS.get(self._candle_interval, 60)
            base = self._url.replace("wss://", "https://").replace("/ws", "/info")
            async with httpx.AsyncClient(timeout=10) as client:
                for market in self._markets:
                    coin = _FLINT_TO_HL.get(market, market)
                    resp = await client.post(base, json={
                        "type": "candleSnapshot",
                        "req": {"coin": coin, "interval": self._candle_interval,
                                "startTime": disconnect_ts * 1000,
                                "endTime": reconnect_ts * 1000},
                    })
                    if resp.status_code == 200:
                        raw_candles = resp.json()
                        candles = []
                        for c in raw_candles:
                            candles.append(Candle(
                                ts=c["t"] // 1000,
                                open=float(c["o"]), high=float(c["h"]),
                                low=float(c["l"]), close=float(c["c"]),
                                volume=float(c["v"]),
                                market=market, resolution_s=resolution_s,
                                venue="hyperliquid",
                            ))
                        if candles and self._store:
                            self._store.upsert_candles(candles)
                        logger.info("Backfilled %d candles for %s", len(candles), market)
        except Exception as e:
            logger.error("Hyperliquid backfill failed: %s", e)
