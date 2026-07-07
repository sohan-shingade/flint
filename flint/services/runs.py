"""Run Library services — list + compare, tenant-scoped (§11.2, §12).

Thin wrappers over ``research.runlib`` so a surface never reaches into research
directly (§4). ``list_runs`` backs ``GET /api/v1/runs`` (the Run-Library table);
``compare`` backs the two-run diff, carrying runlib's honesty warnings (most
importantly the **different effective range** warning — two Sharpes over
different windows are not the same measurement, §6.3). Every call is scoped to
``tenant`` — a tenant only ever sees its own runs (§2.7).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from flint.data import DataManager
from flint.ports import TenantContext, UserDataPort
from flint.research import compare as _compare
from flint.research import export_bundle as _export_bundle
from flint.research import list_runs as _list_runs
from flint.research import reproduce as _reproduce

from .backtest import BacktestRequest, rerun_events
from .errors import NotFoundError, ValidationError


def list_runs(
    tenant: TenantContext,
    *,
    user_data: UserDataPort,
    strategy: str | None = None,
) -> list[dict[str, Any]]:
    """List ``tenant``'s runs as JSON-safe library rows (optionally one strategy)."""
    return [_row(m) for m in _list_runs(tenant, user_data, strategy=strategy)]


def compare_runs(
    tenant: TenantContext,
    run_ids: Sequence[str],
    *,
    user_data: UserDataPort,
) -> dict[str, Any]:
    """Side-by-side metric table for ``run_ids`` with runlib's honesty warnings."""
    cmp = _compare(tenant, user_data, list(run_ids))
    return {
        "run_ids": list(cmp.run_ids),
        "warnings": list(cmp.warnings),
        "metrics": cmp.metric_table(),
    }


def export_run(
    tenant: TenantContext,
    run_id: str,
    *,
    user_data: UserDataPort,
) -> str:
    """The reproducibility bundle for ``tenant``'s run ``run_id`` as JSON (§11.2).

    Packs the run's manifest head + recorded event stream into a self-contained
    :class:`~flint.research.ReproBundle` (``flint export --run-id``); re-running it
    reproduces the event stream bit-for-bit (§2.10). Raises :class:`NotFoundError`
    when the tenant has no such run (absent or another tenant's — §2.7).
    """
    try:
        bundle = _export_bundle(tenant, user_data, run_id)
    except KeyError:
        raise NotFoundError(f"unknown run {run_id!r}") from None
    return bundle.to_json()


def reproduce_run(
    tenant: TenantContext,
    run_id: str,
    *,
    user_data: UserDataPort,
    data: DataManager,
) -> dict[str, Any]:
    """Re-execute ``tenant``'s run ``run_id`` and verify the event stream (§2.10).

    Rebuilds the original request from the bundle + the recorded summary (which
    carries the fields the bundle schema does not: universe/venues, requested
    range, resolution, capital, fill mode, resolved granularity + engine), re-runs
    it into a throwaway store, and compares streams bit-for-bit via
    ``research.reproduce``. Covers template runs; a sandboxed user-source re-run
    is future work and is refused loudly rather than approximated.
    """
    try:
        bundle = _export_bundle(tenant, user_data, run_id)
        record = user_data.load_run(tenant, run_id)
    except KeyError:
        raise NotFoundError(f"unknown run {run_id!r}") from None
    summary: dict[str, Any] = dict(record.summary or {})
    if bundle.strategy_source:
        raise ValidationError(
            "reproduce covers template runs only (for now)",
            detail="this run was a sandboxed user-source backtest",
            hint="re-running user source through the sandbox is a v1.x item",
        )
    if summary.get("verdict") == "rejected":
        raise ValidationError(
            f"run {run_id!r} was rejected — there is no event stream to reproduce",
            detail=str(summary.get("note", "")),
            hint="only verdict=ok runs are reproducible",
        )

    requested = summary.get("requested_range") or {}
    request = BacktestRequest(
        run_id=run_id,
        strategy=bundle.strategy_name,
        universe=tuple(
            summary.get("universe") or sorted({d.market for d in bundle.data_manifest})
        ),
        venues=tuple(
            summary.get("venues") or sorted({d.venue for d in bundle.data_manifest})
        ),
        start_ms=int(requested.get("start_ms") or bundle.effective_start_ts or 0),
        end_ms=int(requested.get("end_ms") or bundle.effective_end_ts or 0),
        resolution_s=int(summary.get("resolution_s") or 3600),
        fill_mode=str(summary.get("fill_mode") or "auto"),
        seed=int(bundle.seed or 0),
        initial_capital=str(summary.get("initial_capital") or "100000"),
        overrides=dict(bundle.params),
        engine=str(summary.get("engine") or "auto"),
        granularity=str(summary.get("granularity") or "auto"),
    )
    result = _reproduce(bundle, lambda _b: rerun_events(tenant, request, data=data))
    return {
        "run_id": run_id,
        "reproduced": result.reproduced,
        "n_original": result.n_original,
        "n_reproduced": result.n_reproduced,
        "mismatch_index": result.mismatch_index,
        "detail": result.describe(),
    }


