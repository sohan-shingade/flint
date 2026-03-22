"""Telegram notification channel."""
from __future__ import annotations

import httpx

from .base import Notifier, TradingEvent


class TelegramNotifier(Notifier):
    """Send notifications via Telegram Bot API."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    async def send(self, event: TradingEvent) -> bool:
        if not self.bot_token or not self.chat_id:
            return False

        text = f"*Flint* | `{event.event_type}`\n{event.message}"
        async with httpx.AsyncClient() as client:
            resp = await client.post(self._url, json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown",
            }, timeout=10)
            return resp.status_code == 200
