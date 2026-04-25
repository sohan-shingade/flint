"""WebSocket connection manager for real-time streaming.

D-4.3-websocket (Wave 3) extends the channel broadcaster with:
  - Per-session ring buffer of recent events for replay on reconnect.
    Clients send `?since=<seq>`; server replays everything it still
    has cached and then streams live.
  - Heartbeat: server can `ping(channel)` to test liveness; client
    is expected to drop the socket on 30s silence.

Channel naming convention:
  `paper:{session_id}`  → paper trading session updates
  `live:{session_id}`   → live trading session updates
  `all`                 → catch-all (legacy)
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from typing import Deque, Dict, Optional, Set, Tuple

from fastapi import WebSocket

logger = logging.getLogger("flint.websocket")


# Ring buffer size per channel — bounds memory under burst.
_REPLAY_BUFFER_SIZE = 500


class ConnectionManager:
    """Manages WebSocket connections, broadcasts events by channel,
    and replays missed events on reconnect."""

    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {}
        # Per-channel ring of (seq, ts, payload_str) for replay.
        self._buffers: Dict[str, Deque[Tuple[int, float, str]]] = {}
        # Per-channel monotonic sequence number assigned on broadcast.
        self._seq: Dict[str, int] = {}

    # --- connection lifecycle -------------------------------------------

    async def connect(
        self,
        websocket: WebSocket,
        channel: str = "all",
        since_seq: Optional[int] = None,
    ) -> None:
        """Accept a WebSocket connection. When `since_seq` is set, replay
        every buffered event with seq > since_seq before streaming live
        — gives clients an at-least-once delivery story on reconnect."""
        await websocket.accept()
        self._connections.setdefault(channel, set()).add(websocket)

        if since_seq is not None:
            for seq, _ts, payload in self._buffers.get(channel, ()):
                if seq <= since_seq:
                    continue
                try:
                    await websocket.send_text(payload)
                except Exception:
                    self._connections[channel].discard(websocket)
                    return

        logger.info("WebSocket connected to channel '%s' (since=%s)",
                    channel, since_seq)

    def disconnect(self, websocket: WebSocket, channel: str = "all") -> None:
        if channel in self._connections:
            self._connections[channel].discard(websocket)

    # --- broadcast ------------------------------------------------------

    async def broadcast(self, channel: str, data: dict) -> int:
        """Broadcast a message to all connections on a channel and the
        catch-all `all` channel. Returns number of messages successfully
        sent. The message is stamped with a monotonic per-channel seq
        and cached in the channel's ring buffer for replay."""
        seq = self._seq.get(channel, 0)
        self._seq[channel] = seq + 1
        envelope = {"channel": channel, "seq": seq, **data}
        message = json.dumps(envelope)

        # Cache for replay before send so a slow first-broadcast doesn't
        # drop the message from the buffer if no clients are connected.
        buf = self._buffers.setdefault(channel, deque(maxlen=_REPLAY_BUFFER_SIZE))
        buf.append((seq, time.time(), message))

        sent = 0
        targets = set()
        if channel in self._connections:
            targets.update(self._connections[channel])
        if channel != "all" and "all" in self._connections:
            targets.update(self._connections["all"])

        dead = []
        for ws in targets:
            try:
                await ws.send_text(message)
                sent += 1
            except Exception:
                dead.append(ws)

        for ws in dead:
            for chset in self._connections.values():
                chset.discard(ws)

        return sent

    async def ping(self, channel: str) -> int:
        """Send a heartbeat to all subscribers of `channel`. Returns
        the count delivered. Clients should drop the connection if
        they go 30s without seeing a ping or any other message."""
        return await self.broadcast(channel, {"type": "ping", "ts": time.time()})

    # --- introspection --------------------------------------------------

    @property
    def connection_count(self) -> int:
        return sum(len(conns) for conns in self._connections.values())

    def channels(self) -> Set[str]:
        return set(self._connections.keys())

    def buffered_count(self, channel: str) -> int:
        return len(self._buffers.get(channel, ()))
