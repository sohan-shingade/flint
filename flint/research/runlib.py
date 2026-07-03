"""Run Library + reproducibility export — research memory for humans and agents (§11.2).

Every backtest/paper/live run leaves a **head record** the user can come back to,
sort, diff, and re-run. This module is the research-memory surface the design pitches
against "scattered notebooks": each run persists a stable ``run_id``, the strategy
source + its content hash, the parameter snapshot, the post-gate effective range, the
fidelity-tier summary, the full metrics, the engine version, the seed, the
``lake_revision``, and an optional free-text tag + note (§11.2).

Three capabilities sit on top of that persisted head:

* :func:`persist_run` / :func:`load_run` / :func:`list_runs` — write the head through
  ``UserDataPort`` (never storage directly) and read it back, tenant-scoped on every
  call (§2.7). The rich metadata rides in ``RunRecord.summary`` — the design's opaque
  result blob — so nothing about the ports/records DTO changes.
* :func:`compare` — the agent-and-human ``compare(run_ids)`` (§11.2/§13). It lines runs
  up side by side and **warns when they cover different effective ranges** (their
  Sharpes are not directly comparable, §6.3), when engine versions differ (re-run to
  compare, §17), or when the lake revision differs (the underlying data was revised).
* :func:`export_bundle` → a **reproducibility bundle**: strategy code + params + data
  manifest (sources, ranges, ``lake_revision``) + engine version + seed, plus the
  recorded event stream. :func:`reproduce` re-executes it through an injected runner and
  asserts the emitted event stream is **bit-for-bit identical** — event sourcing (§2.10)
  is what makes "re-runs bit-for-bit" real, not a claim.

Numeric policy (§5): timestamps are integer unix-ms UTC (bar start); the seed is an int
(or ``None`` when genuinely unknown — a legacy import must not fabricate one, D26);
metrics are opaque and carried verbatim from the caller's tearsheet. Everything this
module persists is reduced to JSON-safe primitives so it round-trips identically through
both the in-memory reference adapter and the durable DuckDB adapter (which stores
``summary`` as a JSON string).

This module depends only downward (``flint.ports``); it never reaches up into services
or surfaces (§4).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from flint.ports import RunRecord, TenantContext, UserDataPort

# Bumped only when the bundle wire format changes; read back for upcasting.
BUNDLE_VERSION = 1

# Sentinels for fields a legacy run genuinely never recorded (D26 — never a guess).
# These mirror the migrate.py provenance discipline ("legacy", not a fabricated number).
LEGACY_SENTINEL = "legacy"

# compare() warning categories (§11.2/§13/§17).
DIFFERENT_RANGE_WARNING = "different_effective_range"
DIFFERENT_ENGINE_WARNING = "different_engine_version"
DIFFERENT_LAKE_WARNING = "different_lake_revision"


def content_hash(source: str) -> str:
    """SHA-256 hex digest of strategy source — the run's content-addressed identity.

    The hash is over the exact bytes of the strategy file, so two runs of "the same
    strategy" are only the same when the code is byte-identical. A bundle carries both
    the source and this hash; :meth:`ReproBundle.verify_integrity` recomputes it to catch
    a tampered or truncated bundle before anyone trusts a re-run.
    """
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def engine_version() -> str:
    """The installed Flint engine version, stamped on every run (§17, repro bundle).

    Read from installed package metadata; falls back to ``"unknown"`` rather than
    inventing a version when metadata is unavailable (D26). Runs made under different
    engine versions are flagged by :func:`compare` rather than silently compared (§17).
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("flint")
        except PackageNotFoundError:
            return "unknown"
    except Exception:  # pragma: no cover - importlib always present on 3.11+
        return "unknown"


@dataclass(frozen=True)
class DataSource:
    """One entry in a run's data manifest: which recorded series it read, over what
    range, at what lake revision (§9/§11.2). The ``lake_revision`` is what makes a
    re-run after an exchange's retroactive funding correction *say* the data changed
    (new content-addressed key) instead of silently producing different numbers (§9)."""

    venue: str
    market: str
    kind: str  # "candles" | "funding" | "depth" | "oi"
    start_ts: int  # unix ms, inclusive
    end_ts: int  # unix ms, exclusive (half-open, §core.time)
    lake_revision: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "venue": self.venue,
            "market": self.market,
            "kind": self.kind,
            "start_ts": int(self.start_ts),
            "end_ts": int(self.end_ts),
            "lake_revision": self.lake_revision,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "DataSource":
        return cls(
            venue=d["venue"],
            market=d["market"],
            kind=d["kind"],
            start_ts=int(d["start_ts"]),
            end_ts=int(d["end_ts"]),
            lake_revision=d.get("lake_revision", ""),
        )


