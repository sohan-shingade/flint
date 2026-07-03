"""D5 tick-scale streaming — fetch_batches + PreparedData.streams (§9.2, §B2).

Every market-data input here is the committed **real** 2026-06-01 Hyperliquid
fragment from Tardis (D26 — no synthetic series) or hand-authored unit rows.

The memory-bounded read test proves, pragmatically: the streaming surface
serves a stored BOOK_DELTA range as a sequence of ``RecordBatch``es each no
larger than ``batch_size`` rows, without any whole-range ``concat_tables`` on
the read path (the native reader visits one Parquet fragment at a time through
``ParquetFile.iter_batches``, so peak decoded memory is one row group — 1M rows
under the D5 writer tuning). It asserts the per-batch bound and stream/table
equivalence rather than RSS (rusage deltas are noisy across platforms and
allocator reuse makes them non-monotonic at this fixture's scale).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa

from flint.data import Kind, TimeRange
from flint.data.ingest.vendors.tardis import TardisCsvClient
from flint.data.manager import DataManager
from flint.data.store import DurableCacheSource

FIXTURES = Path(__file__).parent / "fixtures" / "tardis"
DAY1_MS = 1_780_272_000_000  # 2026-06-01T00:00:00Z — the real fragment's day
DAY_MS = 86_400_000


def _ms(y, m, d, h=0) -> int:
    return int(datetime(y, m, d, h, tzinfo=UTC).timestamp() * 1000)


def _real_book_fragment() -> pa.Table:
    gz = (FIXTURES / "hyperliquid_incremental_book_L2_2026-06-01_SOL.csv.gz").read_bytes()
    return TardisCsvClient.parse(
        "incremental_book_L2", gz, market="SOL-PERP", venue="hyperliquid"
    )[Kind.BOOK_DELTA]


def _seeded_cache(tmp_path) -> tuple[DurableCacheSource, pa.Table]:
    """The real fragment landed in a durable cache with asserted coverage."""
    cache = DurableCacheSource(tmp_path)
    table = _real_book_fragment()
    cache.store("hyperliquid", "SOL-PERP", Kind.BOOK_DELTA, table)
    ledger = cache.coverage_ledger("hyperliquid", "SOL-PERP", Kind.BOOK_DELTA)
    ledger.assert_covered(TimeRange(DAY1_MS, DAY1_MS + DAY_MS), "tardis")
    return cache, table


def test_fetch_batches_streams_real_fragment_memory_bounded(tmp_path):
    cache, table = _seeded_cache(tmp_path)
    span = TimeRange(DAY1_MS, DAY1_MS + DAY_MS)
    batch_size = 64

    batches = list(
        cache.fetch_batches(
            "hyperliquid", "SOL-PERP", Kind.BOOK_DELTA, span, batch_size=batch_size
        )
    )
    # Bounded: every step is at most batch_size rows, and the 300-row fragment
    # arrives in multiple increments — never one materialized table.
    assert len(batches) >= 2
    assert max(b.num_rows for b in batches) <= batch_size
    assert sum(b.num_rows for b in batches) == table.num_rows

    # Stream/table equivalence: same rows, same (ts, seq) order as fetch().
    whole = cache.fetch("hyperliquid", "SOL-PERP", Kind.BOOK_DELTA, span)
    streamed_seq = [s for b in batches for s in b.column("seq").to_pylist()]
    assert streamed_seq == whole.column("seq").to_pylist()
    streamed_ts = [t for b in batches for t in b.column("ts").to_pylist()]
    assert streamed_ts == whole.column("ts").to_pylist()
    assert streamed_ts == sorted(streamed_ts)


def test_fetch_batches_is_half_open_at_span_edges(tmp_path):
    cache, table = _seeded_cache(tmp_path)
    import pyarrow.compute as pc

    ts_min = pc.min(table["ts"]).as_py()
    ts_max = pc.max(table["ts"]).as_py()
    # [min, max) excludes the last-ms rows, exactly like fetch().
    span = TimeRange(ts_min, ts_max)
    streamed = sum(
        b.num_rows
        for b in cache.fetch_batches("hyperliquid", "SOL-PERP", Kind.BOOK_DELTA, span)
    )
    fetched = cache.fetch("hyperliquid", "SOL-PERP", Kind.BOOK_DELTA, span).num_rows
    assert streamed == fetched < table.num_rows


def test_default_fetch_batches_wraps_fetch():
    # The DataSource default implementation serves any source that only has a
    # table fetch. (Not the in-memory cache tier: its kind-agnostic store()
    # dedupes by ts alone and would thin the fragment's same-ms delta bursts.)
    from flint.data.ranges import RangeSet
    from flint.data.sources import DataSource

    table = _real_book_fragment()

    class TableOnlySource(DataSource):
        name = "table_only"

        def available(self, venue, market, kind, want):
            return RangeSet((want,))

        def fetch(self, venue, market, kind, span):
            return table

    span = TimeRange(DAY1_MS, DAY1_MS + DAY_MS)
    batches = list(
        TableOnlySource().fetch_batches(
            "hyperliquid", "SOL-PERP", Kind.BOOK_DELTA, span, batch_size=64
        )
    )
    assert max(b.num_rows for b in batches) <= 64
    assert sum(b.num_rows for b in batches) == table.num_rows


def test_fetch_batches_upgrades_old_schema_files_per_batch(tmp_path):
    # A lake-v1 funding file (no settlement_ts) promotes on the streaming read
    # path too — batch-local, because lake migrations are row-local.
    from flint.data.store.layout import write_parquet

    ts = _ms(2025, 1, 1, 1)
    v1 = pa.table(
        {
            "ts": [ts],
            "rate_hourly": [0.0001],
            "rate_type": ["final"],
            "venue": ["hyperliquid"],
            "market": ["SOL-PERP"],
        }
    )
    part_dir = tmp_path / "funding" / "hyperliquid" / "SOL-PERP" / "2025-01-01"
    part_dir.mkdir(parents=True)
    write_parquet(v1, str(part_dir / "part.parquet"), schema_version=1)

    cache = DurableCacheSource(tmp_path)
    batches = list(
        cache.fetch_batches(
            "hyperliquid", "SOL-PERP", Kind.FUNDING, TimeRange(ts, ts + 1)
        )
    )
    assert len(batches) == 1
    assert "settlement_ts" in batches[0].schema.names
    assert batches[0].column("settlement_ts").to_pylist() == [ts]


# --- PreparedData.streams (additive, D5) -------------------------------------


def _funding_covering(span: TimeRange) -> pa.Table:
    """Hand-authored funding rows pinning the envelope over ``span``."""
    return pa.table(
        {
            "ts": [span.start_ms, span.end_ms - 1],
            "rate_hourly": [0.0001, 0.0001],
            "venue": ["hyperliquid", "hyperliquid"],
            "market": ["SOL-PERP", "SOL-PERP"],
        }
    )


def _trades_rows(ts_values: list[int]) -> pa.Table:
    from flint.data.normalize import TRADES_SCHEMA

    return pa.Table.from_pylist(
        [
            {
                "ts": ts,
                "market": "SOL-PERP",
                "venue": "hyperliquid",
                "price": 211.5,
                "size": 1.0,
                "side": "buy",
                "trade_id": 100 + i,
            }
            for i, ts in enumerate(ts_values)
        ],
        schema=TRADES_SCHEMA,
    )


def test_prepare_serves_book_delta_as_stream_not_table(tmp_path):
    cache, table = _seeded_cache(tmp_path)
    requested = TimeRange(DAY1_MS, DAY1_MS + 3_600_000)  # hour 0 of the real day
    cache.store("hyperliquid", "SOL-PERP", Kind.FUNDING, _funding_covering(requested))
    trades_ts = [DAY1_MS + 1_000, DAY1_MS + 2_000]
    cache.store("hyperliquid", "SOL-PERP", Kind.TRADES, _trades_rows(trades_ts))
    cache.coverage_ledger("hyperliquid", "SOL-PERP", Kind.TRADES).assert_covered(
        requested, "recorder"
    )

    manager = DataManager(sources=[cache])
    prepared = manager.prepare(
        universe=["SOL-PERP"],
        venues=["hyperliquid"],
        kinds=[Kind.BOOK_DELTA, Kind.TRADES, Kind.FUNDING],
        requested=requested,
    )

    key = ("hyperliquid", "SOL-PERP", Kind.BOOK_DELTA)
    # tables does NOT carry BOOK_DELTA whole — placeholder only.
    assert prepared.tables[key].num_rows == 0
    # ...the stream handle does, batch by batch, restartably.
    first = list(prepared.streams[key]())
    second = list(prepared.streams[key]())
    assert sum(b.num_rows for b in first) == table.num_rows
    assert sum(b.num_rows for b in second) == table.num_rows

    # BOOK_DELTA fidelity is computed from coverage alone (no fetch): full.
    book_entries = [
        e for e in prepared.fidelity.entries if e.kind is Kind.BOOK_DELTA
    ]
    assert len(book_entries) == 1 and book_entries[0].full

    # TRADES keeps its table exactly as before D5 AND gains a stream handle.
    trades_key = ("hyperliquid", "SOL-PERP", Kind.TRADES)
    assert prepared.tables[trades_key].num_rows == 2
    assert sum(b.num_rows for b in prepared.streams[trades_key]()) == 2

    # Non-tick kinds stay table-only: no stream handles for FUNDING.
    assert ("hyperliquid", "SOL-PERP", Kind.FUNDING) not in prepared.streams


def test_prepared_data_streams_default_is_empty():
    # Additive contract: constructing PreparedData exactly as pre-D5 callers
    # did (no streams argument) still works and yields an empty mapping.
    from flint.data.manager import PreparedData

    prepared = PreparedData(
        requested=TimeRange(0, 1), effective_range=TimeRange(0, 1)
    )
    assert prepared.streams == {}
