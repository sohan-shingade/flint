"""The live Hyperliquid WebSocket ``WsMessageSource`` (§9.1, §12).

The only place in ``recorders/`` that opens a socket. Everything testable —
frame classification, buffering, coverage windows — lives behind the
``WsMessageSource`` seam and is exercised with ``ReplayWsSource`` over recorded
fragments (D26); this wrapper is deliberately thin: connect, subscribe, yield
``(channel, data)`` pairs, keep the connection alive with HL's app-level ping.

``flint data record`` drives it; a ``KeyboardInterrupt`` (Ctrl-C) simply ends
:meth:`messages`, and the CLI closes the recorder session — the coverage ledger
ends at the last observed event, never beyond it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from .hyperliquid import WsMessageSource

HYPERLIQUID_WS_URL = "wss://api.hyperliquid.xyz/ws"

# Idle window before an app-level ping. HL drops connections idle for ~60s;
# any received frame resets the window.
_PING_AFTER_S = 30.0

# Channels a capture session may subscribe. ``l2Book`` is valid but off by
# default at the call sites (volume; §9.2 tee defaults).
RECORDABLE_CHANNELS = frozenset({"trades", "bbo", "l2Book", "activeAssetCtx"})


def coin_of_market(market: str) -> str:
    """Flint market symbol -> HL coin id: ``SOL-PERP`` -> ``SOL``."""
    return market.removesuffix("-PERP")


def subscribe_messages(markets: list[str], channels: list[str]) -> list[dict]:
    """The HL subscribe payloads for a capture session (pure, testable).

    One ``{"method": "subscribe", "subscription": {type, coin}}`` per
    ``(channel, market)`` pair, in a deterministic order.
    """
    unknown = sorted(set(channels) - RECORDABLE_CHANNELS)
    if unknown:
        raise ValueError(
            f"unrecordable channels {unknown}; expected a subset of "
            f"{sorted(RECORDABLE_CHANNELS)}"
        )
    return [
        {
            "method": "subscribe",
            "subscription": {"type": channel, "coin": coin_of_market(market)},
        }
        for channel in sorted(channels)
        for market in markets
    ]


class HyperliquidWsSource(WsMessageSource):
    """A blocking live WS source: subscribe once, yield data frames forever.

    Control frames (``subscriptionResponse``, ``pong``) are consumed here;
    only data channels reach the recorder. The socket library is imported
    lazily so nothing network-shaped loads in the mocked test suite.
    """

    def __init__(
        self,
        markets: list[str],
        channels: list[str],
        *,
        url: str = HYPERLIQUID_WS_URL,
    ) -> None:
        self._markets = list(markets)
        self._channels = list(channels)
        self._url = url
        self.subscriptions = subscribe_messages(self._markets, self._channels)

    def messages(self) -> Iterator[tuple[str, Any]]:
        from websockets.sync.client import connect  # lazy: network-only path

        with connect(self._url) as ws:
            for sub in self.subscriptions:
                ws.send(json.dumps(sub))
            while True:
                try:
                    raw = ws.recv(timeout=_PING_AFTER_S)
                except TimeoutError:
                    ws.send(json.dumps({"method": "ping"}))
                    continue
                frame = json.loads(raw)
                channel = frame.get("channel")
                if channel in RECORDABLE_CHANNELS:
                    yield channel, frame.get("data")
