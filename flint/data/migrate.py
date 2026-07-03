"""One-shot importer: legacy Flint v1.x DuckDB -> the new store (§19.6).

The greenfield rewrite deletes old *code*, never old *data* — and the recorded
market data in a legacy Flint DuckDB (candles, funding, locally-recorded depth
and open interest) is *now-or-never* by our own argument: nobody can re-record
yesterday's book. This module lifts that data into the new Parquet lake layout,
row-for-row, translating the legacy schema into the canonical Arrow schemas the
engine reads today.

Contract:

* The legacy file is opened **strictly read-only** (``read_only=True``); the
  importer never writes a byte back to it, so it stays a safe, verifiable
  original until the operator is satisfied with the import.
* It writes only through an injected sink (``MigrationSink`` — anything with the
  ``store(venue, market, kind, table)`` write-through method, i.e. every cache
  tier). The importer never touches storage directly, honoring the ports rule.
* It fabricates no market data (D26). Where the legacy schema carries a field the
  new schema does not (funding ``mark``/``index``; OI ``short_oi``) the field is
  dropped, documented in ``docs/redesign/MIGRATION.md``. Where the new schema
  carries a field the legacy row never recorded (funding cadence/basis; OI
  mark/index/funding), the value is a null or an explicit ``"legacy"`` provenance
  sentinel — never an invented number.
* It returns a :class:`MigrationReport` — per-kind row counts + a run-metadata
  inventory — so the operator can diff source against destination before trusting
  the copy.

Run metadata (equity journals, strategy rows) has no market-data home in the new
store, so :func:`migrate_legacy_duckdb` only *inventories* those tables (row counts
in the report) — it never moves them. The Run Library (Phase 6, ``research/runlib.py``)
is what owns them, and :func:`extract_legacy_run_metadata` /
:func:`import_legacy_runs_from_duckdb` are the read-only bridge into it: they lift the
``strategies`` / ``journal_equity`` rows out of the same read-only connection and hand
them to the Run Library importer, which maps them into tenant-scoped head records with
honest ``"legacy"`` sentinels (never a fabricated seed or engine version, D26). See
``docs/redesign/MIGRATION.md`` and ``research/runlib.py``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import pyarrow as pa

from .ingest.backfillers.hyperliquid import _CANDLE_SCHEMA
from .normalize import DEPTH_SCHEMA, FUNDING_SCHEMA, OI_SCHEMA
from .ranges import Kind

if TYPE_CHECKING:  # pragma: no cover - typing only
    import duckdb

    from flint.ports import TenantContext, UserDataPort

_logger = logging.getLogger("flint.data.migrate")

# Legacy table names (recovered from bf2d385:flint/store/schema.py) mapped to the
# new-store Kind they carry. These four are the now-or-never recorded market data.
_CANDLES_TABLE = "candles"
_FUNDING_TABLE = "venue_funding_rates"
_DEPTH_TABLE = "orderbook_snapshots"
_OI_TABLE = "open_interest"

# Run-metadata tables inventoried (counted) but not moved — see module docstring.
_RUN_METADATA_TABLES: tuple[str, ...] = ("journal_equity", "strategies")

# Provenance sentinels for funding fields the legacy schema never recorded. The
# legacy ``rate_hourly`` is already an hourly rate, but the settlement cadence,
# price basis, and rate type were not stored, so they are marked, not guessed.
_LEGACY_FUNDING_INTERVAL_S = 0  # unknown native cadence
_LEGACY_FUNDING_PRICE_BASIS = "legacy"
_LEGACY_FUNDING_RATE_TYPE = "legacy"


class MigrationSink(Protocol):
    """The write side the importer targets — every cache tier satisfies it.

    Matches ``InMemoryCacheSource.store`` / ``DurableCacheSource.store``: an
    idempotent upsert keyed ``(venue, market, ts)``, so re-running the import over
    an already-imported store is a no-op.
    """

    def store(
        self, venue: str, market: str, kind: Kind, table: pa.Table
    ) -> None: ...


@dataclass
class MigrationReport:
    """Verification report: what came out of the legacy file, per kind."""

    rows_by_kind: dict[Kind, int] = field(default_factory=dict)
    groups_by_kind: dict[Kind, int] = field(default_factory=dict)
    run_metadata_counts: dict[str, int] = field(default_factory=dict)
    skipped_tables: list[str] = field(default_factory=list)

    @property
    def total_market_rows(self) -> int:
        return sum(self.rows_by_kind.values())

    def format(self) -> str:
        """Render a human-readable verification report (row counts per kind)."""
        lines = ["Legacy DuckDB migration — verification report", ""]
        lines.append("Market data imported into the new store:")
        if self.rows_by_kind:
            for kind in (Kind.CANDLES, Kind.FUNDING, Kind.DEPTH, Kind.OI):
                if kind in self.rows_by_kind:
                    groups = self.groups_by_kind.get(kind, 0)
                    lines.append(
                        f"  {kind.value:<8} {self.rows_by_kind[kind]:>12,} rows "
                        f"across {groups} venue/market series"
                    )
        else:
            lines.append("  (none)")
        lines.append(f"  {'total':<8} {self.total_market_rows:>12,} rows")
        lines.append("")
        lines.append("Run metadata inventoried (deferred to Phase-6 Run Library):")
        if self.run_metadata_counts:
            for name, n in self.run_metadata_counts.items():
                lines.append(f"  {name:<20} {n:>12,} rows  (not moved)")
        else:
            lines.append("  (none)")
        if self.skipped_tables:
            lines.append("")
            lines.append("Absent legacy tables (older DB, nothing to import):")
            for name in self.skipped_tables:
                lines.append(f"  {name}")
        return "\n".join(lines)


def migrate_legacy_duckdb(
    legacy_path: str | Path, sink: MigrationSink
) -> MigrationReport:
    """Import a legacy Flint v1.x DuckDB into ``sink``; the file is read-only.

    Returns a :class:`MigrationReport` with per-kind row counts for verification.
    Idempotent: the sink's upsert semantics make a re-run a no-op.
    """
    import duckdb

    path = Path(legacy_path)
    if not path.exists():
        raise FileNotFoundError(f"legacy DuckDB not found: {path}")

    report = MigrationReport()
    # read_only=True is the whole read-only guarantee: DuckDB refuses any DML.
    con = duckdb.connect(str(path), read_only=True)
    try:
        _migrate_candles(con, sink, report)
        _migrate_funding(con, sink, report)
        _migrate_depth(con, sink, report)
        _migrate_oi(con, sink, report)
        _inventory_run_metadata(con, report)
    finally:
        con.close()

    _logger.info(
        "legacy migration complete: %d market rows across %d kinds",
        report.total_market_rows,
        len(report.rows_by_kind),
    )
    return report


# --- per-kind importers -----------------------------------------------------


def _migrate_candles(
    con: duckdb.DuckDBPyConnection, sink: MigrationSink, report: MigrationReport
) -> None:
    if not _table_exists(con, _CANDLES_TABLE):
        report.skipped_tables.append(_CANDLES_TABLE)
        return
    rows = _select(
        con,
        f"SELECT venue, market, resolution_s, ts, open, high, low, close, volume "
        f"FROM {_CANDLES_TABLE}",
    )
    _store_grouped(
        sink,
        Kind.CANDLES,
        _CANDLE_SCHEMA,
        report,
        (
            (
                r["venue"],
                r["market"],
                {
                    "ts": int(r["ts"]),
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": float(r["volume"]),
                    "market": r["market"],
                    "resolution_s": int(r["resolution_s"]),
                    "venue": r["venue"],
                },
            )
            for r in rows
        ),
    )


def _migrate_funding(
    con: duckdb.DuckDBPyConnection, sink: MigrationSink, report: MigrationReport
) -> None:
    if not _table_exists(con, _FUNDING_TABLE):
        report.skipped_tables.append(_FUNDING_TABLE)
        return
    # Legacy mark_price / index_price have no home in FUNDING_SCHEMA and are
    # dropped here (documented in MIGRATION.md); rate_hourly is carried verbatim.
    rows = _select(
        con,
        f"SELECT venue, market, ts, rate_hourly FROM {_FUNDING_TABLE}",
    )
    _store_grouped(
        sink,
        Kind.FUNDING,
        FUNDING_SCHEMA,
        report,
        (
            (
                r["venue"],
                r["market"],
                {
                    "ts": int(r["ts"]),
                    "rate_hourly": float(r["rate_hourly"]),
                    "interval_s": _LEGACY_FUNDING_INTERVAL_S,
                    "price_basis": _LEGACY_FUNDING_PRICE_BASIS,
                    "rate_type": _LEGACY_FUNDING_RATE_TYPE,
                    "venue": r["venue"],
                    "market": r["market"],
                    # Legacy rows are settled rates: they settled at their ts.
                    "settlement_ts": int(r["ts"]),
                },
            )
            for r in rows
        ),
    )


def _migrate_depth(
    con: duckdb.DuckDBPyConnection, sink: MigrationSink, report: MigrationReport
) -> None:
    if not _table_exists(con, _DEPTH_TABLE):
        report.skipped_tables.append(_DEPTH_TABLE)
        return
    # Legacy stores four parallel arrays; the new schema stores best-first
    # [[px, sz], ...] level pairs. Zip them back together, truncating to the
    # shorter side so a malformed legacy row never invents a price or a size.
    rows = _select(
        con,
        f"SELECT venue, market, ts, bid_prices, bid_sizes, ask_prices, ask_sizes "
        f"FROM {_DEPTH_TABLE}",
    )
    _store_grouped(
        sink,
        Kind.DEPTH,
        DEPTH_SCHEMA,
        report,
        (
            (
                r["venue"],
                r["market"],
                {
                    "ts": int(r["ts"]),
                    "market": r["market"],
                    "venue": r["venue"],
                    "bids": _zip_levels(r["bid_prices"], r["bid_sizes"]),
                    "asks": _zip_levels(r["ask_prices"], r["ask_sizes"]),
                },
            )
            for r in rows
        ),
    )


def _migrate_oi(
    con: duckdb.DuckDBPyConnection, sink: MigrationSink, report: MigrationReport
) -> None:
    if not _table_exists(con, _OI_TABLE):
        report.skipped_tables.append(_OI_TABLE)
        return
    # Aggregate open interest is one side of the book (every long has a short), so
    # long_oi is the canonical ``oi``; short_oi is dropped (documented). The new
    # OI row also carries mark/index/funding, which the legacy OI table never
    # recorded — those are null (not zero: a zero price would be a fabrication).
    rows = _select(
        con,
        f"SELECT venue, market, ts, long_oi FROM {_OI_TABLE}",
    )
    _store_grouped(
        sink,
        Kind.OI,
        OI_SCHEMA,
        report,
        (
            (
                r["venue"],
                r["market"],
                {
                    "ts": int(r["ts"]),
                    "market": r["market"],
                    "venue": r["venue"],
                    "oi": float(r["long_oi"]),
                    "mark_price": None,
                    "index_price": None,
                    "funding_hourly": None,
                },
            )
            for r in rows
        ),
    )


def _inventory_run_metadata(
    con: duckdb.DuckDBPyConnection, report: MigrationReport
) -> None:
    """Count run-metadata rows without moving them (no Phase-2 target)."""
    for name in _RUN_METADATA_TABLES:
        if _table_exists(con, name):
            (count,) = con.execute(f"SELECT count(*) FROM {name}").fetchone()
            report.run_metadata_counts[name] = int(count)


# --- run-metadata bridge into the Run Library (§19.6, research/runlib.py) ----


def extract_legacy_run_metadata(
    legacy_path: str | Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read the legacy ``strategies`` / ``journal_equity`` rows as import-shaped dicts.

    The read-only companion to :func:`migrate_legacy_duckdb` (which only *counts* these
    run-metadata tables): opens ``legacy_path`` strictly read-only (§19.6 — the greenfield
    deletes old code, never old data) and returns ``(strategy_rows, journal_rows)`` in the
    exact shape :func:`flint.research.runlib.import_legacy_runs` accepts. Legacy column
    names are translated to that surface's keys and values are carried verbatim — nothing
    is fabricated (D26); absent tables yield an empty list, never an error.

    ``journal_equity`` has no stored link to ``strategies`` in the legacy schema (its
    ``run_id`` is a run identifier, not a strategy name/id), so the two are extracted
    independently and grouped downstream by their own keys — an honest reflection of two
    disconnected legacy tables, not an invented join.
    """
    import duckdb

    path = Path(legacy_path)
    if not path.exists():
        raise FileNotFoundError(f"legacy DuckDB not found: {path}")

    con = duckdb.connect(str(path), read_only=True)
    try:
        strategy_rows = _extract_strategy_rows(con)
        journal_rows = _extract_journal_rows(con)
    finally:
        con.close()
    return strategy_rows, journal_rows