@dataclass(frozen=True)
class RunManifest:
    """The full §11.2 head record for one run — everything needed to find it later,
    compare it honestly, and rebuild it exactly.

    Persisted inside ``RunRecord.summary`` (the opaque result blob), so the ports/records
    DTO is untouched. ``seed`` is ``int | None``: ``None`` means "genuinely unknown" (a
    legacy import must not fabricate a seed and thereby fake determinism, D26).
    ``metrics`` and ``fidelity`` are opaque JSON-safe maps carried verbatim from the
    caller's tearsheet (§11.1) and per-segment fidelity roll-up (§6.3)."""

    run_id: str
    strategy_name: str
    strategy_source: str
    params: Mapping[str, Any] = field(default_factory=dict)
    effective_start_ts: int | None = None  # post-gate range (§6.3), unix ms
    effective_end_ts: int | None = None
    fidelity: Mapping[str, Any] = field(default_factory=dict)
    metrics: Mapping[str, Any] = field(default_factory=dict)
    engine_version: str = ""
    seed: int | None = None
    lake_revision: str = ""
    data_manifest: tuple[DataSource, ...] = ()
    tag: str = ""
    note: str = ""
    kind: str = "backtest"  # "backtest" | "paper" | "live"
    created_ts: int = 0  # unix ms
    provenance: str = "native"  # "native" | "legacy"

    @property
    def strategy_hash(self) -> str:
        """Content hash of the strategy source (§11.2). Derived, never stored twice."""
        return content_hash(self.strategy_source)

    def to_summary(self) -> dict[str, Any]:
        """Reduce to the JSON-safe blob stored in ``RunRecord.summary``.

        Only primitives, lists, and maps — so it survives ``json.dumps`` in the DuckDB
        adapter and reloads byte-identical. The strategy hash is stored alongside the
        source as a redundant integrity check (a corrupted source is caught on read)."""
        return {
            "schema": "runlib/manifest",
            "strategy_name": self.strategy_name,
            "strategy_source": self.strategy_source,
            "strategy_hash": self.strategy_hash,
            "params": dict(self.params),
            "effective_start_ts": self.effective_start_ts,
            "effective_end_ts": self.effective_end_ts,
            "fidelity": dict(self.fidelity),
            "metrics": dict(self.metrics),
            "engine_version": self.engine_version,
            "seed": self.seed,
            "lake_revision": self.lake_revision,
            "data_manifest": [s.to_dict() for s in self.data_manifest],
            "tag": self.tag,
            "note": self.note,
            "provenance": self.provenance,
        }

    @classmethod
    def from_record(cls, record: RunRecord) -> "RunManifest":
        """Reconstruct a manifest from a persisted ``RunRecord`` (its ``summary`` blob).

        ``run_id``, ``kind``, and ``created_ts`` come from the record head; everything
        else from the summary. Tolerant of ``json`` having turned tuples into lists."""
        s = record.summary
        return cls(
            run_id=record.run_id,
            strategy_name=s.get("strategy_name", ""),
            strategy_source=s.get("strategy_source", ""),
            params=dict(s.get("params", {})),
            effective_start_ts=s.get("effective_start_ts"),
            effective_end_ts=s.get("effective_end_ts"),
            fidelity=dict(s.get("fidelity", {})),
            metrics=dict(s.get("metrics", {})),
            engine_version=s.get("engine_version", ""),
            seed=s.get("seed"),
            lake_revision=s.get("lake_revision", ""),
            data_manifest=tuple(
                DataSource.from_dict(d) for d in s.get("data_manifest", [])
            ),
            tag=s.get("tag", ""),
            note=s.get("note", ""),
            kind=record.kind,
            created_ts=record.created_ts,
            provenance=s.get("provenance", "native"),
        )

    def to_record(self, *, status: str = "done") -> RunRecord:
        """Build the persisted ``RunRecord`` head that carries this manifest."""
        return RunRecord(
            run_id=self.run_id,
            kind=self.kind,
            status=status,
            created_ts=self.created_ts,
            summary=self.to_summary(),
        )


