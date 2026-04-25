"""BookState snapshot store — compaction for the event log.

D-6.4-replay slice 3 of 5. Long-running sessions accumulate millions
of events; replaying from `seq=0` every time would be O(N). Slice 3
caches periodic full-state snapshots so replay can fast-forward to
the latest snapshot before the target_ts and only fold the tail.

Schema:

    portfolio_snapshots(
        session_id  VARCHAR
        seq         BIGINT   -- the seq of the last event included
        ts          BIGINT   -- the ts of that event
        payload     VARCHAR  -- JSON-encoded BookState
        PRIMARY KEY (session_id, seq)
    )

Compaction strategy: callers decide when to write a snapshot. The
writer offers `should_compact(session_id, every_n_events)` so engine
hooks can flush every 10k events (the spec's default).

Replay protocol with snapshot:
  1. `latest_before(session_id, target_ts)` → (seq_at, snapshot)
  2. read events with `seq > seq_at AND ts <= target_ts`
  3. seed `fold(events, snapshot)` from the snapshot
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict
from typing import Optional, Tuple

from .replay import BookState, _ReplayPosition


CREATE_PORTFOLIO_SNAPSHOTS_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    session_id VARCHAR NOT NULL,
    seq        BIGINT  NOT NULL,
    ts         BIGINT  NOT NULL,
    payload    VARCHAR NOT NULL,
    PRIMARY KEY (session_id, seq)
);
"""

CREATE_PORTFOLIO_SNAPSHOTS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_session_ts
    ON portfolio_snapshots(session_id, ts);
"""


def _state_to_json(state: BookState) -> str:
    """Serialize BookState to JSON. Positions are nested dataclasses,
    so they need explicit conversion (asdict() handles that), and the
    `(venue, market)` tuple keys flatten to "venue|market" strings."""
    positions = {
        f"{venue}|{market}": asdict(pos)
        for (venue, market), pos in state.positions.items()
    }
    return json.dumps({
        "cash": state.cash,
        "initial_capital": state.initial_capital,
        "total_fees": state.total_fees,
        "total_tx_costs": state.total_tx_costs,
        "total_funding": state.total_funding,
        "total_borrow_paid": state.total_borrow_paid,
        "realized_pnl": state.realized_pnl,
        "fill_count": state.fill_count,
        "liquidation_count": state.liquidation_count,
        "order_submit_count": state.order_submit_count,
        "order_cancel_count": state.order_cancel_count,
        "positions": positions,
    }, separators=(",", ":"))


def _state_from_json(text: str) -> BookState:
    d = json.loads(text)
    state = BookState(
        cash=d["cash"],
        initial_capital=d["initial_capital"],
        total_fees=d.get("total_fees", 0.0),
        total_tx_costs=d.get("total_tx_costs", 0.0),
        total_funding=d.get("total_funding", 0.0),
        total_borrow_paid=d.get("total_borrow_paid", 0.0),
        realized_pnl=d.get("realized_pnl", 0.0),
        fill_count=d.get("fill_count", 0),
        liquidation_count=d.get("liquidation_count", 0),
        order_submit_count=d.get("order_submit_count", 0),
        order_cancel_count=d.get("order_cancel_count", 0),
    )
    for key, pos_d in d.get("positions", {}).items():
        venue, market = key.split("|", 1)
        state.positions[(venue, market)] = _ReplayPosition(
            market=pos_d["market"],
            venue=pos_d["venue"],
            side=pos_d["side"],
            size=pos_d["size"],
            entry_price=pos_d["entry_price"],
        )
    return state


class SnapshotStore:
    """Append-only snapshot writer + reader. Thread-safe via a lock
    shared across all DB-touching ops on this instance."""

    __slots__ = ("_store", "_lock")

    def __init__(self, store) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._lock:
            self._store._conn.execute(CREATE_PORTFOLIO_SNAPSHOTS_SQL)
            self._store._conn.execute(CREATE_PORTFOLIO_SNAPSHOTS_INDEX_SQL)

    def write(self, session_id: str, seq: int, ts: int, state: BookState) -> None:
        """Persist a snapshot. `seq` should be the last event seq
        included in the state; replay uses `seq > snapshot.seq` as
        the resume point."""
        payload = _state_to_json(state)
        with self._lock:
            # Upsert — same (session_id, seq) overwrites. Useful for
            # idempotent re-runs of the compactor.
            self._store._conn.execute(
                "INSERT OR REPLACE INTO portfolio_snapshots "
                "(session_id, seq, ts, payload) VALUES (?, ?, ?, ?)",
                [session_id, int(seq), int(ts), payload],
            )

    def latest_before(self, session_id: str, target_ts: int) -> Optional[Tuple[int, int, BookState]]:
        """Return `(seq, ts, state)` for the most recent snapshot with
        `ts <= target_ts`, or None if no qualifying snapshot exists."""
        with self._lock:
            row = self._store._conn.execute(
                "SELECT seq, ts, payload FROM portfolio_snapshots "
                "WHERE session_id = ? AND ts <= ? "
                "ORDER BY seq DESC LIMIT 1",
                [session_id, int(target_ts)],
            ).fetchone()
        if row is None:
            return None
        seq, ts, payload = row
        return int(seq), int(ts), _state_from_json(payload)

    def latest(self, session_id: str) -> Optional[Tuple[int, int, BookState]]:
        """Most recent snapshot regardless of ts."""
        with self._lock:
            row = self._store._conn.execute(
                "SELECT seq, ts, payload FROM portfolio_snapshots "
                "WHERE session_id = ? ORDER BY seq DESC LIMIT 1",
                [session_id],
            ).fetchone()
        if row is None:
            return None
        seq, ts, payload = row
        return int(seq), int(ts), _state_from_json(payload)

    def count(self, session_id: str) -> int:
        with self._lock:
            row = self._store._conn.execute(
                "SELECT COUNT(*) FROM portfolio_snapshots WHERE session_id = ?",
                [session_id],
            ).fetchone()
        return int(row[0]) if row else 0


def should_compact(events_since_last: int, every_n_events: int = 10_000) -> bool:
    """Compaction trigger predicate. The spec defaults to every 10k
    events; tests use lower numbers to exercise the path."""
    return events_since_last >= every_n_events
