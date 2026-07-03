"""engine.portfolio — cross-position risk plus the event log, replay/fold, and snapshots (§2.10, §6.6).

Phase 1 ships the event-sourcing seam: the ``Event`` envelope, the upcaster
registry (upcast-on-read), and the ``EventLog`` writer/reader over
``UserDataPort``. Replay/fold and snapshots land with the Phase-3 engine.
"""

from __future__ import annotations

from .event_log import EventLog
from .events import (
    FILL,
    FUNDING,
    LIQUIDATION,
    NOOP,
    ORDER_PLACED,
    RUN_FINISHED,
    RUN_STARTED,
    Event,
)
from .upcasters import REGISTRY, UpcasterRegistry

__all__ = [
    "Event",
    "EventLog",
    "UpcasterRegistry",
    "REGISTRY",
    "NOOP",
    "RUN_STARTED",
    "RUN_FINISHED",
    "ORDER_PLACED",
    "FILL",
    "FUNDING",
    "LIQUIDATION",
]
