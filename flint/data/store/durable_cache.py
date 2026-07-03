"""Durable Parquet-backed local cache tier (§9.0).

The 2.2 ``InMemoryCacheSource`` proved the write-through contract; this is the
durable version the local client actually caches into, so a fetched range
survives a restart and the next run for it never leaves the machine. It is a
drop-in ``DataSource`` with the same ``store`` write-through method, backed by
the ``store.layout`` Parquet lake: rows land in ``kind/venue/market/date``
partitions (depth hour-partitioned), each file stamped with ``schema_version``
and upgraded on read (§9.0) — the same physical layout the hosted lake uses, so
a cached partition and a lake partition are byte-identical.

Coverage is reported as the honest ``[min_ts, max_ts + 1)`` envelope of the rows
actually on disk for a ``(venue, market, kind)`` — never assumed — mirroring the
in-memory tier. The held-range index is seeded from the ts column statistics of
existing partition files on construction, so re-opening a populated cache knows
what it holds without re-reading every row.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from ..ranges import Kind, RangeSet, TimeRange
from ..sources import DataSource
from .layout import MigrationRegistry, partition_path, read_parquet, write_parquet

_PART_FILE = "part.parquet"
_HOUR_PARTITIONED = frozenset({Kind.DEPTH})


def _date_of(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _hour_of(ts_ms: int) -> int:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).hour


def _dedupe_sorted_by_ts(table: pa.Table) -> pa.Table:
    import pyarrow.compute as pc

    if table.num_rows < 2 or "ts" not in table.column_names:
        return table.sort_by("ts") if table.num_rows and "ts" in table.column_names else table
    sorted_table = table.sort_by("ts")
    ts = sorted_table.column("ts").combine_chunks()
    keep = pa.concat_arrays([pa.array([True]), pc.not_equal(ts[1:], ts[:-1])])
    return sorted_table.filter(keep)


class DurableCacheSource(DataSource):
    """A write-through cache tier that persists to the Parquet lake layout (§9.0)."""

    name = "local_cache"

    def __init__(
        self, root: str | Path, *, registry: MigrationRegistry | None = None
    ) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._registry = registry
        # (venue, market, kind) -> (min_ts, max_ts) held on disk.
        self._held: dict[tuple[str, str, Kind], tuple[int, int]] = {}
        self._reindex()

    # --- DataSource surface ------------------------------------------------

    def available(
        self, venue: str, market: str, kind: Kind, want: TimeRange
    ) -> RangeSet:
        held = self._held.get((venue, market, kind))
        if held is None:
            return RangeSet()
        envelope = TimeRange(held[0], held[1] + 1)
        return RangeSet((envelope,)).intersect(RangeSet((want,)))

    def fetch(
        self, venue: str, market: str, kind: Kind, span: TimeRange
    ) -> pa.Table:
        tables = [
            read_parquet(str(p), registry=self._registry)
            for p in self._partition_files(venue, market, kind, span)
        ]
        tables = [t for t in tables if t.num_rows]
        if not tables:
            return pa.table({})
        merged = _dedupe_sorted_by_ts(pa.concat_tables(tables))
        return self._slice_half_open(merged, span)

    def store(self, venue: str, market: str, kind: Kind, table: pa.Table) -> None:
        """Write ``table`` through to the Parquet lake, merging by ts per partition."""
        if table.num_rows == 0:
            return
        for path, part in self._split_into_partitions(venue, market, kind, table):
            existing = read_parquet(str(path), registry=self._registry) if path.exists() else None
            combined = pa.concat_tables([existing, part]) if existing is not None else part
            merged = _dedupe_sorted_by_ts(combined)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_parquet(merged, str(path))
        self._extend_held(venue, market, kind, table)

    # --- partition layout --------------------------------------------------

    def _partition_dir(
        self, venue: str, market: str, kind: Kind, ts_ms: int
    ) -> Path:
        date = _date_of(ts_ms)
        hour = _hour_of(ts_ms) if kind in _HOUR_PARTITIONED else None
        sub = partition_path(kind.value, venue, market, date, hour=hour)
        return self._root / sub

    def _split_into_partitions(
        self, venue: str, market: str, kind: Kind, table: pa.Table
    ) -> Iterable[tuple[Path, pa.Table]]:
        rows_by_dir: dict[Path, list[int]] = {}
        ts_list = table.column("ts").to_pylist()
        for i, ts_ms in enumerate(ts_list):
            rows_by_dir.setdefault(
                self._partition_dir(venue, market, kind, int(ts_ms)), []
            ).append(i)
        for directory, indices in rows_by_dir.items():
            yield directory / _PART_FILE, table.take(pa.array(indices))

    def _partition_files(
        self, venue: str, market: str, kind: Kind, span: TimeRange
    ) -> list[Path]:
        market_dir = self._root / kind.value / venue / market
        if not market_dir.exists():
            return []
        lo, hi = _date_of(span.start_ms), _date_of(max(span.start_ms, span.end_ms - 1))
        hits: list[Path] = []
        for part in sorted(market_dir.rglob(_PART_FILE)):
            # .../date/part.parquet (day) or .../date/hour/part.parquet (depth).
            date = part.parent.parent.name if kind in _HOUR_PARTITIONED else part.parent.name
            if lo <= date <= hi:
                hits.append(part)
        return hits

    # --- held-range index --------------------------------------------------

    def _extend_held(
        self, venue: str, market: str, kind: Kind, table: pa.Table
    ) -> None:
        import pyarrow.compute as pc

        col = table.column("ts")
        lo = int(pc.min(col).as_py())
        hi = int(pc.max(col).as_py())
        key = (venue, market, kind)
        prev = self._held.get(key)
        if prev is None:
            self._held[key] = (lo, hi)
        else:
            self._held[key] = (min(prev[0], lo), max(prev[1], hi))

    def _reindex(self) -> None:
        """Seed the held-range index from ts stats of existing partition files."""
        for kind_dir in self._root.iterdir() if self._root.exists() else []:
            if not kind_dir.is_dir():
                continue
            try:
                kind = Kind(kind_dir.name)
            except ValueError:
                continue
            for part in kind_dir.rglob(_PART_FILE):
                # root/kind/venue/market/date[/hour]/part.parquet
                rel = part.relative_to(kind_dir).parts
                if len(rel) < 3:
                    continue
                venue, market = rel[0], rel[1]
                lo, hi = self._ts_bounds(part)
                if lo is None:
                    continue
                key = (venue, market, kind)
                prev = self._held.get(key)
                self._held[key] = (
                    (lo, hi) if prev is None else (min(prev[0], lo), max(prev[1], hi))
                )

    @staticmethod
    def _ts_bounds(path: Path) -> tuple[int, int] | tuple[None, None]:
        """Read just the ts column's min/max from a partition file (cheap)."""
        try:
            ts = pq.read_table(str(path), columns=["ts"]).column("ts")
        except (KeyError, OSError, pa.ArrowInvalid):
            return (None, None)
        if len(ts) == 0:
            return (None, None)
        import pyarrow.compute as pc

        return (int(pc.min(ts).as_py()), int(pc.max(ts).as_py()))

    @staticmethod
    def _slice_half_open(table: pa.Table, span: TimeRange) -> pa.Table:
        import pyarrow.compute as pc

        if table.num_rows == 0 or "ts" not in table.column_names:
            return table
        ts = table.column("ts")
        mask = pc.and_(
            pc.greater_equal(ts, span.start_ms), pc.less(ts, span.end_ms)
        )
        return table.filter(mask)
