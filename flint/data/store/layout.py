"""Parquet lake layout + upgrade-on-read migration (§9.0).

The lake is Parquet on object storage, partitioned ``kind/venue/market/date/``
(hour-partitioned for depth), with the ``schema_version`` stamped in each file's
metadata. Historical files are **never mass-rewritten**: a reader that finds an
older ``schema_version`` walks it forward through a migration registry (v*N* →
v*N+1* transforms) up to the current version — the same upgrade-on-read contract
the event log uses for events (§2.10), applied to columnar market-data files.
"""

from __future__ import annotations

from collections.abc import Callable

import pyarrow as pa
import pyarrow.parquet as pq

# The current lake schema version. Bump when a file layout changes and register a
# migration from the previous version so old files upgrade on read.
# v2: FUNDING gains a nullable ``settlement_ts`` column (other kinds unchanged).
SCHEMA_VERSION = 2

_SCHEMA_VERSION_KEY = b"flint_schema_version"
# Depth and the tick-scale kinds (trades/quotes/book deltas) are
# hour-partitioned; every other kind is day-partitioned (§9.0).
_HOUR_PARTITIONED = frozenset({"depth", "trades", "quotes", "book_delta"})

# Per-kind Parquet writer options (D5 write tuning). BOOK_DELTA is the only
# kind whose volume warrants tuning: zstd beats the pyarrow default (snappy) by
# ~14% on the real 2026-06-01 HL fragment (16.9 vs 19.6 bytes/row on disk;
# ~74 bytes/row decoded), and ~1M-row row groups (~17 MB on disk / ~75 MB
# decoded at those rates) keep a full HL day (tens of millions of flat delta
# rows) split into dozens of independently readable groups, so a streaming
# reader's peak memory is one row group, never a day. Every other kind gets
# NO options — byte-identical writer behavior to pre-D5 files (and old files
# need no migration either way: these are writer defaults, not schema).
_WRITER_OPTIONS_BY_KIND: dict[str, dict[str, object]] = {
    "book_delta": {"compression": "zstd", "row_group_size": 1_000_000},
}


def is_hour_partitioned(kind: str) -> bool:
    """True if ``kind`` partitions ``date/hour`` instead of ``date`` (§9.0)."""
    return kind in _HOUR_PARTITIONED


def partition_path(
    kind: str, venue: str, market: str, date: str, *, hour: int | None = None
) -> str:
    """Return the partition sub-path ``kind/venue/market/date[/hour]`` (§9.0).

    ``date`` is an ISO ``YYYY-MM-DD`` string. Depth is hour-partitioned, so an
    ``hour`` (0–23) is required for it and rejected for every other kind.
    """
    if kind in _HOUR_PARTITIONED:
        if hour is None:
            raise ValueError(f"{kind!r} is hour-partitioned; pass hour=")
        return f"{kind}/{venue}/{market}/{date}/{hour:02d}"
    if hour is not None:
        raise ValueError(f"{kind!r} is day-partitioned; hour= is not allowed")
    return f"{kind}/{venue}/{market}/{date}"


def write_parquet(
    table: pa.Table,
    path: str,
    *,
    schema_version: int = SCHEMA_VERSION,
    kind: str | None = None,
) -> None:
    """Write ``table`` to ``path`` with ``schema_version`` stamped in metadata.

    ``kind`` (the ``Kind`` string value) selects per-kind writer options
    (:data:`_WRITER_OPTIONS_BY_KIND` — zstd + ~1M-row row groups for
    ``book_delta``); omitted or unlisted kinds write with pyarrow defaults,
    exactly as before D5.
    """
    existing = dict(table.schema.metadata or {})
    existing[_SCHEMA_VERSION_KEY] = str(schema_version).encode()
    options = _WRITER_OPTIONS_BY_KIND.get(kind or "", {})
    pq.write_table(table.replace_schema_metadata(existing), path, **options)


def read_schema_version(path: str) -> int:
    """Return the ``schema_version`` stamped in ``path`` (0 if unstamped)."""
    metadata = pq.read_metadata(path).metadata or {}
    raw = metadata.get(_SCHEMA_VERSION_KEY)
    return int(raw) if raw is not None else 0


class MigrationRegistry:
    """Upgrade-on-read for lake Parquet files (§9.0).

    Register a transform per *from* version; ``upgrade`` walks a table forward
    one version at a time to the target. Mirrors the event ``UpcasterRegistry``:
    on-disk files are never rewritten — the promotion happens in memory on read.
    """

    def __init__(self) -> None:
        self._migrations: dict[int, Callable[[pa.Table], pa.Table]] = {}

    def register(
        self, from_version: int
    ) -> Callable[[Callable[[pa.Table], pa.Table]], Callable[[pa.Table], pa.Table]]:
        """Register the transform that promotes ``from_version`` → ``from_version + 1``."""

        def decorator(
            fn: Callable[[pa.Table], pa.Table]
        ) -> Callable[[pa.Table], pa.Table]:
            if from_version in self._migrations:
                raise ValueError(
                    f"migration from schema v{from_version} already registered"
                )
            self._migrations[from_version] = fn
            return fn

        return decorator

    def upgrade(
        self, table: pa.Table, from_version: int, to_version: int = SCHEMA_VERSION
    ) -> pa.Table:
        """Walk ``table`` from ``from_version`` up to ``to_version``, one step at a time."""
        version = from_version
        while version < to_version:
            step = self._migrations.get(version)
            if step is None:
                raise KeyError(f"no migration registered from schema v{version}")
            table = step(table)
            version += 1
        return table


def read_parquet(
    path: str,
    *,
    registry: MigrationRegistry | None = None,
    to_version: int = SCHEMA_VERSION,
) -> pa.Table:
    """Read ``path`` and upgrade it on read to ``to_version`` if it is older.

    ``registry`` defaults to the built-in :data:`DEFAULT_MIGRATIONS`; a current
    file is returned as-is. The stored file is untouched (upgrade happens in
    memory).
    """
    table = pq.read_table(path)
    version = read_schema_version(path)
    if version < to_version:
        table = (registry or DEFAULT_MIGRATIONS).upgrade(table, version, to_version)
    return table


# --- built-in lake migrations ------------------------------------------------

# The registry every reader gets by default. Each lake SCHEMA_VERSION bump
# registers its upgrade step here so old files promote on read everywhere.
DEFAULT_MIGRATIONS = MigrationRegistry()


@DEFAULT_MIGRATIONS.register(1)
def _v1_to_v2(table: pa.Table) -> pa.Table:
    """Lake v2: FUNDING gains a nullable ``settlement_ts`` column.

    Only funding tables (recognised by their ``rate_type`` column) change; every
    other kind passes through untouched. Legacy rows never recorded a settlement
    time, so it is *derived, not fabricated*: a settled row (``rate_type`` of
    ``"final"`` — or the pre-greenfield import's ``"legacy"``, which is a
    settled rate) settles at its own ``ts``; a ``"predicted"`` row has no
    settlement yet and stays null. Appended last to match ``FUNDING_SCHEMA``.
    """
    import pyarrow.compute as pc

    if "rate_type" not in table.column_names or "settlement_ts" in table.column_names:
        return table
    settled = pc.not_equal(table.column("rate_type"), pa.scalar("predicted"))
    settlement_ts = pc.if_else(
        settled,
        table.column("ts").cast(pa.int64()),
        pa.scalar(None, pa.int64()),
    )
    return table.append_column(
        pa.field("settlement_ts", pa.int64()), settlement_ts
    )
