"""``EventBusPort`` — publish "a thing happened" to interested subscribers (§2.7).

This is the *ephemeral* pub/sub bus ("a fill happened", "a run finished") used
to fan out live updates — distinct from the durable, append-only **event log**
(§2.10) that makes runs reproducible. The bus is lossy across restarts by
design; authoritative state lives in the store. Laptop adapter is an in-memory
bus; cloud is Redis pub/sub.

Topics are tenant-scoped: a subscriber only ever sees its own tenant's events,
so the bus is another isolation seam, not just a convenience.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from typing import Any

from .tenant import TenantContext

EventHandler = Callable[[Mapping[str, Any]], None]


class EventBusPort(ABC):
    """Publish/subscribe to tenant-scoped topics. Ephemeral; not the event log."""

    @abstractmethod
    def publish(
        self, tenant: TenantContext, topic: str, payload: Mapping[str, Any]
    ) -> None:
        """Deliver ``payload`` to every handler subscribed to ``(tenant, topic)``."""
        ...

    @abstractmethod
    def subscribe(
        self, tenant: TenantContext, topic: str, handler: EventHandler
    ) -> None:
        """Register ``handler`` for ``(tenant, topic)``. Sees only this tenant."""
        ...
