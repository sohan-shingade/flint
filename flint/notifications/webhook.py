"""Generic webhook notification channel."""
from __future__ import annotations

import httpx

from .base import Notifier, TradingEvent


class WebhookNotifier(Notifier):
    """Send notifications as JSON POST to any URL."""

    def __init__(self, url: str, headers: dict = None):
        self.url = url
        self.headers = headers or {}

    async def send(self, event: TradingEvent) -> bool:
        if not self.url:
            return False

        payload = {
            "event_type": event.event_type,
            "message": event.message,
            "data": event.data,
            "timestamp": event.timestamp,
            "source": "flint",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.url, json=payload,
                headers=self.headers, timeout=10,
            )
            return 200 <= resp.status_code < 300
