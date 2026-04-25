"""Persistence layer for paper trading sessions.

Stores session metadata, equity history, trades, and positions
in DuckDB so sessions survive server restarts.

D-2.2-internal — this module used to touch self._store._conn and
self._store._lock directly; migrated to FlintStore._sql_* wrappers so
the encapsulation rule applies uniformly.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from ..store import FlintStore

logger = logging.getLogger("flint.paper")


class PaperSessionStore:
    """CRUD operations for paper trading persistence."""

    def __init__(self, store: FlintStore):
        self._store = store

    def save_session(self, *, session_id: str, strategy_name: str, strategy_code: str,
                     strategy_params: dict, market: str, initial_capital: float,
                     replay_start_ts: int, started_at: int, status: str,
                     risk_config: dict, live_start_ts: int = 0) -> None:
        self._store._sql_exec(
            "INSERT OR REPLACE INTO paper_sessions "
            "(session_id, strategy_name, strategy_code, strategy_params, market, "
            " initial_capital, replay_start_ts, live_start_ts, started_at, status, risk_config) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [session_id, strategy_name, strategy_code, json.dumps(strategy_params),
             market, initial_capital, replay_start_ts, live_start_ts, started_at,
             status, json.dumps(risk_config)],
        )

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self._store._sql_read_one(
            "SELECT session_id, strategy_name, strategy_code, strategy_params, market, "
            "initial_capital, replay_start_ts, live_start_ts, started_at, stopped_at, "
            "status, stop_reason, risk_config FROM paper_sessions WHERE session_id = ?",
            [session_id],
        )
        if not row:
            return None
        return {
            "session_id": row[0], "strategy_name": row[1], "strategy_code": row[2],
            "strategy_params": json.loads(row[3]), "market": row[4],
            "initial_capital": row[5], "replay_start_ts": row[6], "live_start_ts": row[7],
            "started_at": row[8], "stopped_at": row[9], "status": row[10],
            "stop_reason": row[11], "risk_config": json.loads(row[12]),
        }

    def update_status(self, session_id: str, status: str, *,
                      live_start_ts: int = 0, stopped_at: int = 0,
                      stop_reason: str = "") -> None:
        sets = ["status = ?"]
        vals: list = [status]
        if live_start_ts:
            sets.append("live_start_ts = ?")
            vals.append(live_start_ts)
        if stopped_at:
            sets.append("stopped_at = ?")
            vals.append(stopped_at)
        if stop_reason:
            sets.append("stop_reason = ?")
            vals.append(stop_reason)
        vals.append(session_id)
        self._store._sql_exec(
            f"UPDATE paper_sessions SET {', '.join(sets)} WHERE session_id = ?", vals,
        )

    def update_risk_config(self, session_id: str, risk_config: dict) -> None:
        self._store._sql_exec(
            "UPDATE paper_sessions SET risk_config = ? WHERE session_id = ?",
            [json.dumps(risk_config), session_id],
        )

    def save_equity_snapshots(self, session_id: str, snapshots: List[dict]) -> None:
        if not snapshots:
            return
        stmts = [("BEGIN TRANSACTION", None)]
        for s in snapshots:
            stmts.append((
                "INSERT OR REPLACE INTO paper_equity_history "
                "(session_id, ts, equity, cash, unrealized_pnl, is_replay) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [session_id, s["ts"], s["equity"], s["cash"],
                 s.get("unrealized_pnl", 0), s.get("is_replay", False)],
            ))
        stmts.append(("COMMIT", None))
        self._store._sql_exec_many(stmts)

    def get_equity_history(self, session_id: str) -> List[dict]:
        rows = self._store._sql_read_all(
            "SELECT ts, equity, cash, unrealized_pnl, is_replay "
            "FROM paper_equity_history WHERE session_id = ? ORDER BY ts",
            [session_id],
        )
        return [{"ts": r[0], "equity": r[1], "cash": r[2],
                 "unrealized_pnl": r[3], "is_replay": r[4]} for r in rows]

    def save_trades(self, session_id: str, trades: List[dict]) -> None:
        if not trades:
            return
        stmts = [("BEGIN TRANSACTION", None)]
        for t in trades:
            stmts.append((
                "INSERT OR REPLACE INTO paper_trades "
                "(session_id, trade_id, market, side, size, entry_price, exit_price, "
                " entry_ts, exit_ts, pnl, fees, is_replay) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [session_id, t["trade_id"], t["market"], t["side"], t["size"],
                 t["entry_price"], t["exit_price"], t["entry_ts"], t["exit_ts"],
                 t["pnl"], t.get("fees", 0), t.get("is_replay", False)],
            ))
        stmts.append(("COMMIT", None))
        self._store._sql_exec_many(stmts)

    def get_trades(self, session_id: str) -> List[dict]:
        rows = self._store._sql_read_all(
            "SELECT trade_id, market, side, size, entry_price, exit_price, "
            "entry_ts, exit_ts, pnl, fees, is_replay "
            "FROM paper_trades WHERE session_id = ? ORDER BY exit_ts",
            [session_id],
        )
        return [{"trade_id": r[0], "market": r[1], "side": r[2], "size": r[3],
                 "entry_price": r[4], "exit_price": r[5], "entry_ts": r[6],
                 "exit_ts": r[7], "pnl": r[8], "fees": r[9], "is_replay": r[10]}
                for r in rows]

    def save_positions(self, session_id: str, positions: List[dict]) -> None:
        stmts = [(
            "DELETE FROM paper_positions WHERE session_id = ?", [session_id],
        )]
        for p in positions:
            stmts.append((
                "INSERT INTO paper_positions "
                "(session_id, market, side, size, entry_price, entry_ts, unrealized_pnl) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [session_id, p["market"], p["side"], p["size"],
                 p["entry_price"], p["entry_ts"], p.get("unrealized_pnl", 0)],
            ))
        self._store._sql_exec_many(stmts)

    def load_positions(self, session_id: str) -> List[dict]:
        rows = self._store._sql_read_all(
            "SELECT market, side, size, entry_price, entry_ts, unrealized_pnl "
            "FROM paper_positions WHERE session_id = ?",
            [session_id],
        )
        return [{"market": r[0], "side": r[1], "size": r[2],
                 "entry_price": r[3], "entry_ts": r[4], "unrealized_pnl": r[5]}
                for r in rows]

    def list_active_sessions(self) -> List[dict]:
        rows = self._store._sql_read_all(
            "SELECT session_id, strategy_name, market, initial_capital, status, started_at "
            "FROM paper_sessions WHERE status IN ('live', 'replaying') "
            "ORDER BY started_at DESC",
        )
        return [{"session_id": r[0], "strategy_name": r[1], "market": r[2],
                 "initial_capital": r[3], "status": r[4], "started_at": r[5]}
                for r in rows]

    def save_funding_payment(self, session_id: str, ts: int, market: str,
                             rate: float, payment: float, position_size: float,
                             mark_price: float) -> None:
        self._store._sql_exec(
            "INSERT INTO paper_funding_payments "
            "(session_id, ts, market, rate, payment, position_size, mark_price) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [session_id, ts, market, rate, payment, position_size, mark_price],
        )

    def get_funding_payments(self, session_id: str) -> list:
        rows = self._store._sql_read_all(
            "SELECT ts, market, rate, payment, position_size, mark_price "
            "FROM paper_funding_payments WHERE session_id = ? ORDER BY ts",
            [session_id],
        )
        return [{"ts": r[0], "market": r[1], "rate": r[2], "payment": r[3],
                 "position_size": r[4], "mark_price": r[5]} for r in rows]

    def clear_session_data(self, session_id: str) -> None:
        """Wipe all live data for a session (equity, trades, positions, funding)."""
        self._store._sql_exec_many([
            ("BEGIN TRANSACTION", None),
            ("DELETE FROM paper_equity_history WHERE session_id = ?", [session_id]),
            ("DELETE FROM paper_trades WHERE session_id = ?", [session_id]),
            ("DELETE FROM paper_positions WHERE session_id = ?", [session_id]),
            ("DELETE FROM paper_funding_payments WHERE session_id = ?", [session_id]),
            ("COMMIT", None),
        ])

    def list_all_sessions(self) -> List[dict]:
        rows = self._store._sql_read_all(
            "SELECT session_id, strategy_name, market, initial_capital, status, "
            "started_at, stopped_at, stop_reason "
            "FROM paper_sessions ORDER BY started_at DESC",
        )
        return [{"session_id": r[0], "strategy_name": r[1], "market": r[2],
                 "initial_capital": r[3], "status": r[4], "started_at": r[5],
                 "stopped_at": r[6], "stop_reason": r[7]}
                for r in rows]
