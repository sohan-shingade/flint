"""Tests for notification system."""
from __future__ import annotations


import pytest

from flint.notifications.base import NotificationManager, Notifier, TradingEvent


class MockNotifier(Notifier):
    def __init__(self, should_fail=False):
        self.sent = []
        self.should_fail = should_fail

    async def send(self, event):
        if self.should_fail:
            raise ConnectionError("mock fail")
        self.sent.append(event)
        return True


class TestTradingEvent:
    def test_create_event(self):
        event = TradingEvent(
            event_type="fill",
            message="Bought 10 SOL at 150",
            data={"price": 150},
            timestamp=1000,
        )
        assert event.event_type == "fill"
        assert event.timestamp == 1000


class TestNotificationManager:
    @pytest.mark.asyncio
    async def test_notify_no_notifiers(self):
        mgr = NotificationManager()
        event = TradingEvent(event_type="test", message="hello")
        sent = await mgr.notify(event)
        assert sent == 0
        assert len(mgr.event_log) == 1

    @pytest.mark.asyncio
    async def test_notify_single(self):
        mock = MockNotifier()
        mgr = NotificationManager([mock])
        event = TradingEvent(event_type="fill", message="trade executed")
        sent = await mgr.notify(event)
        assert sent == 1
        assert len(mock.sent) == 1

    @pytest.mark.asyncio
    async def test_notify_multiple(self):
        m1 = MockNotifier()
        m2 = MockNotifier()
        mgr = NotificationManager([m1, m2])
        event = TradingEvent(event_type="fill", message="trade")
        sent = await mgr.notify(event)
        assert sent == 2

    @pytest.mark.asyncio
    async def test_failed_notifier_doesnt_break(self):
        m1 = MockNotifier(should_fail=True)
        m2 = MockNotifier()
        mgr = NotificationManager([m1, m2])
        event = TradingEvent(event_type="error", message="oops")
        sent = await mgr.notify(event)
        assert sent == 1  # m2 succeeded
        assert len(m2.sent) == 1

    @pytest.mark.asyncio
    async def test_add_notifier(self):
        mgr = NotificationManager()
        mock = MockNotifier()
        mgr.add_notifier(mock)
        await mgr.notify(TradingEvent(event_type="test", message="hi"))
        assert len(mock.sent) == 1
