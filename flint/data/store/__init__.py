"""data.store — durable port adapters + lake layout (§2.7, §9, §9.0).

The two DuckDB adapters (``DuckDBMarketData``, ``DuckDBUserData``) fulfil the
storage ports on a laptop; ``layout`` holds the Parquet lake partitioning +
upgrade-on-read migration, and ``cache`` holds the content-addressed cache-key +
freshness policy the local client uses against the hosted lake.
"""

from .cache import (
    FRESHNESS_TTL_MS,
    REVISABLE_KINDS,
    cache_key,
    is_fresh,
    is_immutable,
)
from .coverage import CoverageEntry, CoverageLedger
from .durable_cache import DurableCacheSource
from .duckdb_adapter import DuckDBMarketData, DuckDBUserData
from .layout import (
    DEFAULT_MIGRATIONS,
    SCHEMA_VERSION,
    MigrationRegistry,
    is_hour_partitioned,
    partition_path,
    read_parquet,
    read_schema_version,
    write_parquet,
)

__all__ = [
    "CoverageEntry",
    "CoverageLedger",
    "DuckDBMarketData",
    "DuckDBUserData",
    "DurableCacheSource",
    "DEFAULT_MIGRATIONS",
    "SCHEMA_VERSION",
    "MigrationRegistry",
    "is_hour_partitioned",
    "partition_path",
    "read_parquet",
    "read_schema_version",
    "write_parquet",
    "REVISABLE_KINDS",
    "FRESHNESS_TTL_MS",
    "cache_key",
    "is_fresh",
    "is_immutable",
]
