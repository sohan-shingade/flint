"""Durable Parquet-backed local cache tier (§9.0).

The 2.2 ``InMemoryCacheSource`` proved the write-through contract; this is the
durable version the local client actually caches into, so a fetched range
survives a restart and the next run for it never leaves the machine. It is a
drop-in ``DataSource`` with the same ``store`` write-through method, backed by
the ``store.layout`` Parquet lake: rows land in ``kind/venue/market/date``
partitions (depth hour-partitioned), each file stamped with ``schema_version``
and upgraded on read (§9.0) — the same physical layout the hosted lake uses, so
a cached partition and a lake partition are byte-identical.

Coverage has two regimes (§9.0):

* **Asserted** — a ``_coverage.json`` :class:`CoverageLedger` in the
  ``(kind, venue, market)`` directory holds ingester-asserted ranges. When one
  is present it is authoritative: ``available()`` reports exactly what was
  asserted, and non-tick write-throughs extend it with the stored envelope.
  Tick-scale kinds (trades/quotes/book deltas) **require** it — rows without a
  ledger contribute *no* coverage, because a quiet market and a capture gap are
  indistinguishable at the row level.
* **Inferred** — with no ledger, non-tick kinds keep the honest
  ``[min_ts, max_ts + 1)`` envelope of the rows actually on disk, mirroring the
  in-memory tier. The first ``available()`` touch of such a populated directory
  seeds a ledger from that envelope (one-shot migration, provenance
  ``hl_rest``) so pre-ledger caches keep working verbatim.

The held-range index is seeded from the ts column statistics of existing
partition files on construction, so re-opening a populated cache knows what it
holds without re-reading every row.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from ..ranges import Kind, RangeSet, TimeRange
from ..sources import DEFAULT_BATCH_SIZE, DataSource
from .coverage import FUNDING_PREDICTED_VARIANT, CoverageLedger
from .layout import (
    DEFAULT_MIGRATIONS,
    SCHEMA_VERSION,
    MigrationRegistry,
    partition_path,
    read_parquet,
    read_schema_version,
    write_parquet,
)

_PART_FILE = "part.parquet"
# Sampled depth snapshots plus every tick-scale kind (mirrors layout.py's
# string-keyed set for the path grammar).
_HOUR_PARTITIONED = frozenset({Kind.DEPTH}) | frozenset(
    k for k in Kind if k.is_tick_scale
)

# Duplicate-row identity per kind (§B2 pre-flight fix): dedupe by ts alone
# silently drops one of a predicted+final funding pair sharing a settlement ts,
# and distinct trades/deltas can legitimately share a millisecond. Columns
# missing from a table are skipped, so schema-light fixtures degrade to ts.
#
# QUOTES tie-break (D5, carried flag from D2): QUOTES_SCHEMA has no ``seq``
# column (adding one is a lake schema bump — deliberately avoided), so the
# identity is the full BBO row: distinct same-millisecond BBO updates both
# survive, while byte-identical rows from overlapping ingests still collapse.
# Caveat, documented: arrival order *within* one millisecond is not preserved
# (rows sort by px/sz inside a ts tie); same-ms BBO consumers read the set of
# states, not the sub-ms sequence. Revisit with a seq column if sub-ms BBO
# ordering ever becomes load-bearing.
_DEDUPE_KEYS: dict[Kind, tuple[str, ...]] = {
    Kind.FUNDING: ("ts", "rate_type"),
    Kind.TRADES: ("ts", "trade_id"),
    Kind.BOOK_DELTA: ("ts", "seq"),
    Kind.QUOTES: ("ts", "bid_px", "bid_sz", "ask_px", "ask_sz"),
}


def _date_of(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _hour_of(ts_ms: int) -> int:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).hour


def _funding_assertion_spans(
    table: pa.Table,
) -> list[tuple[TimeRange, str]]:
    """(envelope, ledger-variant) per rate_type series in a FUNDING write (§6.4).

    Predicted rows assert under ``FUNDING_PREDICTED_VARIANT``; everything else
    (final, and the pre-greenfield import's ``legacy``) under the default final
    variant — so a mixed write extends both series honestly and a predicted-only
    write never extends the gate-satisfying one.
    """
    import pyarrow.compute as pc

    out: list[tuple[TimeRange, str]] = []
    predicted_mask = pc.equal(table.column("rate_type"), "predicted")
    for mask, variant in (
        (predicted_mask, FUNDING_PREDICTED_VARIANT),
        (pc.invert(predicted_mask), ""),
    ):
        rows = table.filter(mask)
        if rows.num_rows == 0:
            continue
        lo = int(pc.min(rows["ts"]).as_py())
        hi = int(pc.max(rows["ts"]).as_py())
        out.append((TimeRange(lo, hi + 1), variant))
    return out


def _dedupe_sorted(table: pa.Table, kind: Kind) -> pa.Table:
    """Sort by the kind's identity key and drop consecutive duplicates."""
    import pyarrow.compute as pc

    if "ts" not in table.column_names or table.num_rows == 0:
        return table
    keys = tuple(
        c for c in _DEDUPE_KEYS.get(kind, ("ts",)) if c in table.column_names
    ) or ("ts",)
    sorted_table = table.sort_by([(k, "ascending") for k in keys])
    if sorted_table.num_rows < 2:
        return sorted_table
    differs = None
    for k in keys:
        col = sorted_table.column(k).combine_chunks()
        step = pc.not_equal(col[1:], col[:-1])
        differs = step if differs is None else pc.or_(differs, step)
    keep = pa.concat_arrays([pa.array([True]), differs])
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
        ledger = CoverageLedger.load(self._market_dir(venue, market, kind))
        if ledger is not None:
            # Asserted coverage is authoritative once a ledger exists. The serve
            # path unions every variant — FUNDING's predicted-series assertions
            # are real, servable rows; only the hard gate discriminates, via
            # available_funding() below.
            return ledger.covered_any().intersect(RangeSet((want,)))
        if kind.is_tick_scale:
            # Ledger-mandatory: rows without an asserted ledger contribute NO
            # coverage — a quiet market and a capture gap look identical at the
            # row level, so tick coverage is never inferred (§9.0).
            return RangeSet()
        held = self._held.get((venue, market, kind))
        if held is None:
            return RangeSet()
        envelope = TimeRange(held[0], held[1] + 1)
        # One-shot envelope-seeding migration: a populated pre-ledger directory
        # gets its inferred envelope asserted (provenance "hl_rest") on this
        # first available() touch, so existing caches keep working while all
        # later reads go through the ledger path above. available() is the
        # trigger because it is the single entry point of every coverage
        # question (the manager's source chain always asks before fetching).
        seeded = CoverageLedger(self._market_dir(venue, market, kind))
        seeded.assert_covered(envelope, "hl_rest")
        return RangeSet((envelope,)).intersect(RangeSet((want,)))

    def available_funding(
        self, venue: str, market: str, want: TimeRange, *, rate_type: str = "final"
    ) -> RangeSet:
        """Per-series FUNDING coverage — what the hard gate consults (§6.4).

        With a ledger, the variants discriminate: the default variant ``""`` is
        the settled/final series (REST backfills, write-through self-assertions
        of final rows); ``FUNDING_PREDICTED_VARIANT`` holds recorder/Tardis
        predicted captures, which must never satisfy the gate. Without a ledger
        the inferred envelope counts as final: pre-ledger caches predate the
        recorder and hold REST-backfilled settled history (the same assumption
        the one-shot envelope-seed makes).
        """
        ledger = CoverageLedger.load(self._market_dir(venue, market, Kind.FUNDING))
        if ledger is not None:
            variant = "" if rate_type == "final" else FUNDING_PREDICTED_VARIANT
            return ledger.covered(variant).intersect(RangeSet((want,)))
        if rate_type != "final":
            return RangeSet()
        return self.available(venue, market, Kind.FUNDING, want)

    def provenance(
        self, venue: str, market: str, kind: Kind, want: TimeRange
    ) -> tuple[tuple[TimeRange, str], ...]:
        """Coverage broken into (range, who-captured-it) pieces from the ledger.

        Ledger entries carry their asserting source (tardis / recorder /
        hl_rest); without a ledger the inferred envelope is attributed to
        ``hl_rest`` — the same provenance the one-shot envelope-seed records.
        """
        ledger = CoverageLedger.load(self._market_dir(venue, market, kind))
        want_set = RangeSet((want,))
        if ledger is not None:
            out: list[tuple[TimeRange, str]] = []
            for entry in ledger.entries:
                for piece in RangeSet((entry.range,)).intersect(want_set).ranges:
                    out.append((piece, entry.source))
            return tuple(out)
        return tuple(
            (r, "hl_rest")
            for r in self.available(venue, market, kind, want).ranges
        )

    def coverage_ledger(self, venue: str, market: str, kind: Kind) -> CoverageLedger:
        """The asserted-coverage ledger of one stream directory, created if absent.

        The write-side hook for coverage-asserting ingesters (the live recorder,
        §9.2): assertions land in the same ``_coverage.json`` that ``available()``
        reads, so recorded coverage flows through the ordinary gate machinery.
        Attaching a writer to a populated pre-ledger non-tick directory first
        seeds the inferred envelope (the same one-shot migration ``available()``
        performs) so becoming asserted-authoritative never erases coverage the
        envelope honestly implied. Tick kinds are never envelope-seeded (§9.0).
        """
        ledger = CoverageLedger(self._market_dir(venue, market, kind))
        if not ledger.exists and not kind.is_tick_scale:
            held = self._held.get((venue, market, kind))
            if held is not None:
                ledger.assert_covered(TimeRange(held[0], held[1] + 1), "hl_rest")
        return ledger

    def fetch(
        self, venue: str, market: str, kind: Kind, span: TimeRange
    ) -> pa.Table:
        """Materialize ``span`` as one table.

        BOOK_DELTA callers must prefer :meth:`fetch_batches`: a whole-table
        fetch of an L2 day concatenates tens of millions of rows, which is
        exactly what the streaming surface exists to avoid (D5). The
        DataManager never routes BOOK_DELTA through here — it hands out
        ``PreparedData.streams`` handles instead.
        """
        tables = [
            read_parquet(str(p), registry=self._registry)
            for p in self._partition_files(venue, market, kind, span)
        ]
        tables = [t for t in tables if t.num_rows]
        if not tables:
            return pa.table({})
        merged = _dedupe_sorted(pa.concat_tables(tables), kind)
        return self._slice_half_open(merged, span)

    def fetch_batches(
        self,
        venue: str,
        market: str,
        kind: Kind,
        span: TimeRange,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> Iterator[pa.RecordBatch]:
        """Stream ``span`` batch-by-batch, never materializing a fragment set.

        Native override of the :class:`~flint.data.sources.DataSource` default
        (D5): partition files are visited in path order — chronological, since
        dates are ISO and hours zero-padded — and each is read through
        ``ParquetFile.iter_batches``, so peak memory is one row group (~1M rows
        for BOOK_DELTA under the D5 writer tuning), not a day. Rows were
        deduped + sorted by the kind's identity key at write time, and one
        identity key always lands in exactly one partition file, so no
        cross-fragment merge or dedupe is needed on the way out.

        Files stamped with an older lake ``schema_version`` upgrade on read
        per batch — sound because lake migrations are row-local (v1→v2 derives
        ``settlement_ts`` from the same row); a future non-row-local migration
        must materialize instead (guarded in ``layout``'s registry review).
        """
        for path in self._partition_files(venue, market, kind, span):
            version = read_schema_version(str(path))
            parquet = pq.ParquetFile(str(path))
            for batch in parquet.iter_batches(batch_size=batch_size):
                if batch.num_rows == 0:
                    continue
                if version < SCHEMA_VERSION:
                    upgraded = (self._registry or DEFAULT_MIGRATIONS).upgrade(
                        pa.Table.from_batches([batch]), version
                    )
                    candidates = upgraded.to_batches(max_chunksize=batch_size)
                else:
                    candidates = [batch]
                for candidate in candidates:
                    sliced = self._slice_batch_half_open(candidate, span)
                    if sliced.num_rows:
                        yield sliced

    def store(self, venue: str, market: str, kind: Kind, table: pa.Table) -> None:
        """Write ``table`` through to the Parquet lake, merging by ts per partition."""
        if table.num_rows == 0:
            return
        for path, part in self._split_into_partitions(venue, market, kind, table):
            existing = read_parquet(str(path), registry=self._registry) if path.exists() else None
            combined = pa.concat_tables([existing, part]) if existing is not None else part
            merged = _dedupe_sorted(combined, kind)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_parquet(merged, str(path), kind=kind.value)
        lo, hi = self._extend_held(venue, market, kind, table)
        if not kind.is_tick_scale:
            # Once a directory has an authoritative ledger, non-tick
            # write-throughs must keep extending it (the stored table's envelope
            # is exactly the inference the pre-ledger fallback would have made).
            # Tick kinds never self-assert: their ingesters own the assertion.
            ledger = CoverageLedger.load(self._market_dir(venue, market, kind))
            if ledger is not None:
                if kind is Kind.FUNDING and "rate_type" in table.column_names:
                    # FUNDING self-assertions are split per rate_type series
                    # (§6.4): a predicted-only write-through (e.g. Tardis
                    # derivative_ticker landing in the cache) must never extend
                    # the gate-satisfying final-series coverage.
                    for span, variant in _funding_assertion_spans(table):
                        ledger.assert_covered(span, "hl_rest", variant=variant)
                else:
                    ledger.assert_covered(TimeRange(lo, hi + 1), "hl_rest")

    # --- partition layout --------------------------------------------------

    def _market_dir(self, venue: str, market: str, kind: Kind) -> Path:
        """The ``(kind, venue, market)`` directory — where the ledger lives."""
        return self._root / kind.value / venue / market

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
    ) -> tuple[int, int]:
        """Extend the held index with ``table``'s ts bounds; return them."""
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
        return lo, hi

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

    @staticmethod
    def _slice_batch_half_open(batch: pa.RecordBatch, span: TimeRange) -> pa.RecordBatch:
        """Half-open ts slice of one batch (edges of a partition may overhang)."""
        import pyarrow.compute as pc

        if batch.num_rows == 0 or "ts" not in batch.schema.names:
            return batch
        ts = batch.column("ts")
        mask = pc.and_(
            pc.greater_equal(ts, span.start_ms), pc.less(ts, span.end_ms)
        )
        return batch.filter(mask)
