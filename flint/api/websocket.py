"""WebSocket connection manager for real-time streaming."""
from __future__ import annotations

import json
import logging
from typing import Dict, Set

from fastapi import WebSocket

logger = logging.getLogger("flint.websocket")


class ConnectionManager:
    """Manages WebSocket connections and broadcasts events by channel."""

    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, channel: str = "all") -> None:
        """Accept a WebSocket connection and subscribe to a channel."""
        await websocket.accept()
        if channel not in self._connections:
            self._connections[channel] = set()
        self._connections[channel].add(websocket)
        logger.info("WebSocket connected to channel '%s'", channel)

    def disconnect(self, websocket: WebSocket, channel: str = "all") -> None:
        """Remove a WebSocket connection."""
        if channel in self._connections:
            self._connections[channel].discard(websocket)

    async def broadcast(self, channel: str, data: dict) -> int:
        """Broadcast a message to all connections on a channel.

        Also broadcasts to 'all' channel subscribers.
        Returns number of messages sent.
        """
        message = json.dumps({"channel": channel, **data})
        sent = 0
        targets = set()

        if channel in self._connections:
            targets.update(self._connections[channel])
        if "all" in self._connections:
            targets.update(self._connections["all"])

        dead = []
        for ws in targets:
            try:
                await ws.send_text(message)
                sent += 1
            except Exception:
                dead.append((ws, channel))

        # Clean up dead connections
        for ws, ch in dead:
            self.disconnect(ws, ch)

        return sent

    @property
    def connection_count(self) -> int:
        return sum(len(conns) for conns in self._connections.values())
