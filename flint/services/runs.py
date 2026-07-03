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

from flint.ports import TenantContext, UserDataPort
from flint.research import compare as _compare
from flint.research import export_bundle as _export_bundle
from flint.research import list_runs as _list_runs

from .errors import NotFoundError


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
