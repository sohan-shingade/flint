"""``EventLog`` — the emit seam + upcast-on-read reader (§2.10).

This is the single place the engine appends to a run's log (``emit``) and the
single place it reads events back for folding (``read``). Durability lives
*behind* ``UserDataPort``: the log serialises each ``Event`` to a primitive row
and hands it to the port, which persists it per ``(tenant, run_id)``. The
Phase-1 in-memory adapter keeps rows in a list; the Phase-2 DuckDB adapter
(task #2.1) will make them durable — same seam, no engine change.

The log is the **sequencer**: it assigns each event a monotonic ``seq`` starting
where the run left off, so an append-only log survives resumption. On read it
runs every stored row through the upcaster registry, so callers always fold the
latest shape while on-disk rows stay byte-for-byte as written.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flint.ports import TenantContext, UserDataPort

from .events import Event
from .upcasters import REGISTRY, UpcasterRegistry


class EventLog:
    """Append-only writer + upcast-on-read reader for one run's events."""

    def __init__(
        self,
        store: UserDataPort,
        tenant: TenantContext,
        run_id: str,
        *,
        registry: UpcasterRegistry = REGISTRY,
    ) -> None:
        self._store = store
        self._tenant = tenant
        self._run_id = run_id
        self._registry = registry
        # Resume the sequence where any prior events left off (append-only).
        self._seq = len(store.load_events(tenant, run_id))

    def emit(
        self,
        kind: str,
        payload: Mapping[str, Any] | None = None,
        *,
        ts: int = 0,
        event_version: int = 1,
    ) -> Event:
        """Append one event; assign its ``seq``; persist it via ``UserDataPort``."""
        event = Event(
            kind=kind,
            payload=dict(payload or {}),
            ts=ts,
            event_version=event_version,
            seq=self._seq,
        )
        self._store.append_events(self._tenant, self._run_id, [event.to_row()])
        self._seq += 1
        return event

    def read(self) -> list[Event]:
        """Load all events, upcast each to its latest version, return typed events."""
        rows = self._store.load_events(self._tenant, self._run_id)
        return [Event.from_row(self._registry.upcast_to_latest(r)) for r in rows]

    def read_raw(self) -> list[dict[str, Any]]:
        """The stored rows exactly as written (no upcasting) — proves immutability."""
        return self._store.load_events(self._tenant, self._run_id)

    def __len__(self) -> int:
        return self._seq
