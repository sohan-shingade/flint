"""Persistence DTOs carried across the user-data port (§2.7).

These are the records ``UserDataPort`` saves and loads. They are deliberately
thin in Phase 1 — a run is identified and stamped with a status and a summary
blob; the rich per-bar/per-fill history lives in the event log (§2.10) and is
folded on read, not stored here. ``tenant_id`` is *not* a field: the tenant is
carried by the ``TenantContext`` on every call and stamped by the adapter, so a
record can never disagree with the tenant it is stored under.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RunRecord:
    """The persisted head of a backtest/paper/live run (§6.2).

    ``run_id`` is the caller-supplied idempotency key. ``summary`` is an opaque
    result blob (final equity, trade count, drift metrics) filled once the run
    completes; the authoritative history is replayed from the event log.
    """

    run_id: str
    kind: str = "backtest"  # "backtest" | "paper" | "live"
    status: str = "created"  # "created" | "running" | "done" | "failed"
    created_ts: int = 0  # unix ms
    summary: Mapping[str, Any] = field(default_factory=dict)
