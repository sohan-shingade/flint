"""Paper service — wrappers around `PaperTradingEngine`.

The engine itself owns the asyncio task lifecycle for live sessions, so
"in-process" only really applies to **read** operations (status, list)
and **start/stop** when the caller already has the engine instance.
MCP's `start_paper_trading` still goes over HTTP because the daemon
holding the loop is the running `flint serve` process — these wrappers
let that route stay simple.

Read-only consumers (CLI scripts, notebook checks of session state) can
use `list_sessions_from_store(store)` to query the persisted view
without needing the engine at all.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..paper.engine import PaperTradingEngine
from ..paper.session_store import PaperSessionStore
from ..store import FlintStore


def list_sessions(engine: PaperTradingEngine) -> List[Dict[str, Any]]:
    return engine.list_sessions()


def get_session_status(engine: PaperTradingEngine, session_id: str) -> Optional[Dict[str, Any]]:
    status = engine.get_status(session_id)
    if status is None:
        return None

    session = engine.sessions.get(session_id)
    if session and status:
        mr = session.broker.margin_ratio
        status["margin"] = {
            "leverage": round(session.broker.leverage, 2),
            "margin_used": round(session.broker.margin_used, 2),
            "free_margin": round(session.broker.free_margin, 2),
            "margin_ratio": round(mr, 4) if mr != float("inf") else 0,
            "liquidation_prices": {
                m: round(session.broker.get_liquidation_price(m), 2)
                for m in session.broker.positions
            },
        }
        status["funding_total"] = round(session.broker.total_funding, 4)
        status["equity_curve"] = session.equity_history[-200:]

    return status


def stop_session(engine: PaperTradingEngine, session_id: str) -> bool:
    return engine.stop_session(session_id)


def kill_session(engine: PaperTradingEngine, session_id: str) -> bool:
    return engine.kill_session(session_id)


def list_sessions_from_store(store: FlintStore) -> List[Dict[str, Any]]:
    """Read persisted session metadata without needing a running engine.

    Useful for CLI checks and MCP's "did the server forget about my
    session?" queries while the engine is offline.
    """
    ss = PaperSessionStore(store)
    return ss.list_active_sessions()


def get_equity_history_from_store(store: FlintStore, session_id: str) -> List[Dict[str, Any]]:
    ss = PaperSessionStore(store)
    return ss.get_equity_history(session_id)
