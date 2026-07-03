"""The backtest front door (§4, §18 item 1).

``run_backtest`` is the one place a backtest is orchestrated. It takes a
``TenantContext`` (like every service function), and composes the Phase-1 seams
into a walking skeleton: it persists a run through ``UserDataPort``, runs the
strategy **inside the sandbox** (via the unconditional ``run_strategy_sandboxed``
entry, carried on the ``JobRunnerPort`` quota), and appends **versioned events**
through the ``EventLog``. The engine is still empty — no bars, no fills — so this
is a *no-op* backtest end-to-end; the honest per-bar engine (Phase 3) slots in
behind this same surface without changing the front door.

Layering: this depends only on ports + the domain (engine.portfolio, sandbox),
never on a concrete adapter. The composition root (sdk/api) injects the ports.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from flint.engine.portfolio import NOOP, RUN_FINISHED, RUN_STARTED, EventLog
from flint.ports import (
    JobRunnerPort,
    ResourceQuota,
    RunRecord,
    TenantContext,
    UserDataPort,
)
from flint.strategy.sandbox import SandboxError, run_strategy_sandboxed


@dataclass(frozen=True)
class BacktestRequest:
    """What to run. Minimal for the skeleton; grows into universe/range (§2.11)."""

    run_id: str
    strategy_source: str
    entry: str = "run"
    venue: str = "hyperliquid"
    universe: tuple[str, ...] = ("SOL-PERP",)


@dataclass(frozen=True)
class BacktestResult:
    """The head of a finished run — the durable detail is the event log."""

    run_id: str
    status: str  # "done" | "failed"
    events: int
    summary: Mapping[str, Any] = field(default_factory=dict)


def run_backtest(
    tenant: TenantContext,
    request: BacktestRequest,
    *,
    user_data: UserDataPort,
    job_runner: JobRunnerPort,
    quota: ResourceQuota | None = None,
    now_ms: int = 0,
) -> BacktestResult:
    """Run ``request`` for ``tenant`` end-to-end: persist -> sandbox -> events.

    Returns a ``BacktestResult``. A strategy failure (raise or denied import)
    yields ``status="failed"`` with the error in the summary — the run is still
    recorded and closed, never left dangling.
    """
    quota = quota or ResourceQuota.default()

    # 1. Record the run as in-flight, scoped to the tenant.
    user_data.save_run(
        tenant,
        RunRecord(run_id=request.run_id, kind="backtest", status="running", created_ts=now_ms),
    )

    # 2. Open the event log (durability behind UserDataPort) and mark the start.
    log = EventLog(user_data, tenant, request.run_id)
    log.emit(
        RUN_STARTED,
        {"venue": request.venue, "universe": list(request.universe)},
        ts=now_ms,
    )

    # 3. Run the strategy INSIDE THE SANDBOX, carried on the job runner's quota.
    #    The empty engine hands it only its context; there are no bars yet.
    ctx = {"venue": request.venue, "universe": list(request.universe)}

    def _job() -> Any:
        return run_strategy_sandboxed(
            request.strategy_source, request.entry, ctx, quota=quota
        )

    try:
        strategy_out = job_runner.submit(tenant, _job, quota)
        status = "done"
        summary: dict[str, Any] = {"bars": 0, "strategy_result": strategy_out}
        log.emit(NOOP, {"strategy_result": strategy_out}, ts=now_ms)
    except SandboxError as exc:
        status = "failed"
        summary = {"bars": 0, "error": str(exc)}

    # 4. Close the run: final event + persisted head record.
    log.emit(RUN_FINISHED, {"status": status}, ts=now_ms)
    user_data.save_run(
        tenant,
        RunRecord(
            run_id=request.run_id,
            kind="backtest",
            status=status,
            created_ts=now_ms,
            summary=summary,
        ),
    )
    return BacktestResult(
        run_id=request.run_id, status=status, events=len(log), summary=summary
    )
