"""Live trading data API endpoints."""
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
        rows = store.get_live_equity_history(session_id)
        return {"equity": [
            {"ts": r["ts"], "equity": r["equity"],
             "cash": r["cash"], "unrealized_pnl": r["unrealized_pnl"]}
            for r in rows
        ]}
    except Exception as e:
        return {"error": str(e)}


@router.get("/sessions")
def get_live_sessions(request: Request):
    """List live trading sessions."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        return {"error": "Store not available"}
    try:
        return {"sessions": store.list_live_sessions(limit=20)}
    except Exception as e:
        return {"error": str(e)}