def import_legacy_runs(
    tenant: TenantContext,
    *,
    user_data: UserDataPort,
    path: str,
) -> list[str]:
    """Import legacy DuckDB run metadata into ``tenant``'s Run Library (§19.6, (g)).

    Read-only on the legacy file; idempotent on ``legacy:<name>`` so re-running is a
    no-op. Returns the imported run_ids. The DuckDB bridge is imported lazily — the
    services import graph never pulls it until an operator actually runs the import.
    """
    from flint.data.migrate import import_legacy_runs_from_duckdb

    return import_legacy_runs_from_duckdb(tenant, user_data, path)


def run_results(
    tenant: TenantContext,
    run_id: str,
    *,
    user_data: UserDataPort,
) -> dict[str, Any]:
    """The structured result of ``tenant``'s run for an agent to act on (§13.2/§13.3).

    Returns the persisted summary (metrics, equity curve, cost attribution,
    per-segment fill-fidelity lines, in-run rejections, verdict) plus a
    **per-trade log** folded from the run's FILL event stream — the machine-
    readable block §13.3 requires so an agent can see *why* PnL happened (funding
    vs trading vs fees vs slippage) and at what fill-fidelity tier. Raises
    :class:`NotFoundError` when the tenant has no such run (absent or another's).
    """
    from flint.engine.portfolio import FILL, EventLog

    try:
        record = user_data.load_run(tenant, run_id)
    except KeyError:
        raise NotFoundError(f"unknown run {run_id!r}") from None
    summary = dict(record.summary or {})

    trades: list[dict[str, Any]] = []
    fidelity_by_tier: dict[str, int] = {}
    for event in EventLog(user_data, tenant, run_id).read():
        if event.kind != FILL:
            continue
        p = event.payload
        tier = p.get("fidelity_tier", "?")
        fidelity_by_tier[tier] = fidelity_by_tier.get(tier, 0) + 1
        trades.append(
            {
                "ts": event.ts,
                "market": p.get("market"),
                "venue": p.get("venue"),
                "side": p.get("side"),
                "price": p.get("price"),
                "size": p.get("size"),
                "fee": p.get("fee"),
                "realized_pnl": p.get("realized_pnl"),
                "fidelity_tier": tier,
                "slippage_bps": p.get("slippage_bps"),
                "liquidity": p.get("liquidity"),
            }
        )

    return {
        "run_id": run_id,
        "verdict": summary.get("verdict", "ok"),
        "status": record.status,
        "metrics": summary.get("metrics"),
        "deflated_sharpe": summary.get("deflated_sharpe"),
        "n_trials": summary.get("n_trials"),
        "win_rate": summary.get("win_rate"),
        "cost_attribution": summary.get("cost"),
        "equity_curve": summary.get("equity_curve", []),
        "per_segment_fidelity": summary.get("fidelity_lines", []),
        "fills_by_fidelity_tier": fidelity_by_tier,
        "trades": trades,
        "rejections": summary.get("rejections", []),
        "notes": summary.get("notes", []),
        "effective_range": summary.get("effective_range"),
        "rejected": summary.get("rejected"),
        "validation": summary.get("validation"),
    }


def _row(manifest: Any) -> dict[str, Any]:
    return {
        "run_id": manifest.run_id,
        "strategy": manifest.strategy_name,
        "kind": manifest.kind,
        "created_ts": manifest.created_ts,
        "effective_start_ts": manifest.effective_start_ts,
        "effective_end_ts": manifest.effective_end_ts,
        "metrics": dict(manifest.metrics),
        "seed": manifest.seed,
        "engine_version": manifest.engine_version,
        "provenance": manifest.provenance,
        "note": manifest.note,
    }
