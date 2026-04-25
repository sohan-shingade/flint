"""D-4.3-websocket — broadcast + replay-on-reconnect tests.

Covers the new ConnectionManager replay-buffer + per-channel seq +
heartbeat-broadcast machinery without spinning up a real WebSocket
server (we use a fake `WebSocket` stand-in that captures `send_text`
and `accept` calls).
"""
from __future__ import annotations

import asyncio
import json

from flint.api.websocket import ConnectionManager


class _FakeWebSocket:
    """Captures everything ConnectionManager sends. No real socket."""

    def __init__(self):
        self.accepted = False
        self.sent: list[str] = []
        self.closed = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, msg: str) -> None:
        if self.closed:
            raise RuntimeError("closed")
        self.sent.append(msg)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestBroadcast:
    def test_broadcast_assigns_monotonic_seq(self):
        mgr = ConnectionManager()
        ws = _FakeWebSocket()
        _run(mgr.connect(ws, "paper:s1"))
        _run(mgr.broadcast("paper:s1", {"equity": 100}))
        _run(mgr.broadcast("paper:s1", {"equity": 110}))
        envelopes = [json.loads(m) for m in ws.sent]
        assert [e["seq"] for e in envelopes] == [0, 1]

    def test_per_channel_seqs_independent(self):
        mgr = ConnectionManager()
        a = _FakeWebSocket()
        b = _FakeWebSocket()
        _run(mgr.connect(a, "paper:s1"))
        _run(mgr.connect(b, "paper:s2"))
        _run(mgr.broadcast("paper:s1", {"equity": 1}))
        _run(mgr.broadcast("paper:s2", {"equity": 2}))
        _run(mgr.broadcast("paper:s1", {"equity": 3}))
        a_seqs = [json.loads(m)["seq"] for m in a.sent]
        b_seqs = [json.loads(m)["seq"] for m in b.sent]
        assert a_seqs == [0, 1]
        assert b_seqs == [0]

    def test_all_channel_receives_every_broadcast(self):
        mgr = ConnectionManager()
        catchall = _FakeWebSocket()
        _run(mgr.connect(catchall, "all"))
        _run(mgr.broadcast("paper:s1", {"x": 1}))
        _run(mgr.broadcast("live:s2", {"y": 2}))
        # Both messages reached the all-channel subscriber
        assert len(catchall.sent) == 2

    def test_dead_socket_pruned(self):
        mgr = ConnectionManager()
        good = _FakeWebSocket()
        dead = _FakeWebSocket()
        dead.closed = True  # send_text will raise
        _run(mgr.connect(good, "paper:s1"))
        _run(mgr.connect(dead, "paper:s1"))
        _run(mgr.broadcast("paper:s1", {"x": 1}))
        # Dead socket is gone; subsequent broadcast only reaches good.
        sent = _run(mgr.broadcast("paper:s1", {"x": 2}))
        assert sent == 1


class TestReplayOnReconnect:
    def test_since_replays_buffered_events(self):
        mgr = ConnectionManager()
        # Broadcast 3 events with no subscribers
        _run(mgr.broadcast("paper:s1", {"i": 0}))
        _run(mgr.broadcast("paper:s1", {"i": 1}))
        _run(mgr.broadcast("paper:s1", {"i": 2}))

        # Connect with since=0 → should replay seqs 1 and 2
        ws = _FakeWebSocket()
        _run(mgr.connect(ws, "paper:s1", since_seq=0))
        replayed = [json.loads(m) for m in ws.sent]
        assert [e["seq"] for e in replayed] == [1, 2]

    def test_since_none_replays_nothing(self):
        mgr = ConnectionManager()
        _run(mgr.broadcast("paper:s1", {"i": 0}))
        ws = _FakeWebSocket()
        _run(mgr.connect(ws, "paper:s1"))
        assert ws.sent == []

    def test_buffer_capped(self):
        """Ring buffer holds at most _REPLAY_BUFFER_SIZE recent events."""
        from flint.api.websocket import _REPLAY_BUFFER_SIZE
        mgr = ConnectionManager()
        # 1 over the cap
        for i in range(_REPLAY_BUFFER_SIZE + 1):
            _run(mgr.broadcast("paper:s1", {"i": i}))
        assert mgr.buffered_count("paper:s1") == _REPLAY_BUFFER_SIZE


class TestHeartbeat:
    def test_ping_broadcasts_to_channel(self):
        mgr = ConnectionManager()
        ws = _FakeWebSocket()
        _run(mgr.connect(ws, "paper:s1"))
        _run(mgr.ping("paper:s1"))
        envelopes = [json.loads(m) for m in ws.sent]
        assert envelopes[0]["type"] == "ping"
        assert "ts" in envelopes[0]


class TestIntrospection:
    def test_connection_count(self):
        mgr = ConnectionManager()
        a = _FakeWebSocket()
        b = _FakeWebSocket()
        _run(mgr.connect(a, "paper:s1"))
        _run(mgr.connect(b, "live:s2"))
        assert mgr.connection_count == 2
        assert mgr.channels() == {"paper:s1", "live:s2"}

    def test_disconnect_decrements(self):
        mgr = ConnectionManager()
        ws = _FakeWebSocket()
        _run(mgr.connect(ws, "paper:s1"))
        assert mgr.connection_count == 1
        mgr.disconnect(ws, "paper:s1")
        assert mgr.connection_count == 0