# --- persistence (through UserDataPort, tenant-scoped) -----------------------


def persist_run(
    tenant: TenantContext,
    port: UserDataPort,
    manifest: RunManifest,
    *,
    events: Sequence[Mapping[str, Any]] = (),
    status: str = "done",
) -> str:
    """Persist a run's head record (and optional event stream) via ``UserDataPort``.

    The head is idempotent on ``run_id`` (the port upserts). When ``events`` are given
    they are appended to the run's append-only event log (§2.10) — the ground truth a
    reproducibility bundle exports and :func:`reproduce` checks against. Every call is
    tenant-scoped; there is no default-tenant shortcut (§2.7)."""
    run_id = port.save_run(tenant, manifest.to_record(status=status))
    if events:
        port.append_events(tenant, run_id, events)
    return run_id


def load_run(tenant: TenantContext, port: UserDataPort, run_id: str) -> RunManifest:
    """Load one run's manifest for ``tenant``; raises ``KeyError`` if absent for it.

    A run owned by another tenant is indistinguishable from one that never existed —
    the port enforces that, and this call inherits it (§2.7)."""
    return RunManifest.from_record(port.load_run(tenant, run_id))


def list_runs(
    tenant: TenantContext, port: UserDataPort, *, strategy: str | None = None
) -> list[RunManifest]:
    """List ``tenant``'s run manifests, optionally filtered to one strategy name.

    Backs the UI Run Library table (§11.2). Sorting is left to the surface; this returns
    every matching run the tenant owns (never another tenant's, §2.7)."""
    out = [RunManifest.from_record(r) for r in port.list_runs(tenant)]
    if strategy is not None:
        out = [m for m in out if m.strategy_name == strategy]
    return out


# --- compare (§11.2/§13) -----------------------------------------------------


@dataclass(frozen=True)
class RunComparison:
    """Side-by-side comparison of two-or-more runs with honesty warnings.

    ``warnings`` carries the reasons the runs may not be directly comparable — most
    importantly a **different effective range** (§6.3): two Sharpes over different
    windows are not the same measurement, and the design promises ``compare()`` says so
    rather than letting the numbers imply otherwise."""

    run_ids: tuple[str, ...]
    manifests: tuple[RunManifest, ...]
    warnings: tuple[str, ...] = ()

    def metric_table(self) -> dict[str, list[Any]]:
        """One row per metric key seen, one column per run (missing → ``None``)."""
        keys: list[str] = []
        for m in self.manifests:
            for k in m.metrics:
                if k not in keys:
                    keys.append(k)
        return {k: [m.metrics.get(k) for m in self.manifests] for k in keys}

    def describe(self) -> str:
        cols = " | ".join(self.run_ids)
        lines = [f"compare: {cols}"]
        for m in self.manifests:
            rng = f"[{m.effective_start_ts}, {m.effective_end_ts}]"
            lines.append(f"  {m.run_id}: range {rng}  engine {m.engine_version}")
        table = self.metric_table()
        for key, row in table.items():
            rendered = " | ".join("n/a" if v is None else str(v) for v in row)
            lines.append(f"  {key}: {rendered}")
        if self.warnings:
            lines.append("warnings:")
            for w in self.warnings:
                lines.append(f"  ! {w}")
        else:
            lines.append("warnings: none")
        return "\n".join(lines)


def _comparison_warnings(manifests: Sequence[RunManifest]) -> list[str]:
    warnings: list[str] = []
    ranges = {(m.effective_start_ts, m.effective_end_ts) for m in manifests}
    if len(ranges) > 1:
        detail = ", ".join(
            f"{m.run_id}=[{m.effective_start_ts}, {m.effective_end_ts}]"
            for m in manifests
        )
        warnings.append(
            f"{DIFFERENT_RANGE_WARNING}: runs cover different effective ranges "
            f"— their metrics are not directly comparable ({detail})"
        )
    if len({m.engine_version for m in manifests}) > 1:
        detail = ", ".join(f"{m.run_id}={m.engine_version}" for m in manifests)
        warnings.append(
            f"{DIFFERENT_ENGINE_WARNING}: runs were produced by different engine "
            f"versions — re-run to compare rather than trust the diff ({detail})"
        )
    if len({m.lake_revision for m in manifests}) > 1:
        detail = ", ".join(f"{m.run_id}={m.lake_revision or 'n/a'}" for m in manifests)
        warnings.append(
            f"{DIFFERENT_LAKE_WARNING}: runs read different lake revisions — the "
            f"underlying data was revised between them ({detail})"
        )
    return warnings


