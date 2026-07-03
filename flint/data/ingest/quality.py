"""Ingestion quality bars — the checks every worker runs before a write (§9.0).

Two families, both pure functions over Arrow tables (no I/O, no network):

* **Gap detection** — cadence-aware, *expected-vs-actual*. A candle stream has a
  known cadence (``resolution_s``), so "what's missing" is not the 2.2 cache's
  contiguous ``[min, max+1)`` envelope but the set of epoch-aligned bars in the
  requested span that never arrived. ``detect_candle_gaps`` returns those holes
  as a ``RangeSet`` — this is the cadence-aware internal-gap detection deferred
  from slice 2.2 to here.
* **Pre-write checks** — timestamp ordering, duplicate keys, price-spike sanity,
  and zero-volume-bug candles (§9.0). These never fabricate or repair data
  (D26); they *report*. Ordering/duplicate problems are ``errors`` (a worker must
  not upsert an unsorted or key-colliding batch); spikes and zero-volume rows are
  ``warnings`` (surfaced, not blocking — a real market can gap or print a wick).

The cache and Data-API coverage matrix remain the authorities on *serving*
coverage; these functions are what an ingestion worker uses to decide whether a
fetched batch is clean enough to persist and where it still has holes to backfill.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pyarrow as pa
import pyarrow.compute as pc

from flint.core.time import MS_PER_SECOND, bar_start

from ..ranges import RangeSet, TimeRange


@dataclass(frozen=True)
class QualityReport:
    """The outcome of the pre-write checks for one fetched batch (§9.0).

    ``errors`` block a write (unsorted / duplicate keys — a broken batch);
    ``warnings`` are surfaced but do not block (spikes, zero-volume candles).
    """

    rows: int
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True when nothing blocks the write (warnings are allowed)."""
        return not self.errors


def expected_bar_count(span: TimeRange, resolution_s: int) -> int:
    """How many epoch-aligned bars of width ``resolution_s`` fall in ``span``.

    Counts bar-starts ``b`` with ``span.start_ms <= b < span.end_ms`` — the
    denominator of the expected-vs-actual gap check.
    """
    if resolution_s <= 0:
        raise ValueError(f"resolution_s must be positive, got {resolution_s}")
    width = resolution_s * MS_PER_SECOND
    first = bar_start(span.start_ms, resolution_s)
    if first < span.start_ms:
        first += width
    if first >= span.end_ms:
        return 0
    return (span.end_ms - first + width - 1) // width


def detect_candle_gaps(
    table: pa.Table, resolution_s: int, span: TimeRange, *, ts_col: str = "ts"
) -> RangeSet:
    """Return the aligned bars in ``span`` missing from ``table`` (cadence gaps).

    Every epoch-aligned bar-start expected in ``span`` that has no row is a hole;
    consecutive holes coalesce into a single ``[start, end)`` range via
    ``RangeSet`` normalisation. An empty ``span`` (or one with no expected bars)
    yields an empty set; a fully-populated stream yields an empty set.
    """
    width = resolution_s * MS_PER_SECOND
    first = bar_start(span.start_ms, resolution_s)
    if first < span.start_ms:
        first += width
    if first >= span.end_ms:
        return RangeSet()

    present: set[int] = set()
    if table.num_rows and ts_col in table.column_names:
        present = {int(t) for t in table.column(ts_col).to_pylist()}

    gaps: list[TimeRange] = []
    b = first
    while b < span.end_ms:
        if b not in present:
            gaps.append(TimeRange(b, b + width))
        b += width
    return RangeSet(gaps)


def check_prewrite(
    table: pa.Table,
    *,
    ts_col: str = "ts",
    key_cols: tuple[str, ...] = (),
    price_col: str | None = "close",
    volume_col: str | None = "volume",
    spike_ratio: float = 10.0,
) -> QualityReport:
    """Run the §9.0 pre-write checks over one fetched batch (no repair, D26).

    * **Ordering** (error): ``ts`` must be non-decreasing — a worker upserts in
      order so late-arriving out-of-order pages are caught, not silently stored.
    * **Duplicate keys** (error): two rows sharing the row-identity key collide
      in the store; the batch must be de-duplicated first. The identity is
      ``ts`` plus any ``key_cols`` (per-kind tie-breakers — ``trade_id`` for
      trades, ``rate_type`` for funding, ``seq`` for book deltas — mirroring
      the store's per-kind dedupe keys): tick streams legitimately carry many
      rows per millisecond, so ts alone is not their identity (§B2).
    * **Price spike** (warning): a close jumping by more than ``spike_ratio``×
      (or below ``1/spike_ratio``×) the previous close is flagged for review.
    * **Zero volume** (warning): HL's known zero-volume-bug candles are flagged.
    """
    n = table.num_rows
    if n == 0 or ts_col not in table.column_names:
        return QualityReport(rows=n)

    errors: list[str] = []
    warnings: list[str] = []

    ts = table.column(ts_col)
    if n >= 2:
        # Non-decreasing check and duplicate check, both off the same neighbours.
        ts_flat = ts.combine_chunks() if isinstance(ts, pa.ChunkedArray) else ts
        prev, curr = ts_flat[:-1], ts_flat[1:]
        if pc.any(pc.greater(prev, curr)).as_py():
            errors.append("timestamps not sorted ascending")
        same = pc.equal(prev, curr)
        for key in key_cols:
            if key not in table.column_names:
                continue
            col = table.column(key)
            col = col.combine_chunks() if isinstance(col, pa.ChunkedArray) else col
            same = pc.and_(same, pc.equal(col[:-1], col[1:]))
        dupes = pc.sum(same).as_py() or 0
        if dupes:
            errors.append(f"{dupes} duplicate ts key(s)")

    if price_col is not None and price_col in table.column_names and n >= 2:
        prices = table.column(price_col)
        prev_p, curr_p = prices[:-1], prices[1:]
        # Only compare where the previous price > 0; substitute 1.0 into the
        # denominator elsewhere purely to avoid a divide-by-zero (the ``safe``
        # mask discards those positions before any spike is counted).
        safe = pc.greater(prev_p, 0)
        denom = pc.if_else(safe, prev_p, pa.scalar(1.0, pa.float64()))
        ratio = pc.divide(pc.cast(curr_p, pa.float64()), denom)
        spiked = pc.and_(
            safe,
            pc.or_(
                pc.greater(ratio, spike_ratio),
                pc.less(ratio, 1.0 / spike_ratio),
            ),
        )
        n_spikes = pc.sum(spiked).as_py() or 0
        if n_spikes:
            warnings.append(
                f"{n_spikes} price spike(s) > {spike_ratio:g}x vs previous close"
            )

    if volume_col is not None and volume_col in table.column_names:
        zero = pc.sum(pc.equal(table.column(volume_col), 0)).as_py() or 0
        if zero:
            warnings.append(f"{zero} zero-volume candle(s)")

    return QualityReport(rows=n, errors=tuple(errors), warnings=tuple(warnings))


@dataclass(frozen=True)
class BackfillResult:
    """What one backfill call produced — rows persisted plus its quality record."""

    kind: str
    rows_written: int
    quality: QualityReport
    gaps: RangeSet = field(default_factory=RangeSet)
