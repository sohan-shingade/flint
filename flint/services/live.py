"""services.live — the tenant-scoped kill switch the API/UI call (D20, §3.6).

The live *executor* lives in :mod:`flint.live`; this is the front-door wrapper the
surfaces reach it through — every call takes a ``TenantContext`` and a
``SecretsPort``, so a browser (via ``POST /live/{run_id}/stop``) or the SDK can
pull the plug without touching the engine or a store directly (§4). Venue faults
are translated to the uniform taxonomy (§19.1): a missing run is ``not_found``, a
dropped venue is ``venue_unavailable`` — never a stack trace.
"""

from __future__ import annotations

from flint.live import LiveExecutor, LiveStartRefused, stop_all_live
from flint.ports import SecretsPort, TenantContext, UserDataPort
from flint.venues.hyperliquid import LiveVenueUnavailable, hyperliquid_client_factory

from .errors import NotFoundError, ValidationError, VenueUnavailableError


def stop_live(
    tenant: TenantContext,
    run_id: str | None = None,
    *,
    store: UserDataPort,
    secrets: SecretsPort,
    client_factory=hyperliquid_client_factory,
    flatten: bool = False,
    all_runs: bool = False,
) -> dict[str, object]:
    """Stop a live run (or every running one) for ``tenant`` — the UI kill switch.

    With ``all_runs`` it stops every ``running`` live run; otherwise ``run_id`` is
    required and names the one to stop. ``flatten`` also closes open positions
    (reduce-only). Returns a structured payload of what was cancelled/flattened.
    """
    try:
        if all_runs:
            reports = stop_all_live(
                tenant,
                store=store,
                secrets=secrets,
                client_factory=client_factory,
                flatten=flatten,
            )
            return {"stopped": [r.to_payload() for r in reports]}
        if not run_id:
            raise ValidationError(
                "stop_live needs a run_id (or all_runs=True)",
                hint="pass run_id to stop one run, or all_runs to stop every live run",
            )
        try:
            executor = LiveExecutor.resume(
                tenant=tenant,
                store=store,
                secrets=secrets,
                run_id=run_id,
                client_factory=client_factory,
            )
        except KeyError:
            raise NotFoundError(
                f"no live run {run_id!r} for this tenant",
                hint="check the run id, or list runs with GET /runs",
            ) from None
        except LiveStartRefused as refused:
            raise VenueUnavailableError(refused.message, hint=refused.hint) from refused
        return executor.stop(flatten=flatten).to_payload()
    except LiveVenueUnavailable as unavailable:
        raise VenueUnavailableError(
            "the venue could not be reached to cancel orders",
            detail=str(unavailable),
            hint="retry the kill switch; positions may still be open on the venue",
        ) from unavailable
