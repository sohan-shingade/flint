"""Journal storage — save/load/list/delete backtest runs in DuckDB.

Thread-safe: all DB access goes through the store's lock.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from ..store import FlintStore

_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id         VARCHAR PRIMARY KEY,
    strategy_name  VARCHAR NOT NULL,
    market         VARCHAR NOT NULL,
    resolution_s   INTEGER NOT NULL,
    start_ts       BIGINT NOT NULL,
    end_ts         BIGINT NOT NULL,
    initial_capital DOUBLE NOT NULL,
    params_json    VARCHAR,
    created_at     BIGINT NOT NULL,
    total_pnl      DOUBLE,
    win_rate       DOUBLE,
    sharpe_ratio   DOUBLE,
    max_drawdown   DOUBLE,
    total_trades   INTEGER,
    total_fees     DOUBLE DEFAULT 0,
    funding_paid   DOUBLE DEFAULT 0
);
"""

_CREATE_TRADES = """
CREATE TABLE IF NOT EXISTS journal_trades (
    run_id      VARCHAR NOT NULL,
    trade_idx   INTEGER NOT NULL,
    side        VARCHAR,
    entry_price DOUBLE,
    exit_price  DOUBLE,
    size        DOUBLE,
    entry_ts    BIGINT,
    exit_ts     BIGINT,
    pnl         DOUBLE,
    PRIMARY KEY (run_id, trade_idx)
);
"""


class JournalStorage:
    """Save and query backtest runs. Thread-safe via store lock."""

    def __init__(self, store: FlintStore):
        self._store = store
        with store._lock:
            store._conn.execute(_CREATE_RUNS)
            store._conn.execute(_CREATE_TRADES)

    def save_run(self, run_id, strategy_name, market, resolution_s,
                 start_ts, end_ts, initial_capital, params=None, result=None):
        params_json = json.dumps(params) if params else None
        total_pnl = result.total_pnl if result else 0
        win_rate = result.win_rate if result else 0
        sharpe = result.sharpe_ratio if result else 0
        max_dd = result.max_drawdown if result else 0
        trades = result.total_trades if result else 0
        fees = getattr(result, "total_fees", 0)
        funding = getattr(result, "funding_paid", 0)

        with self._store._lock:
            self._store._conn.execute(
                "INSERT OR REPLACE INTO backtest_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [run_id, strategy_name, market, resolution_s, start_ts, end_ts,
                 initial_capital, params_json, int(time.time()),
                 total_pnl, win_rate, sharpe, max_dd, trades, fees, funding],
            )
            if result and result.positions:
                for idx, pos in enumerate(result.positions):
                    self._store._conn.execute(
                        "INSERT OR REPLACE INTO journal_trades VALUES (?,?,?,?,?,?,?,?,?)",
                        [run_id, idx,
                         pos.side.value if hasattr(pos.side, 'value') else str(pos.side),
                         pos.entry_price, pos.exit_price,
                         abs(pos.size), pos.entry_ts, pos.exit_ts, pos.pnl],
                    )

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._store._lock:
            rows = self._store._conn.execute(
                "SELECT * FROM backtest_runs ORDER BY created_at DESC LIMIT ?", [limit],
            ).fetchall()
            cols = [d[0] for d in self._store._conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._store._lock:
            row = self._store._conn.execute(
                "SELECT * FROM backtest_runs WHERE run_id = ?", [run_id]
            ).fetchone()
            if row is None:
                return None
            cols = [d[0] for d in self._store._conn.description]
            run = dict(zip(cols, row))
            trade_rows = self._store._conn.execute(
                "SELECT * FROM journal_trades WHERE run_id = ? ORDER BY trade_idx",
                [run_id],
            ).fetchall()
            trade_cols = [d[0] for d in self._store._conn.description]
        run["trades"] = [dict(zip(trade_cols, tr)) for tr in trade_rows]
        return run

    def delete_run(self, run_id: str) -> bool:
        with self._store._lock:
            self._store._conn.execute("DELETE FROM journal_trades WHERE run_id = ?", [run_id])
            self._store._conn.execute("DELETE FROM backtest_runs WHERE run_id = ?", [run_id])
        return True

    def compare_runs(self, run_ids: List[str]) -> List[Dict[str, Any]]:
        results = []
        with self._store._lock:
            for rid in run_ids:
                row = self._store._conn.execute(
                    "SELECT run_id, strategy_name, market, total_pnl, win_rate, "
                    "sharpe_ratio, max_drawdown, total_trades, total_fees "
                    "FROM backtest_runs WHERE run_id = ?", [rid],
                ).fetchone()
                if row:
                    cols = [d[0] for d in self._store._conn.description]
                    results.append(dict(zip(cols, row)))
        return results
