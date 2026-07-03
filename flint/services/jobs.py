"""Backtest job lifecycle — submit/status/cancel/result + idempotency (§12).

The API needs a job model even though the v1 laptop runner is synchronous: long
optimizations need status/cancel and agents need idempotent submission (§12). This
registry holds that lifecycle. The work itself runs through the injected
``JobRunnerPort`` (the in-process runner today; a queue-backed one in the cloud),
so the same call site is correct in both.

``idempotency_key`` is tenant-scoped: a retried POST with the same key returns the
existing ``run_id`` instead of double-executing. Job records are held in memory
(the running server owns them); the durable head is still the tenant-scoped
``RunRecord`` in ``UserDataPort``. An unexpected exception marks the job
``failed`` with an incident id — the §19.1 "loud bug" surface — while a
funding-gap rejection is a *completed* job whose result carries the structured
``rejected`` payload (data, not a fault).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from flint.data import DataManager
from flint.ports import JobRunnerPort, ResourceQuota, TenantContext, UserDataPort

from .backtest import BacktestOutcome, BacktestRequest, run_backtest
from .errors import NotFoundError, ServiceError

_TERMINAL = frozenset({"done", "failed", "cancelled"})


@dataclass
class JobRecord:
    """The in-memory lifecycle head for one submitted backtest."""

    run_id: str
    state: str = "queued"  # queued | running | done | failed | cancelled
    progress_pct: int = 0
    queue_position: int = 0
    outcome: BacktestOutcome | None = None
    error: dict[str, Any] | None = None  # set on state == "failed" (§19.1 bug)

    def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "run_id": self.run_id,
            "state": self.state,
            "progress_pct": self.progress_pct,
            "queue_position": self.queue_position,
        }
        if self.error is not None:
            out["error"] = self.error
        return out


class BacktestJobs:
    """Tracks submitted backtests and drives them through the job runner."""

    def __init__(
        self,
        *,
        user_data: UserDataPort,
        data: DataManager,
        job_runner: JobRunnerPort,
        quota: ResourceQuota | None = None,
    ) -> None:
        self._user_data = user_data
        self._data = data
        self._job_runner = job_runner
        self._quota = quota or ResourceQuota.default()
        self._jobs: dict[tuple[str, str], JobRecord] = {}
        self._idem: dict[tuple[str, str], str] = {}

    def submit(
        self,
        tenant: TenantContext,
        request: BacktestRequest,
        *,
        idempotency_key: str | None = None,
        now_ms: int = 0,
    ) -> str:
        """Run ``request`` and return its ``run_id`` (existing one on a dup key).

        Synchronous in v1: the job runs to completion before returning, so the
        returned ``run_id`` is already queryable. A :class:`ServiceError` (e.g. an
        unknown template) propagates and leaves no phantom job.
        """
        def _job() -> BacktestOutcome:
            return run_backtest(
                tenant,
                request,
                user_data=self._user_data,
                data=self._data,
                now_ms=now_ms,
            )

        return self._track(tenant, request.run_id, _job, idempotency_key=idempotency_key)

    def submit_source(
        self,
        tenant: TenantContext,
        *,
        source: str,
        run_id: str,
        universe: Sequence[str] = ("SOL-PERP",),
        venues: Sequence[str] = ("hyperliquid",),
        start_ms: int = 0,
        end_ms: int = 0,
        resolution_s: int = 3600,
        fill_mode: str = "auto",
        seed: int = 0,
        initial_capital: str = "100000",
        overrides: Mapping[str, Any] | None = None,
        signal_venues: Sequence[str] = (),
        granularity: str = "auto",
        idempotency_key: str | None = None,
        now_ms: int = 0,
    ) -> str:
        """Run user ``source`` through the sandboxed §13.2 path as a tracked job.

        Same lifecycle as :meth:`submit` — the returned ``run_id`` is queryable via
        ``status``/``result``. A validation failure (or a contained in-run sandbox
        fault) is a *completed* job whose summary carries ``verdict="invalid"``
        (§19.1 data, not an HTTP error); a funding gap completes as ``rejected``.
        """
        from .strategy_source import run_backtest_source

        def _job() -> BacktestOutcome:
            return run_backtest_source(
                tenant,
                source=source,
                run_id=run_id,
                universe=tuple(universe),
                venues=tuple(venues),
                start_ms=start_ms,
                end_ms=end_ms,
                resolution_s=resolution_s,
                fill_mode=fill_mode,
                seed=seed,
                initial_capital=initial_capital,
                overrides=dict(overrides or {}),
                signal_venues=tuple(signal_venues),
                granularity=granularity,
                user_data=self._user_data,
                data=self._data,
                now_ms=now_ms,
            )

        return self._track(tenant, run_id, _job, idempotency_key=idempotency_key)

    def _track(
        self,
        tenant: TenantContext,
        run_id: str,
        job: "Callable[[], BacktestOutcome]",
        *,
        idempotency_key: str | None = None,
    ) -> str:
        """Drive ``job`` through the runner under the shared job lifecycle."""
        if idempotency_key is not None:
            existing = self._idem.get((tenant.tenant_id, idempotency_key))
            if existing is not None:
                return existing

        key = (tenant.tenant_id, run_id)
        rec = JobRecord(run_id=run_id, state="running")
        self._jobs[key] = rec
        if idempotency_key is not None:
            self._idem[(tenant.tenant_id, idempotency_key)] = run_id

        try:
            rec.outcome = self._job_runner.submit(tenant, job, self._quota)
        except ServiceError:
            # User error / known fault — no job left behind; the surface maps it.
            del self._jobs[key]
            if idempotency_key is not None:
                self._idem.pop((tenant.tenant_id, idempotency_key), None)
            raise
        except Exception as exc:  # noqa: BLE001 — §19.1: an unexpected bug is loud
            rec.state = "failed"
            rec.error = {
                "code": "internal",
                "message": "the backtest hit an unexpected error — this is a Flint bug",
                "detail": str(exc),
                "incident_id": uuid.uuid4().hex,
            }
            return run_id

        rec.state = "done"
        rec.progress_pct = 100
        return run_id

    def status(self, tenant: TenantContext, run_id: str) -> dict[str, Any]:
        return self._get(tenant, run_id).status()

    def result(self, tenant: TenantContext, run_id: str) -> dict[str, Any]:
        """Return the run's result summary; raise NotFound until it is ``done``.

        A ``rejected`` verdict is a completed run — its summary carries the
        structured ``rejected`` payload and is returned here (200), not a 4xx.
        """
        rec = self._get(tenant, run_id)
        if rec.state != "done" or rec.outcome is None:
            raise NotFoundError(
                f"run {run_id!r} has no result yet",
                detail=f"state is {rec.state!r}",
                hint="poll the status endpoint until state is 'done'",
            )
        return dict(rec.outcome.summary)

    def cancel(self, tenant: TenantContext, run_id: str) -> dict[str, Any]:
        """Cancel a non-terminal job (idempotent). Terminal jobs are unchanged."""
        rec = self._get(tenant, run_id)
        if rec.state not in _TERMINAL:
            rec.state = "cancelled"
        return {"run_id": run_id, "state": rec.state}

    def _get(self, tenant: TenantContext, run_id: str) -> JobRecord:
        rec = self._jobs.get((tenant.tenant_id, run_id))
        if rec is None:
            raise NotFoundError(f"unknown run {run_id!r}")
        return rec


def new_run_id() -> str:
    """A fresh server-side run id (the caller-facing idempotency key of §6.2)."""
    return uuid.uuid4().hex
