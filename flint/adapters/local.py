"""Local reference adapters — the Phase-1 walking-skeleton implementations (§2.7).

Every port has a concrete fulfilment here that runs on a laptop with no network
and no external service, so the whole skeleton is exercisable in tests. These
are deliberately in-memory: they prove the *seams* (tenant isolation, the Arrow
boundary, quota-carrying jobs) without pulling the Phase-2 data-lake schema
forward. The durable DuckDB market/user store adapters land with the data layer
(task #2); swapping them in is a one-line wiring change, which is the whole
point of ports.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

import pyarrow as pa

from flint.ports import (
    EventBusPort,
    EventHandler,
    Identity,
    JobRunnerPort,
    MarketDataPort,
    ResourceQuota,
    RunRecord,
    SecretsPort,
    TenantContext,
    UserDataPort,
)

T = TypeVar("T")


class InMemoryMarketData(MarketDataPort):
    """Tenant-agnostic market store backed by per-``(venue, market)`` Arrow tables.

    Proves the Arrow boundary and the half-open ``[start_ts, end_ts)`` load
    contract without a database.
    """

    def __init__(self) -> None:
        self._tables: dict[tuple[str, str], pa.Table] = {}

    def save_candles(self, venue: str, market: str, candles: pa.Table) -> int:
        key = (venue, market)
        existing = self._tables.get(key)
        combined = pa.concat_tables([existing, candles]) if existing else candles
        self._tables[key] = combined
        return candles.num_rows

    def load_candles(
        self, venue: str, market: str, start_ts: int, end_ts: int
    ) -> pa.Table:
        table = self._tables.get((venue, market))
        if table is None:
            return pa.table({})
        if "ts" not in table.column_names:
            return table
        import pyarrow.compute as pc

        mask = pc.and_(
            pc.greater_equal(table["ts"], start_ts),
            pc.less(table["ts"], end_ts),
        )
        return table.filter(mask)


class InMemoryUserData(UserDataPort):
    """Per-tenant run store — the concrete proof of the cross-leak seam (§2.7).

    Storage is partitioned by ``tenant.tenant_id``; a read for one tenant can
    never surface another tenant's row, and a cross-tenant ``load_run`` raises
    ``KeyError`` exactly as if the row did not exist.
    """

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, RunRecord]] = {}
        # event rows keyed by (tenant_id, run_id), append-only
        self._events: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def save_run(self, tenant: TenantContext, run: RunRecord) -> str:
        self._runs.setdefault(tenant.tenant_id, {})[run.run_id] = run
        return run.run_id

    def load_run(self, tenant: TenantContext, run_id: str) -> RunRecord:
        try:
            return self._runs[tenant.tenant_id][run_id]
        except KeyError:
            # Constant message whether the run is absent or owned by another
            # tenant — a probing caller cannot tell the difference (no leak).
            raise KeyError("run not found for tenant") from None

    def list_runs(self, tenant: TenantContext) -> list[RunRecord]:
        return list(self._runs.get(tenant.tenant_id, {}).values())

    def append_events(
        self, tenant: TenantContext, run_id: str, rows: Sequence[Mapping[str, Any]]
    ) -> int:
        log = self._events.setdefault((tenant.tenant_id, run_id), [])
        # Store defensive copies so callers can't mutate persisted rows.
        log.extend(dict(r) for r in rows)
        return len(log)

    def load_events(
        self, tenant: TenantContext, run_id: str
    ) -> list[dict[str, Any]]:
        return [dict(r) for r in self._events.get((tenant.tenant_id, run_id), [])]


class InProcessJobRunner(JobRunnerPort):
    """Run jobs synchronously in the current process (laptop default).

    Records the quota it was handed but does not enforce it — enforcement is the
    sandbox's job (§8.3, slice 1.5). It must never silently drop the quota, so
    the last-seen quota is retained for inspection/wiring.
    """

    def __init__(self) -> None:
        self.last_quota: ResourceQuota | None = None

    def submit(
        self, tenant: TenantContext, fn: Callable[[], T], quota: ResourceQuota
    ) -> T:
        self.last_quota = quota
        return fn()


class EnvSecrets(SecretsPort):
    """Resolve secrets from the process environment (server-side ``.env``).

    Namespaced per tenant as ``FLINT_SECRET__{tenant_id}__{name}`` so a
    multi-tenant server never returns one tenant's key to another; the ``local``
    tenant also falls back to a bare ``name`` for convenience.
    """

    def get_secret(self, tenant: TenantContext, name: str) -> str | None:
        scoped = f"FLINT_SECRET__{tenant.tenant_id}__{name}"
        if scoped in os.environ:
            return os.environ[scoped]
        if tenant.tenant_id == "local":
            return os.environ.get(name)
        return None


class InMemoryEventBus(EventBusPort):
    """Ephemeral pub/sub keyed by ``(tenant_id, topic)`` — an isolation seam."""

    def __init__(self) -> None:
        self._subs: dict[tuple[str, str], list[EventHandler]] = {}

    def publish(
        self, tenant: TenantContext, topic: str, payload: Mapping[str, Any]
    ) -> None:
        for handler in self._subs.get((tenant.tenant_id, topic), []):
            handler(payload)

    def subscribe(
        self, tenant: TenantContext, topic: str, handler: EventHandler
    ) -> None:
        self._subs.setdefault((tenant.tenant_id, topic), []).append(handler)


class LocalIdentity(Identity):
    """The single implicit tenant on a self-hosted install."""

    def current(self) -> TenantContext:
        return TenantContext.local()
