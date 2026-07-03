"""Paper-monitor read service — the data behind ``WS /paper/{id}/stream`` (§6.7, §12).

The live monitor streams a paper session's positions, funding accrual, liq
distance, and drift. The paper *runner* (``flint.live.PaperSession``, 5.6)
produces those and persists them in the run's ``RunRecord.summary`` via the
tenant-scoped ``UserDataPort``; this service reads that head back for a tenant.
The WS surface pushes snapshots — for a session still running, later slices push
each new bar; in v1 the snapshot is the persisted head, always tenant-scoped so
one tenant never streams another's run (§2.7).
"""

from __future__ import annotations

from typing import Any

from flint.ports import TenantContext, UserDataPort

from .errors import NotFoundError


def paper_snapshot(
    tenant: TenantContext, run_id: str, *, user_data: UserDataPort
) -> dict[str, Any]:
    """The current monitor snapshot for ``tenant``'s paper run ``run_id``.

    Raises :class:`NotFoundError` if the tenant has no such run (absent, or owned
    by another tenant — indistinguishable by design, §2.7).
    """
    try:
        record = user_data.load_run(tenant, run_id)
    except KeyError:
        raise NotFoundError(f"unknown paper run {run_id!r}") from None
    summary = dict(record.summary)
    return {
        "run_id": record.run_id,
        "kind": record.kind,
        "status": record.status,
        "positions": summary.get("positions", []),
        "funding_accrued": summary.get("funding_accrued"),
        "liq_distances_pct": summary.get("liq_distances_pct", {}),
        "drift": summary.get("drift", {}),
        "alerts": summary.get("alerts", []),
        "final_equity": summary.get("final_equity"),
    }
