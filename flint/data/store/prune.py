"""Storage retention + ``flint data prune`` (D5, §B2/§B9).

Tick-scale capture grows without bound (an HL L2 day is tens of millions of
flat rows), so the local lake gets a per-kind retention policy and a prune pass
that deletes expired partitions. The one invariant that makes eviction safe is
**the ledger never lies about coverage**: pruning a range *removes it from the
CoverageLedger* in the same pass, so ``available()`` shrinks to exactly what is
still on disk and the gate machinery keeps rejecting/degrading honestly. A
zero-row covered day (quiet market) inside the pruned window loses its coverage
too — retention retracts the *guarantee*, not just the bytes; scattered
zero-row islands surviving behind a pruned window would advertise coverage the
user can no longer trust to be complete alongside its neighbors.

Defaults (override per kind via ``flint data prune --retention kind=days``):

* ``BOOK_DELTA`` — 30 days (the heavy Tier-3 kind; ~17 bytes/row on disk).
* ``QUOTES`` — 90 days.
* ``TRADES`` — 180 days.
* everything else (candles, funding, OI, depth) — **forever**: funding and
  candles are the cheap, hard-required history a backtest gates on.

Deletion is whole partitions only (a day file, or an hour file for
hour-partitioned kinds), strictly older than the retention boundary — a partial
day is never rewritten. The boundary is floored to the partition granularity,
so removed coverage aligns exactly with removed files.

A live :class:`~flint.data.store.durable_cache.DurableCacheSource` holds a
held-range index seeded at construction; prune operates on the lake paths
directly, so re-open the store (fresh ``_reindex``) after pruning in-process.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from ..ranges import Kind, RangeSet, TimeRange
from .coverage import CoverageLedger
from .layout import is_hour_partitioned

_PART_FILE = "part.parquet"
_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000

#: Per-kind retention in days; ``None`` = keep forever. See module docstring.
DEFAULT_RETENTION_DAYS: dict[Kind, int | None] = {
    Kind.CANDLES: None,
    Kind.FUNDING: None,
    Kind.OI: None,
    Kind.DEPTH: None,
    Kind.TRADES: 180,
    Kind.QUOTES: 90,
    Kind.BOOK_DELTA: 30,
}


@dataclass(frozen=True, slots=True)
class PrunedPartition:
    """One partition file the pass deleted (or would delete, in dry-run)."""

    kind: Kind
    venue: str
    market: str
    path: str  # relative to the lake root
    start_ms: int  # partition start (day or hour boundary)
    size_bytes: int


@dataclass(frozen=True, slots=True)
class PrunedCoverage:
    """Coverage retracted from one stream's ledger (exact removed sub-ranges)."""

    kind: Kind
    venue: str
    market: str
    removed: tuple[TimeRange, ...]


@dataclass(frozen=True)
class PruneReport:
    """What a prune pass did (or would do — ``dry_run``)."""

    dry_run: bool
    now_ms: int
    partitions: tuple[PrunedPartition, ...]
    coverage: tuple[PrunedCoverage, ...]

    @property
    def bytes_reclaimed(self) -> int:
        return sum(p.size_bytes for p in self.partitions)

    def to_payload(self) -> dict:
        """JSON-safe summary for the CLI."""
        return {
            "dry_run": self.dry_run,
            "partitions_deleted": len(self.partitions),
            "bytes_reclaimed": self.bytes_reclaimed,
            "partitions": [
                {
                    "kind": p.kind.value,
                    "venue": p.venue,
                    "market": p.market,
                    "path": p.path,
                    "size_bytes": p.size_bytes,
                }
                for p in self.partitions
            ],
            "coverage_removed": [
                {
                    "kind": c.kind.value,
                    "venue": c.venue,
                    "market": c.market,
                    "ranges": [
                        {"start_ms": r.start_ms, "end_ms": r.end_ms}
                        for r in c.removed
                    ],
                }
                for c in self.coverage
            ],
        }