def compare(
    tenant: TenantContext, port: UserDataPort, run_ids: Sequence[str]
) -> RunComparison:
    """Compare runs by ``run_id`` for ``tenant``, surfacing not-comparable warnings.

    Loads each run's manifest (tenant-scoped) and emits a warning when the runs differ
    in effective range, engine version, or lake revision (§11.2/§13/§17). A single
    ``run_id`` produces no warnings (nothing to compare)."""
    manifests = tuple(load_run(tenant, port, rid) for rid in run_ids)
    warnings = tuple(_comparison_warnings(manifests)) if len(manifests) > 1 else ()
    return RunComparison(
        run_ids=tuple(run_ids), manifests=manifests, warnings=warnings
    )


# --- reproducibility bundle (§11.2/§2.10) ------------------------------------


@dataclass(frozen=True)
class ReproBundle:
    """A self-contained reproducibility bundle: everything needed to re-run bit-for-bit.

    Strategy code + params + data manifest (sources, ranges, ``lake_revision``) + engine
    version + seed, plus the **recorded event stream** — the append-only log (§2.10) that
    is the ground truth a re-run must reproduce. :func:`reproduce` re-executes the bundle
    through an injected runner and checks the emitted stream equals ``events`` exactly.

    Serialized with :meth:`to_json` (``flint export --run-id X``) and re-loaded with
    :meth:`from_json` (``flint run bundle.flint``); the CLI wiring is a Phase-7 surface."""

    run_id: str
    engine_version: str
    seed: int | None
    strategy_name: str
    strategy_source: str
    strategy_hash: str
    params: Mapping[str, Any]
    data_manifest: tuple[DataSource, ...]
    effective_start_ts: int | None
    effective_end_ts: int | None
    lake_revision: str
    events: tuple[dict[str, Any], ...]
    bundle_version: int = BUNDLE_VERSION

    def verify_integrity(self) -> bool:
        """True when the carried source still hashes to the carried hash (§11.2).

        A tampered or truncated strategy source fails this before anyone trusts a
        re-run — the content hash is the bundle's tamper-evident seal."""
        return content_hash(self.strategy_source) == self.strategy_hash

    def to_json(self) -> str:
        return json.dumps(
            {
                "bundle_version": self.bundle_version,
                "run_id": self.run_id,
                "engine_version": self.engine_version,
                "seed": self.seed,
                "strategy_name": self.strategy_name,
                "strategy_source": self.strategy_source,
                "strategy_hash": self.strategy_hash,
                "params": dict(self.params),
                "data_manifest": [s.to_dict() for s in self.data_manifest],
                "effective_start_ts": self.effective_start_ts,
                "effective_end_ts": self.effective_end_ts,
                "lake_revision": self.lake_revision,
                "events": [dict(e) for e in self.events],
            },
            sort_keys=True,
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str) -> "ReproBundle":
        d = json.loads(text)
        return cls(
            run_id=d["run_id"],
            engine_version=d["engine_version"],
            seed=d["seed"],
            strategy_name=d["strategy_name"],
            strategy_source=d["strategy_source"],
            strategy_hash=d["strategy_hash"],
            params=dict(d.get("params", {})),
            data_manifest=tuple(
                DataSource.from_dict(s) for s in d.get("data_manifest", [])
            ),
            effective_start_ts=d.get("effective_start_ts"),
            effective_end_ts=d.get("effective_end_ts"),
            lake_revision=d.get("lake_revision", ""),
            events=tuple(dict(e) for e in d.get("events", [])),
            bundle_version=d.get("bundle_version", BUNDLE_VERSION),
        )


