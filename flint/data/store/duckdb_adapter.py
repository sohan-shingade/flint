"""Durable DuckDB port adapters — the v1 laptop store (§2.7, §9).

Phase 1 shipped in-memory reference adapters that prove the *seams* without a
database. These are their durable counterparts: the same two ports (market data,
user data) backed by a DuckDB file so a run survives a restart. Swapping these in
for the in-memory ones is the one-line wiring change ports exist to make (D12).

Two adapters, opposite tenancy (§2.7):

* ``DuckDBMarketData`` — candles keyed ``(venue, market, ts)``, tenant-agnostic
  (the same candles for everyone). Arrow ``Table`` in and out; half-open
  ``[start_ts, end_ts)`` loads (a bar at exactly ``end_ts`` is excluded, matching
  ``core.time``). Saves are idempotent per key so re-fetching a range is a no-op.
* ``DuckDBUserData`` — runs + event rows carry a ``tenant_id`` column and *every*
  query filters by it. A cross-tenant read behaves exactly as if the row did not
  exist (constant ``KeyError`` message — no existence leak, §2.7). Event rows are
  append-only: an internal monotonic ordinal preserves append order and stored
  rows are never rewritten (§2.10).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import duckdb
import pyarrow as pa

from flint.ports import MarketDataPort, RunRecord, TenantContext, UserDataPort


class DuckDBMarketData(MarketDataPort):
    """Tenant-agnostic candle store backed by a DuckDB file (or ``:memory:``).

    The candle table is created lazily from the first saved Arrow schema and
    keyed ``(venue, market, ts)``; ``venue``/``market`` are stored as columns but
    stripped on load so the round-trip preserves the caller's schema exactly.
    """

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = duckdb.connect(path)
        # Whether the candle table exists yet (created from the first schema).
        self._ready = (
            self._conn.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name = 'candles'"
            ).fetchone()[0]
            > 0
        )

    def save_candles(self, venue: str, market: str, candles: pa.Table) -> int:
        if candles.num_rows == 0:
            return 0
        conn = self._conn
        conn.register("_incoming", candles)
        try:
            if not self._ready:
                # Prepend venue/market to the incoming schema; LIMIT 0 makes the
                # empty typed shell we then insert into.
                conn.execute(
                    "CREATE TABLE candles AS "
                    "SELECT CAST(NULL AS VARCHAR) AS venue, "
                    "CAST(NULL AS VARCHAR) AS market, * FROM _incoming LIMIT 0"
                )
                self._ready = True
            # Exact-key idempotent upsert: drop any rows whose ts we're about to
            # rewrite for this (venue, market), then insert. Re-saving a range
            # leaves the store unchanged (immutable closed candles, §9.0).
            conn.execute(
                "DELETE FROM candles WHERE venue = ? AND market = ? "
                "AND ts IN (SELECT ts FROM _incoming)",
                [venue, market],
            )
            conn.execute(
                "INSERT INTO candles SELECT ?, ?, * FROM _incoming", [venue, market]
            )
        finally:
            conn.unregister("_incoming")
        return candles.num_rows

    def load_candles(
        self, venue: str, market: str, start_ts: int, end_ts: int
    ) -> pa.Table:
        if not self._ready:
            return pa.table({})
        return self._conn.execute(
            "SELECT * EXCLUDE (venue, market) FROM candles "
            "WHERE venue = ? AND market = ? AND ts >= ? AND ts < ? ORDER BY ts",
            [venue, market, start_ts, end_ts],
        ).to_arrow_table()


class DuckDBUserData(UserDataPort):
    """Per-tenant run + event store — the durable proof of the cross-leak seam.

    Every row carries ``tenant_id`` and every query filters by it. Events are
    append-only, ordered by a per-``(tenant, run)`` monotonic ordinal assigned at
    write time (independent of any ``seq`` inside the opaque row), so append order
    survives regardless of what the caller stores.
    """

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = duckdb.connect(path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS runs ("
            "  tenant_id VARCHAR, run_id VARCHAR, kind VARCHAR, status VARCHAR, "
            "  created_ts BIGINT, summary VARCHAR, "
            "  PRIMARY KEY (tenant_id, run_id))"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "  tenant_id VARCHAR, run_id VARCHAR, ordinal BIGINT, row VARCHAR, "
            "  PRIMARY KEY (tenant_id, run_id, ordinal))"
        )

    def save_run(self, tenant: TenantContext, run: RunRecord) -> str:
        self._conn.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (tenant_id, run_id) DO UPDATE SET "
            "  kind = excluded.kind, status = excluded.status, "
            "  created_ts = excluded.created_ts, summary = excluded.summary",
            [
                tenant.tenant_id,
                run.run_id,
                run.kind,
                run.status,
                run.created_ts,
                json.dumps(dict(run.summary)),
            ],
        )
        return run.run_id

    def load_run(self, tenant: TenantContext, run_id: str) -> RunRecord:
        rows = self._conn.execute(
            "SELECT run_id, kind, status, created_ts, summary FROM runs "
            "WHERE tenant_id = ? AND run_id = ?",
            [tenant.tenant_id, run_id],
        ).fetchall()
        if not rows:
            # Constant message whether absent or owned by another tenant — a
            # probing caller learns nothing (no cross-tenant existence leak).
            raise KeyError("run not found for tenant")
        return self._to_record(rows[0])

    def list_runs(self, tenant: TenantContext) -> list[RunRecord]:
        rows = self._conn.execute(
            "SELECT run_id, kind, status, created_ts, summary FROM runs "
            "WHERE tenant_id = ? ORDER BY run_id",
            [tenant.tenant_id],
        ).fetchall()
        return [self._to_record(r) for r in rows]

    def append_events(
        self, tenant: TenantContext, run_id: str, rows: Sequence[Mapping[str, Any]]
    ) -> int:
        conn = self._conn
        base = conn.execute(
            "SELECT COALESCE(MAX(ordinal) + 1, 0) FROM events "
            "WHERE tenant_id = ? AND run_id = ?",
            [tenant.tenant_id, run_id],
        ).fetchone()[0]
        for offset, row in enumerate(rows):
            conn.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?)",
                [tenant.tenant_id, run_id, base + offset, json.dumps(dict(row))],
            )
        return conn.execute(
            "SELECT count(*) FROM events WHERE tenant_id = ? AND run_id = ?",
            [tenant.tenant_id, run_id],
        ).fetchone()[0]

    def load_events(
        self, tenant: TenantContext, run_id: str
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT row FROM events WHERE tenant_id = ? AND run_id = ? "
            "ORDER BY ordinal",
            [tenant.tenant_id, run_id],
        ).fetchall()
        return [json.loads(r[0]) for r in rows]

    @staticmethod
    def _to_record(row: tuple[Any, ...]) -> RunRecord:
        run_id, kind, status, created_ts, summary = row
        return RunRecord(
            run_id=run_id,
            kind=kind,
            status=status,
            created_ts=created_ts,
            summary=json.loads(summary),
        )
