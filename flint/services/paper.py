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

from typing import TYPE_CHECKING, Any

from flint.ports import TenantContext, UserDataPort

from .errors import NotFoundError, ValidationError

if TYPE_CHECKING:
    from flint.live import PaperSession


def start_paper(
    tenant: TenantContext,
    *,
    user_data: UserDataPort,
    run_id: str,
    strategy: str,
    market: str,
    resolution_s: int = 3600,
    initial_capital: str = "100000",
    seed: int = 0,
    overrides: dict[str, Any] | None = None,
) -> "PaperSession":
    """Start (and persist the head of) a fresh paper session for ``tenant`` (§6.7).

    The paper session is the *same* engine fed live rather than by historical replay
    (the parity promise) — this is the front door the SDK/CLI use to create one. The
    returned :class:`~flint.live.PaperSession` is fed a live-feed connection by the
    caller's run loop; its monitor head is streamed by :func:`paper_snapshot`. Raises
    :class:`ValidationError` for an unknown template (user error), mirroring the
    backtest front door.
    """
    from flint.live import PaperSession
    from flint.strategy.templates.registry import TemplateNotFound, get_template

    try:
        get_template(strategy)
    except TemplateNotFound:
        raise ValidationError(
            f"unknown strategy template {strategy!r}",
            detail="strategy must be a registered template name",
            hint="call list_templates for valid names",
        ) from None

    return PaperSession.create(
        tenant=tenant,
        store=user_data,
        run_id=run_id,
        template=strategy,
        market=market,
        resolution_s=resolution_s,
        initial_capital=initial_capital,
        seed=seed,
        overrides=overrides,
    )


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