def import_legacy_runs_from_duckdb(
    tenant: TenantContext,
    port: UserDataPort,
    legacy_path: str | Path,
) -> list[str]:
    """One call: extract legacy run metadata and persist it into ``tenant``'s Run Library.

    Bridges :func:`extract_legacy_run_metadata` (read-only lift out of the legacy DuckDB)
    into :func:`flint.research.runlib.import_legacy_runs` (tenant-scoped write through
    ``UserDataPort``, §2.7). Idempotent on ``legacy:<name>`` — re-running over an
    already-imported Run Library is a no-op. Returns the imported run_ids.
    """
    # Lazy import: this module is a one-shot operational bridge, and the Run Library
    # lives in research/ (a sibling of data/, §4) — data/ never imports research/ at
    # module load, so the coupling exists only when this bridge is actually called.
    from flint.research.runlib import import_legacy_runs

    strategy_rows, journal_rows = extract_legacy_run_metadata(legacy_path)
    return import_legacy_runs(
        tenant, port, strategy_rows=strategy_rows, journal_rows=journal_rows
    )


def _extract_strategy_rows(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """``strategies`` rows shaped for the Run Library importer (name/source/created_ts)."""
    if not _table_exists(con, "strategies"):
        return []
    rows = _select(con, "SELECT name, code, created_at FROM strategies")
    return [
        {
            "name": r["name"],
            "source": r["code"],
            "created_ts": int(r["created_at"]),
        }
        for r in rows
    ]


def _extract_journal_rows(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """``journal_equity`` rows shaped for the importer, ordered so each run's last row
    is its latest-ts equity endpoint (the importer reads the group's tail verbatim)."""
    if not _table_exists(con, "journal_equity"):
        return []
    rows = _select(
        con, "SELECT run_id, ts, equity FROM journal_equity ORDER BY run_id, ts"
    )
    return [
        {"strategy": r["run_id"], "ts": int(r["ts"]), "equity": r["equity"]}
        for r in rows
    ]


# --- helpers ----------------------------------------------------------------


def _store_grouped(
    sink: MigrationSink,
    kind: Kind,
    schema: pa.Schema,
    report: MigrationReport,
    triples: Any,
) -> None:
    """Group ``(venue, market, row_dict)`` triples and write one table per series.

    ``sink.store`` is called per ``(venue, market, kind)`` — the store keys
    partitions that way — so rows are bucketed by series before the write.
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for venue, market, row in triples:
        grouped[(venue, market)].append(row)

    total = 0
    for (venue, market), row_dicts in grouped.items():
        table = pa.Table.from_pylist(row_dicts, schema=schema)
        sink.store(venue, market, kind, table)
        total += table.num_rows
    if total:
        report.rows_by_kind[kind] = total
        report.groups_by_kind[kind] = len(grouped)


def _zip_levels(prices: Any, sizes: Any) -> list[list[float]]:
    prices = prices or []
    sizes = sizes or []
    return [[float(px), float(sz)] for px, sz in zip(prices, sizes)]


def _table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return row is not None


def _select(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cur = con.execute(sql)
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]
