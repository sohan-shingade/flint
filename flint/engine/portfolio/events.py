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

# Phase-1 event kinds. Phase 3 adds the engine kinds below as the loop grows
# (kinds are plain strings so new ones need no change to the envelope).
NOOP = "noop"
RUN_STARTED = "run_started"
RUN_FINISHED = "run_finished"

# Phase-3 engine kinds (§6). ORDER_PLACED records an accepted order entering the
# shared fill path; FILL an executed trade; FUNDING a settled funding payment;
# LIQUIDATION a mark-triggered forced close. ORDER_REJECTED / ORDER_CANCELLED are
# the terminal order-state transitions (§6.2) — a fill check failing and an IOC
# remainder / close / run-end cancel respectively. All carry ``ts`` = the domain
# time they occurred at, so replay/fold (slice 3.5) reconstructs state in order.
ORDER_PLACED = "order_placed"
ORDER_REJECTED = "order_rejected"
ORDER_CANCELLED = "order_cancelled"
FILL = "fill"
FUNDING = "funding"
LIQUIDATION = "liquidation"

# EQUITY is the per-bar mark-to-market snapshot (§6.1 bar boundary): total equity
# with its components, emitted once at the end of each locked per-bar sequence so
# the tearsheet can fold an equity curve without re-deriving it from cash moves.
# It is purely additive — a derived observation, never a source of cash movement —
# so ``replay.fold`` ignores it and no existing kind or ordering changes. Payload
# (event_version=1, Decimal-as-str per event-log discipline):
#   equity          = cash + unrealized PnL, valued at the bar-closing mark
#   unrealized      = mark-to-market PnL of open positions at that mark
#   accrued_funding = cumulative funding settled to the account to date. This
#                     engine settles funding discretely into ``cash`` (§6.4), so
#                     there is no separately-held *unpaid* funding — this is the
#                     realized funding cash flow already inside ``cash``, exposed
#                     so the equity curve decomposes into its funding line.
#   cash            = free collateral across accounts (incl. realized PnL/fees/funding)
EQUITY = "equity"


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
