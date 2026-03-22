"""Notification abstractions and event types."""
from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("flint.notifications")


@dataclass
class TradingEvent:
    """An event that can trigger a notification."""
    event_type: str  # "fill", "stop_triggered", "drawdown_warning", "session_start", "daily_summary"
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: int = 0


class Notifier(abc.ABC):
    """Base class for notification channels."""

    @abc.abstractmethod
    async def send(self, event: TradingEvent) -> bool:
        """Send a notification. Returns True on success."""
        ...


class NotificationManager:
    """Fan-out notifications to multiple channels."""

    def __init__(self, notifiers: Optional[List[Notifier]] = None):
        self.notifiers = notifiers or []
        self._event_log: List[TradingEvent] = []

    def add_notifier(self, notifier: Notifier) -> None:
        self.notifiers.append(notifier)

    async def notify(self, event: TradingEvent) -> int:
        """Send event to all notifiers. Returns count of successful sends."""
        self._event_log.append(event)
        if not self.notifiers:
            return 0

        sent = 0
        for notifier in self.notifiers:
            try:
                ok = await notifier.send(event)
                if ok:
                    sent += 1
            except Exception as e:
                logger.warning("Notifier %s failed: %s", type(notifier).__name__, e)
        return sent

    @property
    def event_log(self) -> List[TradingEvent]:
        return list(self._event_log)
