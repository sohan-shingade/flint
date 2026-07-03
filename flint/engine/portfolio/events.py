"""The event envelope — the atom of the append-only log (§2.10).

Every meaningful thing that happens in a run (order placed, fill, funding
charged, liquidation, universe-membership change, run lifecycle) is appended as
an ``Event``. Final state is *computed by replaying* these, never stored
directly — that is what makes a run reproducible, auditable, and exportable.

The **on-disk form is a plain dict** (``to_row``/``from_row``), not this
dataclass. Ports and the store deal only in those primitive rows, so the
persistence layer never imports engine types and the wire format is trivially
serialisable to Parquet/JSON. ``Event`` is the typed, in-memory convenience the
engine folds over.

Every event carries an ``event_version: int``. Adding a field to an event kind
bumps its version and registers an upcaster (``upcasters.py``); stored rows are
**never rewritten** — they are upcast *on read* before folding.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# Phase-1 event kinds. Phase 3 adds ORDER_PLACED / FILL / FUNDING /
# LIQUIDATION / UNIVERSE_MEMBERSHIP as the engine grows (kinds are plain
# strings so new ones need no change here).
NOOP = "noop"
RUN_STARTED = "run_started"
RUN_FINISHED = "run_finished"


@dataclass(frozen=True, slots=True)
class Event:
    """One immutable entry in a run's event log.

    ``seq`` is the monotonic position assigned by the writer (``EventLog``);
    ``ts`` is domain time in unix ms (0 for lifecycle events with no bar).
    ``payload`` is the kind-specific body, versioned by ``event_version``.
    """

    kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    ts: int = 0
    event_version: int = 1
    seq: int = -1

    def to_row(self) -> dict[str, Any]:
        """Render to the primitive on-disk/wire form (a plain dict)."""
        return {
            "kind": self.kind,
            "event_version": self.event_version,
            "ts": self.ts,
            "seq": self.seq,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Event":
        """Rebuild a typed event from a stored row."""
        return cls(
            kind=row["kind"],
            payload=dict(row.get("payload", {})),
            ts=int(row.get("ts", 0)),
            event_version=int(row.get("event_version", 1)),
            seq=int(row.get("seq", -1)),
        )
