"""Storage ports — the two halves of "where data lives" (§2.7).

The engine never knows *where* data lives. It depends on these promises, never
on DuckDB or S3 directly, so the laptop→cloud swap is one adapter change, not a
hunt through the codebase (D12).

The storage promise is deliberately **split in two**, because market data and
user data have opposite tenancy:

* ``MarketDataPort`` — candles/funding/depth are the *same for everyone*. It is
  **tenant-agnostic**: no ``TenantContext`` parameter. Arrow ``Table`` is the
  boundary type (columnar, zero-copy to Parquet/DuckDB).
* ``UserDataPort`` — runs/orders/positions/models are *per-user*. **Every** call
  takes a ``TenantContext`` and every query filters by it. Passing a tenant to a
  market-data call that ignores it would confuse every adapter, which is exactly
  why the two are separate classes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any

import pyarrow as pa

from .records import RunRecord
from .tenant import TenantContext


class MarketDataPort(ABC):
    """Save/fetch market data, keyed ``(venue, market, ts)``. Shared namespace.

    Tenant-agnostic by construction — there is no ``TenantContext`` here. The
    v1 laptop adapter is a DuckDB file; a future cloud adapter is Parquet on a
    hosted lake (§9). Callers exchange Arrow ``Table``s at this boundary.
    """

    @abstractmethod
    def save_candles(self, venue: str, market: str, candles: pa.Table) -> int:
        """Persist candles for ``(venue, market)``; return the row count written."""
        ...

    @abstractmethod
    def load_candles(
        self, venue: str, market: str, start_ts: int, end_ts: int
    ) -> pa.Table:
        """Return candles for ``(venue, market)`` with ``start_ts <= ts < end_ts``.

        Half-open on the right, matching the no-look-ahead rules in
        ``core.time`` (a bar timestamped exactly ``end_ts`` is excluded).
        """
        ...


class UserDataPort(ABC):
    """Save/fetch per-user data (runs, orders, positions, models) — §2.7.

    The tenant seam lives here: **every** method takes a ``TenantContext`` as its
    first argument, and every adapter partitions storage by ``tenant.tenant_id``.
    A load of another tenant's ``run_id`` must behave exactly as if the row did
    not exist (§2.7 cross-leak test) — no existence leak across tenants.
    """

    @abstractmethod
    def save_run(self, tenant: TenantContext, run: RunRecord) -> str:
        """Persist ``run`` under ``tenant``; return its ``run_id``."""
        ...

    @abstractmethod
    def load_run(self, tenant: TenantContext, run_id: str) -> RunRecord:
        """Return ``tenant``'s run ``run_id``, or raise ``KeyError`` if absent.

        Absent *for this tenant* includes rows owned by another tenant — the
        caller cannot tell the difference, by design.
        """
        ...

    @abstractmethod
    def list_runs(self, tenant: TenantContext) -> list[RunRecord]:
        """Return every run owned by ``tenant`` (never another tenant's)."""
        ...

    # --- event log (§2.10) -------------------------------------------------
    # Events are stored as primitive rows (plain dicts), never engine types,
    # so this port stays infra-only. The engine's EventLog (emit seam) owns
    # the typed Event <-> row conversion and upcast-on-read. Append-only:
    # stored rows are never rewritten.

    @abstractmethod
    def append_events(
        self, tenant: TenantContext, run_id: str, rows: Sequence[Mapping[str, Any]]
    ) -> int:
        """Append event rows to ``tenant``'s run ``run_id``; return the new total.

        Rows are opaque, serialisable dicts. Ordering is preserved; nothing is
        overwritten (append-only). Scoped to the tenant like every other call.
        """
        ...

    @abstractmethod
    def load_events(
        self, tenant: TenantContext, run_id: str
    ) -> list[dict[str, Any]]:
        """Return ``tenant``'s events for ``run_id`` in append order.

        Empty if the run has no events or belongs to another tenant — the
        caller cannot distinguish the two (no cross-tenant existence leak).
        """
        ...
