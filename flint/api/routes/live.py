"""Live trading data API endpoints."""
from typing import Optional
from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/v1/live", tags=["live"])


@router.get("/fills")
def get_live_fills(request: Request, session_id: str = "", venue: str = "", market: str = ""):
    """Query live fills with optional filters."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        return {"error": "Store not available"}
    if session_id:
        fills = store.get_live_fills(session_id, market=market or None)
    elif venue and market:
        fills = store.query_live_fills_by_venue(venue, market)
    else:
        fills = []
    return {"fills": fills}


@router.get("/equity")
def get_live_equity(request: Request, session_id: str = ""):
    """Query equity history for a session."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        return {"error": "Store not available"}
    if not session_id:
        return {"error": "session_id required"}
    try:
        with store._lock:
            rows = store._conn.execute(
                "SELECT ts, equity, cash, unrealized_pnl FROM live_equity_history "
                "WHERE session_id = ? ORDER BY ts ASC", [session_id]
            ).fetchall()
        return {"equity": [{"ts": r[0], "equity": r[1], "cash": r[2], "unrealized_pnl": r[3]} for r in rows]}
    except Exception as e:
        return {"error": str(e)}


@router.get("/sessions")
def get_live_sessions(request: Request):
    """List live trading sessions."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        return {"error": "Store not available"}
    try:
        with store._lock:
            rows = store._conn.execute(
                "SELECT session_id, strategy, market, venue, status, started_at, stopped_at "
                "FROM live_sessions ORDER BY started_at DESC LIMIT 20"
            ).fetchall()
        return {"sessions": [
            {"session_id": r[0], "strategy": r[1], "market": r[2], "venue": r[3],
             "status": r[4], "started_at": r[5], "stopped_at": r[6]}
            for r in rows
        ]}
    except Exception as e:
        return {"error": str(e)}
