"""Discord webhook notification channel."""
from __future__ import annotations

import httpx

from .base import Notifier, TradingEvent


class DiscordNotifier(Notifier):
    """Send notifications via Discord webhook."""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def send(self, event: TradingEvent) -> bool:
        if not self.webhook_url:
            return False

        content = f"**Flint** | `{event.event_type}`\n{event.message}"
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.webhook_url, json={
                "content": content,
            }, timeout=10)
            return resp.status_code in (200, 204)
