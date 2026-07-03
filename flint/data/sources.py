"""The source chain — where the DataManager looks for data, in order (§9).

The DataManager never talks to a venue or the lake directly; it walks an ordered
list of ``DataSource``s: **local cache → Flint Data API → free-venue providers**.
Each source answers two questions for a ``(venue, market, kind)``:

* ``available(want)`` — which sub-ranges of ``want`` can I serve? (a ``RangeSet``)
* ``fetch(span)`` — give me the rows in ``span`` (an Arrow ``Table``).

``FlintDataAPIClient`` is still an honest **stub** — it declares no coverage until
the lake client lands (2.7). ``FreeVenueProvider`` is now **real** (2.4): it
composes per-venue ``VenueProvider``s (the HL REST provider) and serves the
sub-ranges each declares. With no providers registered it stays inert (declares
nothing), so a bare chain still never fabricates data (D26) and the funding hard
gate still rejects an uncovered request.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from typing import Protocol, runtime_checkable

import pyarrow as pa

from .ranges import Kind, RangeSet, TimeRange

#: Rows per ``RecordBatch`` on the streaming read surface. 64k flat BOOK_DELTA
#: rows ≈ 5 MB decoded (~74 bytes/row on the real 2026-06-01 HL fragment) — a
#: comfortable per-step bound for tick consumers.
DEFAULT_BATCH_SIZE = 65_536


class DataSource(ABC):
    """One tier of the source chain. Kind-agnostic: candles/funding/OI/depth."""

    #: Short stable name used in the fidelity summary's provenance.
    name: str = "source"

    @abstractmethod
    def available(
        self, venue: str, market: str, kind: Kind, want: TimeRange
    ) -> RangeSet:
        """Sub-ranges of ``want`` this source can serve for ``(venue, market, kind)``."""
        ...

    @abstractmethod
    def fetch(
        self, venue: str, market: str, kind: Kind, span: TimeRange
    ) -> pa.Table:
        """Rows within half-open ``span``, ordered by ``ts``. Empty table if none."""
        ...

    def fetch_batches(
        self,
        venue: str,
        market: str,
        kind: Kind,
        span: TimeRange,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> Iterator[pa.RecordBatch]:
        """``fetch`` as a bounded stream of ``RecordBatch``es (D5, §9.2).

        The tick-scale read surface: BOOK_DELTA (and any high-volume tick kind)
        is consumed batch-by-batch so a reader never holds a whole day. The
        default implementation wraps :meth:`fetch` — correct for the small
        tiers (in-memory cache, REST providers, vendor day files) whose fetch
        already materializes; :class:`~flint.data.store.durable_cache.
        DurableCacheSource` overrides it to stream natively over Parquet
        fragments without ever concatenating them.
        """
        table = self.fetch(venue, market, kind, span)
        if table.num_rows == 0:
            return
        yield from table.to_batches(max_chunksize=batch_size)


class InMemoryCacheSource(DataSource):
    """The local cache tier — writable, so the manager writes through to it (§9).

    Backed by per-``(venue, market, kind)`` Arrow tables here; the durable
    Parquet/DuckDB backing (``store.layout``) wires in with the lake client
    (2.7). Coverage is derived honestly from the ``ts`` values actually held —
    never assumed — so a partial cache reports exactly the sub-range it has.
    """

    name = "local_cache"

    def __init__(self) -> None:
        self._tables: dict[tuple[str, str, Kind], pa.Table] = {}

    def _key(self, venue: str, market: str, kind: Kind) -> tuple[str, str, Kind]:
        return (venue, market, kind)

    def available(
        self, venue: str, market: str, kind: Kind, want: TimeRange
    ) -> RangeSet:
        table = self._tables.get(self._key(venue, market, kind))
        if table is None or table.num_rows == 0:
            return RangeSet()
        held = _held_range(table)
        if held is None:
            return RangeSet()
        return RangeSet((held,)).intersect(RangeSet((want,)))

    def fetch(
        self, venue: str, market: str, kind: Kind, span: TimeRange
    ) -> pa.Table:
        table = self._tables.get(self._key(venue, market, kind))
        if table is None:
            return pa.table({})
        return _slice_half_open(table, span)

    def store(self, venue: str, market: str, kind: Kind, table: pa.Table) -> None:
        """Write ``table`` through to the cache, merging + de-duplicating by ts."""
        if table.num_rows == 0:
            return
        key = self._key(venue, market, kind)
        existing = self._tables.get(key)
        combined = pa.concat_tables([existing, table]) if existing else table
        self._tables[key] = _dedupe_sorted_by_ts(combined)


class FlintDataAPIClient(DataSource):
    """The hosted lake tier — a stub until the range client lands (2.7, §9.0).

    Declares no coverage, so the chain falls through to the providers. The real
    client speaks ``GET /v1/data/{kind}`` and writes through to the local cache.
    """

    name = "flint_data_api"

    def available(
        self, venue: str, market: str, kind: Kind, want: TimeRange
    ) -> RangeSet:
        return RangeSet()

    def fetch(
        self, venue: str, market: str, kind: Kind, span: TimeRange
    ) -> pa.Table:
        return pa.table({})


@runtime_checkable
class VenueProvider(Protocol):
    """One venue's free-API fetcher, composed by ``FreeVenueProvider`` (§9.1).

    A provider declares which ``(market, kind)`` it serves, a **coverage floor**
    (the earliest ts it can serve — the venue's history start), and fetches a
    span. The floor keeps the funding hard gate honest without a network probe:
    a request before the venue existed is a gap, not a silent pass. The Flint
    Data API coverage matrix (2.7) is the authority on real coverage; a free
    provider is a best-effort fallback for offline/self-hosted use.
    """

    venue: str

    def supports(self, market: str, kind: Kind) -> bool: ...

    def coverage_floor(self, market: str, kind: Kind) -> int | None: ...

    def fetch_range(self, market: str, kind: Kind, span: TimeRange) -> pa.Table: ...


class FreeVenueProvider(DataSource):
    """The offline/self-hosted fallback tier — composes ``VenueProvider``s (§9.1).

    Real as of 2.4: it delegates ``available``/``fetch`` to the provider
    registered for a venue (the HL REST provider today). With no providers it is
    inert — declares nothing, fetches nothing — so a bare chain still never
    fabricates data (D26) and the funding hard gate still rejects.
    """

    name = "free_venue_provider"

    def __init__(self, providers: Sequence[VenueProvider] = ()) -> None:
        self._providers: dict[str, VenueProvider] = {p.venue: p for p in providers}

    def _for(self, venue: str, market: str, kind: Kind) -> VenueProvider | None:
        provider = self._providers.get(venue)
        if provider is None or not provider.supports(market, kind):
            return None
        return provider

    def available(
        self, venue: str, market: str, kind: Kind, want: TimeRange
    ) -> RangeSet:
        provider = self._for(venue, market, kind)
        if provider is None:
            return RangeSet()
        floor = provider.coverage_floor(market, kind)
        if floor is None:
            return RangeSet()
        start = max(want.start_ms, floor)
        if start >= want.end_ms:
            return RangeSet()
        return RangeSet((TimeRange(start, want.end_ms),))

    def fetch(
        self, venue: str, market: str, kind: Kind, span: TimeRange
    ) -> pa.Table:
        provider = self._for(venue, market, kind)
        if provider is None:
            return pa.table({})
        return provider.fetch_range(market, kind, span)


# --- Arrow helpers (ts-keyed, half-open) ------------------------------------


def _held_range(table: pa.Table) -> TimeRange | None:
    """The ``[min_ts, max_ts + 1)`` envelope of a ts-keyed table, or None.

    ``+1`` makes the envelope half-open and inclusive of the last row: a table
    whose only bar is at ``ts=100`` covers ``[100, 101)``, so ``covers`` of a
    single-point request succeeds.
    """
    if table.num_rows == 0 or "ts" not in table.column_names:
        return None
    import pyarrow.compute as pc

    lo = pc.min(table["ts"]).as_py()
    hi = pc.max(table["ts"]).as_py()
    if lo is None or hi is None:
        return None
    return TimeRange(int(lo), int(hi) + 1)


def _slice_half_open(table: pa.Table, span: TimeRange) -> pa.Table:
    if table.num_rows == 0 or "ts" not in table.column_names:
        return table
    import pyarrow.compute as pc

    mask = pc.and_(
        pc.greater_equal(table["ts"], span.start_ms),
        pc.less(table["ts"], span.end_ms),
    )
    return table.filter(mask).sort_by("ts")


def _dedupe_sorted_by_ts(table: pa.Table) -> pa.Table:
    if table.num_rows == 0 or "ts" not in table.column_names:
        return table
    import pyarrow.compute as pc

    sorted_table = table.sort_by("ts")
    ts = sorted_table["ts"]
    if sorted_table.num_rows < 2:
        return sorted_table
    # Keep a row unless its ts equals the previous row's ts (last write wins on
    # equal keys is not needed here — the caller merges append-only history).
    keep = pc.not_equal(ts[1:], ts[:-1])
    keep = pa.concat_arrays([pa.array([True]), keep.combine_chunks()])
    return sorted_table.filter(keep)