def retention_boundary_ms(kind: Kind, days: int, now_ms: int) -> int:
    """Everything strictly before this instant is out of retention.

    Floored to the kind's partition granularity (hour for hour-partitioned
    kinds, day otherwise) so whole partitions are either fully retained or
    fully deletable and removed coverage aligns exactly with removed files.
    """
    cutoff = now_ms - days * _DAY_MS
    grain = _HOUR_MS if is_hour_partitioned(kind.value) else _DAY_MS
    return cutoff - (cutoff % grain)


def _partition_start_ms(kind: Kind, part: Path, market_dir: Path) -> int | None:
    """The partition's start instant from its path, or None if unparsable."""
    from datetime import UTC, datetime

    rel = part.relative_to(market_dir).parts
    try:
        if is_hour_partitioned(kind.value):
            # <date>/<hour>/part.parquet
            date_s, hour_s = rel[0], rel[1]
            base = datetime.strptime(date_s, "%Y-%m-%d").replace(tzinfo=UTC)
            return int(base.timestamp() * 1000) + int(hour_s) * _HOUR_MS
        # <date>/part.parquet
        base = datetime.strptime(rel[0], "%Y-%m-%d").replace(tzinfo=UTC)
        return int(base.timestamp() * 1000)
    except (ValueError, IndexError):
        return None


def prune(
    root: str | Path,
    *,
    retention_days: dict[Kind, int | None] | None = None,
    now_ms: int | None = None,
    dry_run: bool = False,
) -> PruneReport:
    """Apply retention to the lake at ``root``; see the module docstring.

    ``retention_days`` overrides :data:`DEFAULT_RETENTION_DAYS` per kind.
    Dry-run computes the full report — partitions *and* the exact coverage
    sub-ranges that would be retracted — without touching disk.
    """
    lake = Path(root)
    policy = dict(DEFAULT_RETENTION_DAYS)
    policy.update(retention_days or {})
    now = now_ms if now_ms is not None else int(time.time() * 1000)

    pruned: list[PrunedPartition] = []
    retracted: list[PrunedCoverage] = []
    for kind_dir in sorted(lake.iterdir()) if lake.exists() else []:
        if not kind_dir.is_dir():
            continue
        try:
            kind = Kind(kind_dir.name)
        except ValueError:
            continue
        days = policy.get(kind)
        if days is None:
            continue  # kept forever
        boundary = retention_boundary_ms(kind, days, now)
        hole = TimeRange(0, boundary)
        for market_dir in sorted(kind_dir.glob("*/*")):
            if not market_dir.is_dir():
                continue
            venue, market = market_dir.parent.name, market_dir.name
            # 1. Expired whole partitions: start strictly before the boundary.
            for part in sorted(market_dir.rglob(_PART_FILE)):
                start = _partition_start_ms(kind, part, market_dir)
                if start is None or start >= boundary:
                    continue
                pruned.append(
                    PrunedPartition(
                        kind=kind,
                        venue=venue,
                        market=market,
                        path=str(part.relative_to(lake)),
                        start_ms=start,
                        size_bytes=part.stat().st_size,
                    )
                )
                if not dry_run:
                    part.unlink()
                    _remove_empty_dirs(part.parent, stop=market_dir)
            # 2. Retract coverage for the pruned window — eviction never lies.
            #    The whole [0, boundary) hole goes, including zero-row covered
            #    days (retention retracts the guarantee, not just the bytes).
            ledger = CoverageLedger.load(market_dir)
            if ledger is None:
                continue
            removed = ledger.covered().intersect(RangeSet((hole,))).ranges
            if removed:
                retracted.append(
                    PrunedCoverage(
                        kind=kind, venue=venue, market=market, removed=removed
                    )
                )
                if not dry_run:
                    ledger.remove(hole)

    return PruneReport(
        dry_run=dry_run,
        now_ms=now,
        partitions=tuple(pruned),
        coverage=tuple(retracted),
    )


def _remove_empty_dirs(directory: Path, *, stop: Path) -> None:
    """Remove now-empty partition dirs up to (not including) ``stop``.

    The market directory itself (``stop``) is never removed — it keeps the
    ``_coverage.json`` ledger, and removing it would erase provenance.
    """
    current = directory
    while current != stop and current.is_dir():
        if any(current.iterdir()):
            return  # anything left (files, retained siblings) — stop here
        current.rmdir()
        current = current.parent
