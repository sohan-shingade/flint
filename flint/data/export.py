"""Parquet export/import for DuckDB data. Thread-safe via store lock."""
from __future__ import annotations

from pathlib import Path

from ..store import FlintStore


def export_table_parquet(store: FlintStore, table: str, path: str) -> int:
    """Export a DuckDB table to a Parquet file. Returns row count."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with store._lock:
        store._conn.execute(
            f"COPY {table} TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        result = store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return result[0] if result else 0


def import_parquet(store: FlintStore, path: str, table: str) -> int:
    """Import a Parquet file into a DuckDB table. Returns row count."""
    with store._lock:
        store._conn.execute(
            f"INSERT OR REPLACE INTO {table} SELECT * FROM read_parquet('{path}')"
        )
        result = store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return result[0] if result else 0
