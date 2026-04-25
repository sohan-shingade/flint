"""Tests for WebSocketFeed base class — mocked, no real connections."""
import asyncio
import time
import pytest
from unittest.mock import AsyncMock

from flint.providers.websocket import WebSocketFeed


class MockFeed(WebSocketFeed):
    """Concrete test implementation."""

    def __init__(self, **kwargs):
        super().__init__(url="wss://test.example.com/ws", name="test", **kwargs)
        self.messages_handled = []
        self.fallback_polls = 0
        self.backfill_calls = []
        self._mock_ws = None

    async def _connect_ws(self):
        self._mock_ws = AsyncMock()
        return self._mock_ws

    async def _subscribe(self, ws):
        pass

    async def _handle_message(self, raw):
        self.messages_handled.append(raw)

    async def _fallback_poll(self):
        self.fallback_polls += 1

    async def _backfill_gap(self, disconnect_ts, reconnect_ts):
        self.backfill_calls.append((disconnect_ts, reconnect_ts))


class TestLifecycle:
    def test_initial_state(self):
        feed = MockFeed()
        assert feed.connected is False
        assert feed.name == "test"

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        feed = MockFeed()

        # Override _connect_ws so recv always errors, simulating a broken connection
        async def failing_connect():
            ws = AsyncMock()
            ws.recv = AsyncMock(side_effect=Exception("closed"))
            feed._mock_ws = ws
            return ws
        feed._connect_ws = failing_connect

        task = asyncio.create_task(feed.start())
        await asyncio.sleep(0.05)
        await feed.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert feed.connected is False


class TestHealthCheck:
    def test_health_timeout_detected(self):
        feed = MockFeed(health_timeout_s=0.1)
        feed._connected = True
        feed._last_message_ts = time.time() - 1.0
        assert feed._is_health_timeout() is True

    def test_no_timeout_when_recent_message(self):
        feed = MockFeed(health_timeout_s=30.0)
        feed._connected = True
        feed._last_message_ts = time.time()
        assert feed._is_health_timeout() is False


class TestReconnection:
    def test_backoff_delay_calculation(self):
        feed = MockFeed(max_reconnect_delay_s=60.0)
        assert feed._reconnect_delay(0) == 1.0
        assert feed._reconnect_delay(1) == 2.0
        assert feed._reconnect_delay(2) == 4.0
        assert feed._reconnect_delay(3) == 8.0
        assert feed._reconnect_delay(10) == 60.0


class TestFallback:
    @pytest.mark.asyncio
    async def test_fallback_poll_called_when_disconnected(self):
        feed = MockFeed(fallback_poll_interval_s=0.01)
        feed._connected = False
        feed._running = True
        task = asyncio.create_task(feed._fallback_loop())
        await asyncio.sleep(0.05)
        feed._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert feed.fallback_polls > 0
