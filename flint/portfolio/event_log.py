"""Event-sourcing primitives for the portfolio engine.

D-6.4-replay foundation (Wave 5 first slice). Defines the event-log
schema + writer that future replay/time-travel features will read.
This commit ships only the **append** path; replay + snapshots come in
follow-on slices once the writer is exercised by real engine runs.

Schema:

    portfolio_events(
        session_id  VARCHAR  -- ties events to a backtest or paper session
        seq         BIGINT   -- monotonic per session_id (caller assigns)
        ts          BIGINT   -- epoch seconds (event time, not write time)
        kind        VARCHAR  -- one of EventKind values
        payload     VARCHAR  -- JSON-encoded dict
        PRIMARY KEY (session_id, seq)
    )

The composite PK enforces monotonic seq per session without DuckDB's
AUTOINCREMENT (which doesn't support per-group reset). The writer
tracks `next_seq` in memory and persists it via `MAX(seq)+1` on
process restart.

Event kinds (`EventKind`):
  - `order.submit` — order entered the queue
  - `order.cancel` — explicit cancellation
  - `fill`         — fill applied to the book
  - `liquidation`  — margin engine force-closed a position
  - `funding`      — funding payment booked
  - `borrow`       — Jupiter borrow cost realized

Payloads are JSON for forward-compatibility — the schema can grow new
optional fields without a migration.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class EventKind:
    ORDER_SUBMIT = "order.submit"
    ORDER_CANCEL = "order.cancel"
    FILL = "fill"
    LIQUIDATION = "liquidation"
    FUNDING = "funding"
    BORROW = "borrow"


CREATE_PORTFOLIO_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_events (
    session_id VARCHAR NOT NULL,
    seq        BIGINT  NOT NULL,
    ts         BIGINT  NOT NULL,
    kind       VARCHAR NOT NULL,
    payload    VARCHAR NOT NULL,
    PRIMARY KEY (session_id, seq)
);
"""

CREATE_PORTFOLIO_EVENTS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_portfolio_events_session_ts
    ON portfolio_events(session_id, ts);
"""


@dataclass(frozen=True)
class PortfolioEvent:
    session_id: str
    seq: int
    ts: int
    kind: str
    payload: Dict[str, Any]

    def to_row(self) -> tuple:
        return (self.session_id, self.seq, self.ts, self.kind,
                json.dumps(self.payload, separators=(",", ":")))

    @classmethod
    def from_row(cls, row: tuple) -> "PortfolioEvent":
        session_id, seq, ts, kind, payload_json = row
        return cls(
            session_id=session_id, seq=int(seq), ts=int(ts),
            kind=kind, payload=json.loads(payload_json),
        )


class EventLogWriter:
    """Append-only writer with per-session monotonic seq.

    The writer is thread-safe: `_lock` serializes all DB-touching
    operations. Seq counters cache in `_next_seq`; on first append
    for a session, the writer queries the DB for `MAX(seq)` so process
    restarts pick up cleanly.
    """

    def __init__(self, store) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._next_seq: Dict[str, int] = {}
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._lock:
            self._store._conn.execute(CREATE_PORTFOLIO_EVENTS_SQL)
            self._store._conn.execute(CREATE_PORTFOLIO_EVENTS_INDEX_SQL)

    def _seq_for(self, session_id: str) -> int:
        if session_id in self._next_seq:
            return self._next_seq[session_id]
        row = self._store._conn.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM portfolio_events WHERE session_id = ?",
            [session_id],
        ).fetchone()
        next_seq = (int(row[0]) + 1) if row else 0
        self._next_seq[session_id] = next_seq
        return next_seq

    def append(
        self,
        session_id: str,
        ts: int,
        kind: str,
        payload: Dict[str, Any],
    ) -> int:
        """Append one event. Returns the assigned seq number."""
        with self._lock:
            seq = self._seq_for(session_id)
            self._store._conn.execute(
                "INSERT INTO portfolio_events (session_id, seq, ts, kind, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                [session_id, seq, int(ts), kind,
                 json.dumps(payload, separators=(",", ":"))],
            )
            self._next_seq[session_id] = seq + 1
            return seq

    def append_many(
        self,
        session_id: str,
        events: List[Dict[str, Any]],
    ) -> List[int]:
        """Bulk append; returns assigned seqs in input order. Each
        event must carry `ts`, `kind`, and `payload`."""
        if not events:
            return []
        with self._lock:
            base = self._seq_for(session_id)
            rows = []
            seqs: List[int] = []
            for i, ev in enumerate(events):
                seq = base + i
                seqs.append(seq)
                rows.append((
                    session_id, seq, int(ev["ts"]), ev["kind"],
                    json.dumps(ev["payload"], separators=(",", ":")),
                ))
            self._store._conn.executemany(
                "INSERT INTO portfolio_events (session_id, seq, ts, kind, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            self._next_seq[session_id] = base + len(events)
            return seqs


class EventLogReader:
    """Read-side cursor for replay + audit views."""

    def __init__(self, store) -> None:
        self._store = store

    def read_all(self, session_id: str) -> List[PortfolioEvent]:
        rows = self._store._conn.execute(
            "SELECT session_id, seq, ts, kind, payload "
            "FROM portfolio_events WHERE session_id = ? ORDER BY seq",
            [session_id],
        ).fetchall()
        return [PortfolioEvent.from_row(r) for r in rows]

    def read_until(self, session_id: str, target_ts: int) -> List[PortfolioEvent]:
        """All events with `ts <= target_ts`. The replay primitive
        (next slice) walks this stream forward from the latest snapshot
        before `target_ts`."""
        rows = self._store._conn.execute(
            "SELECT session_id, seq, ts, kind, payload "
            "FROM portfolio_events WHERE session_id = ? AND ts <= ? "
            "ORDER BY seq",
            [session_id, int(target_ts)],
        ).fetchall()
        return [PortfolioEvent.from_row(r) for r in rows]

    def count(self, session_id: str) -> int:
        row = self._store._conn.execute(
            "SELECT COUNT(*) FROM portfolio_events WHERE session_id = ?",
            [session_id],
        ).fetchone()
        return int(row[0]) if row else 0

    def latest_seq(self, session_id: str) -> Optional[int]:
        row = self._store._conn.execute(
            "SELECT MAX(seq) FROM portfolio_events WHERE session_id = ?",
            [session_id],
        ).fetchone()
        if not row or row[0] is None:
            return None
        return int(row[0])
