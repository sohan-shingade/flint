"""The upcaster registry — how old stored events stay replayable (§2.10).

The compatibility contract is **upcast-on-read**: on-disk events are never
rewritten. When an event kind gains or changes a field, we bump its
``event_version`` and register an upcaster that transforms a ``vN`` payload into
a ``vN+1`` payload. At read time every stored row is walked up to the latest
version it has a chain for, *then* folded. Without this, the first field ever
added to ``Fill`` would make every previously stored run unreplayable — and the
Python models will iterate before the Rust port lands, so this is needed from
the first persisted run, not later.

An upcaster transforms only the **payload** and returns the new payload; the
registry owns the version bump, so an upcaster cannot forget to increment or
skip a version. Registration is keyed by ``(kind, from_version)`` and the target
is always ``from_version + 1`` — no version may be skipped.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

PayloadUpcaster = Callable[[Mapping[str, Any]], Mapping[str, Any]]


class UpcasterRegistry:
    """A registry of ``(kind, from_version) -> payload transform``.

    Production code shares the module-level ``REGISTRY``; tests construct their
    own instance so a fixture corpus can exercise upcasting without touching
    global state.
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, int], PayloadUpcaster] = {}

    def register(self, kind: str, from_version: int) -> Callable[[PayloadUpcaster], PayloadUpcaster]:
        """Decorator: register a ``vN -> v(N+1)`` payload transform for ``kind``."""

        def deco(fn: PayloadUpcaster) -> PayloadUpcaster:
            key = (kind, from_version)
            if key in self._by_key:
                raise ValueError(f"duplicate upcaster for {kind} v{from_version}")
            self._by_key[key] = fn
            return fn

        return deco

    def latest_version(self, kind: str, from_version: int = 1) -> int:
        """Highest version reachable for ``kind`` by following the upcaster chain."""
        version = from_version
        while (kind, version) in self._by_key:
            version += 1
        return version

    def upcast_to_latest(self, row: Mapping[str, Any]) -> dict[str, Any]:
        """Return ``row`` walked up to the latest version (a new dict; input untouched).

        Applies each registered ``(kind, version)`` transform in turn, bumping the
        version by exactly one per step, until no further upcaster exists.
        """
        kind = row["kind"]
        version = int(row.get("event_version", 1))
        payload: Mapping[str, Any] = row.get("payload", {})
        while (kind, version) in self._by_key:
            payload = self._by_key[(kind, version)](payload)
            version += 1
        return {**row, "event_version": version, "payload": dict(payload)}


# The process-wide registry used by EventLog unless a caller injects its own.
REGISTRY = UpcasterRegistry()
