"""Tests for WebSocket connection manager."""
from __future__ import annotations

import pytest

from flint.api.websocket import ConnectionManager


class FakeWebSocket:
    """Minimal WebSocket mock for testing."""
    def __init__(self):
        self.accepted = False
        self.sent = []
        self.closed = False

    async def accept(self):
        self.accepted = True

    async def send_text(self, text):
        self.sent.append(text)

    async def receive_text(self):
        return ""


class TestConnectionManager:
    @pytest.mark.asyncio
    async def test_connect(self):
        mgr = ConnectionManager()
        ws = FakeWebSocket()
        await mgr.connect(ws, "portfolio")
        assert ws.accepted
        assert mgr.connection_count == 1

    @pytest.mark.asyncio
    async def test_broadcast(self):
        mgr = ConnectionManager()
        ws = FakeWebSocket()
        await mgr.connect(ws, "trades")
        sent = await mgr.broadcast("trades", {"type": "fill", "pnl": 50})
        assert sent == 1
        assert len(ws.sent) == 1
        assert '"fill"' in ws.sent[0]

    @pytest.mark.asyncio
    async def test_broadcast_to_all(self):
        mgr = ConnectionManager()
        ws = FakeWebSocket()
        await mgr.connect(ws, "all")
        sent = await mgr.broadcast("trades", {"data": "test"})
        assert sent == 1

    @pytest.mark.asyncio
    async def test_disconnect(self):
        mgr = ConnectionManager()
        ws = FakeWebSocket()
        await mgr.connect(ws, "portfolio")
        mgr.disconnect(ws, "portfolio")
        assert mgr.connection_count == 0

    @pytest.mark.asyncio
    async def test_multiple_channels(self):
        mgr = ConnectionManager()
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect(ws1, "trades")
        await mgr.connect(ws2, "portfolio")
        assert mgr.connection_count == 2
        sent = await mgr.broadcast("trades", {"data": "x"})
        assert sent == 1  # only ws1 on trades channel
        assert len(ws1.sent) == 1
        assert len(ws2.sent) == 0