def export_bundle(
    tenant: TenantContext, port: UserDataPort, run_id: str
) -> ReproBundle:
    """Assemble the reproducibility bundle for ``tenant``'s run ``run_id`` (§11.2).

    Pulls the manifest head and the recorded event stream from ``UserDataPort`` (both
    tenant-scoped) and packs them into a self-contained bundle. Raises ``KeyError`` if
    the run is absent for the tenant (§2.7)."""
    manifest = load_run(tenant, port, run_id)
    events = tuple(port.load_events(tenant, run_id))
    return ReproBundle(
        run_id=manifest.run_id,
        engine_version=manifest.engine_version,
        seed=manifest.seed,
        strategy_name=manifest.strategy_name,
        strategy_source=manifest.strategy_source,
        strategy_hash=manifest.strategy_hash,
        params=dict(manifest.params),
        data_manifest=manifest.data_manifest,
        effective_start_ts=manifest.effective_start_ts,
        effective_end_ts=manifest.effective_end_ts,
        lake_revision=manifest.lake_revision,
        events=events,
    )


class BundleRunner(Protocol):
    """Re-executes a bundle deterministically, returning the emitted event stream.

    Injected (like the walk-forward ``BacktestRunner``, 6.1) so this module stays pure
    orchestration: the real re-executor is the engine driven from the bundle's code +
    params + seed + data manifest; a test supplies a hand-authored deterministic one."""

    def __call__(self, bundle: ReproBundle) -> Sequence[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class ReproductionResult:
    """Outcome of re-executing a bundle: is the event stream bit-for-bit identical?

    ``reproduced`` is the §2.10 promise made concrete — the re-run's event stream equals
    the recorded one exactly. ``mismatch_index`` points at the first differing event (or
    the length mismatch) so a divergence is diagnosable, not just a boolean failure."""

    run_id: str
    reproduced: bool
    n_original: int
    n_reproduced: int
    mismatch_index: int | None
    original_events: tuple[dict[str, Any], ...]
    reproduced_events: tuple[dict[str, Any], ...]

    def describe(self) -> str:
        if self.reproduced:
            return (
                f"{self.run_id}: reproduced bit-for-bit "
                f"({self.n_original} events, identical stream)"
            )
        where = (
            "length differs"
            if self.mismatch_index is None
            else f"first mismatch at event #{self.mismatch_index}"
        )
        return (
            f"{self.run_id}: NOT reproduced ({where}; "
            f"original {self.n_original} vs reproduced {self.n_reproduced} events)"
        )


def _first_mismatch(
    a: Sequence[Mapping[str, Any]], b: Sequence[Mapping[str, Any]]
) -> int | None:
    for i in range(min(len(a), len(b))):
        if dict(a[i]) != dict(b[i]):
            return i
    if len(a) != len(b):
        return min(len(a), len(b))
    return None


def reproduce(bundle: ReproBundle, runner: BundleRunner) -> ReproductionResult:
    """Re-execute ``bundle`` via ``runner`` and check the event stream is identical.

    This is the event-sourcing reproducibility test (§2.10) made callable: a faithful,
    deterministic runner fed the bundle's seed + params + data manifest must emit exactly
    the recorded event stream. Any divergence is reported with the first differing index
    rather than a bare failure, so non-determinism is diagnosable."""
    emitted = tuple(dict(e) for e in runner(bundle))
    original = bundle.events
    idx = _first_mismatch(original, emitted)
    return ReproductionResult(
        run_id=bundle.run_id,
        reproduced=idx is None,
        n_original=len(original),
        n_reproduced=len(emitted),
        mismatch_index=idx,
        original_events=original,
        reproduced_events=emitted,
    )


# --- legacy run-metadata import (task #2 carry-forward, §19.6) ----------------
#
# The legacy DuckDB's run-metadata tables (journal_equity, strategies) were *inventoried*
# but not moved by the Phase-2 importer (flint/data/migrate.py counts them; see
# docs/redesign/MIGRATION.md, "Run metadata is inventoried, not moved (yet)"). The Run
# Library is their home. This layer maps already-extracted legacy rows into RunManifests
# with honest sentinels for fields legacy never recorded (D26 — engine version, seed,
# lake revision, fidelity are unknown, not fabricated). The extraction step (migrate.py
# reading and returning the rows, rather than only counting them) is a Phase-2 change
# outside this fence; these functions are the receiving surface it calls once wired.


def legacy_run_manifests(
    *,
    strategy_rows: Sequence[Mapping[str, Any]] = (),
    journal_rows: Sequence[Mapping[str, Any]] = (),
) -> list[RunManifest]:
    """Map legacy ``strategies`` / ``journal_equity`` rows into ``RunManifest``s (§19.6).

    Fabricates nothing (D26): fields the legacy schema never held — engine version, seed,
    ``lake_revision``, fidelity — are set to the ``"legacy"`` sentinel or ``None`` (seed),
    never to an invented number that would fake determinism. ``journal_equity`` rows are
    grouped by strategy name to reconstruct each legacy run's realized-equity endpoint as
    an opaque metric; no synthetic curve is generated. Rows carry ``provenance="legacy"``
    so the UI can mark them as imported, not re-runnable head records."""
    # Group journal equity points by strategy so each legacy run gets one manifest.
    by_strategy: dict[str, list[Mapping[str, Any]]] = {}
    for row in journal_rows:
        name = str(row.get("strategy", row.get("strategy_name", "")))
        by_strategy.setdefault(name, []).append(row)

    manifests: list[RunManifest] = []
    seen: set[str] = set()

    for srow in strategy_rows:
        name = str(srow.get("name", srow.get("strategy", "")))
        source = str(srow.get("source", srow.get("code", "")))
        created = int(srow.get("created_ts", srow.get("ts", 0) or 0))
        points = by_strategy.get(name, [])
        manifests.append(
            _legacy_manifest(name, source, created, points)
        )
        seen.add(name)

    # Journal-only strategies (equity recorded but the strategy row is gone): still
    # inventory them as legacy runs with an empty source (hash of "" — honest, not faked).
    for name, points in by_strategy.items():
        if name in seen:
            continue
        created = int(points[0].get("ts", 0) or 0) if points else 0
        manifests.append(_legacy_manifest(name, "", created, points))

    return manifests


def _legacy_manifest(
    name: str,
    source: str,
    created_ts: int,
    equity_points: Sequence[Mapping[str, Any]],
) -> RunManifest:
    metrics: dict[str, Any] = {}
    ts_values = [int(p["ts"]) for p in equity_points if p.get("ts") is not None]
    if equity_points:
        # Preserve the recorded endpoints verbatim as strings (money stays exact, §5);
        # do not compute derived risk metrics from a legacy curve we can't gate (§6.3).
        last = equity_points[-1]
        if last.get("equity") is not None:
            metrics["final_equity"] = str(last["equity"])
        metrics["n_equity_points"] = len(equity_points)
    return RunManifest(
        run_id=f"legacy:{name}" if name else "legacy:unnamed",
        strategy_name=name,
        strategy_source=source,
        params={},
        effective_start_ts=min(ts_values) if ts_values else None,
        effective_end_ts=max(ts_values) if ts_values else None,
        fidelity={"tier": LEGACY_SENTINEL},
        metrics=metrics,
        engine_version=LEGACY_SENTINEL,
        seed=None,  # legacy never recorded a seed — not 0 (D26: 0 would fake determinism)
        lake_revision=LEGACY_SENTINEL,
        data_manifest=(),
        tag="legacy-import",
        note="imported from legacy DuckDB run metadata (§19.6); not re-runnable",
        kind="backtest",
        created_ts=created_ts,
        provenance="legacy",
    )


def import_legacy_runs(
    tenant: TenantContext,
    port: UserDataPort,
    *,
    strategy_rows: Sequence[Mapping[str, Any]] = (),
    journal_rows: Sequence[Mapping[str, Any]] = (),
) -> list[str]:
    """Persist legacy run metadata into ``tenant``'s Run Library; return the run_ids.

    Tenant-scoped like every other ``UserDataPort`` write (§2.7); idempotent on the
    ``legacy:<name>`` run_id so re-importing is a no-op, matching the migrate importer's
    idempotency (docs/redesign/MIGRATION.md)."""
    ids: list[str] = []
    for manifest in legacy_run_manifests(
        strategy_rows=strategy_rows, journal_rows=journal_rows
    ):
        ids.append(persist_run(tenant, port, manifest))
    return ids
