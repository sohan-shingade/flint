"""Replay API — query the portfolio event log + reconstruct state.

D-6.4-replay slice 5 backend. Surfaces three endpoints:

  GET /api/v1/replay/{session_id}/events?since=<seq>&limit=<n>
      Paginate events in seq order. Default limit 200, max 5000.

  GET /api/v1/replay/{session_id}/state?target_ts=<t>&initial_capital=<c>&use_snapshot=<bool>
      Replay-fold the log up to `target_ts` and return BookState as
      JSON. Honors snapshot fast-forward by default.

  GET /api/v1/replay/{session_id}/summary
      Lightweight metadata: latest seq, total event count, snapshot
      count. Cheap polling target for the UI page header.

Time-travel UI binding lands in a follow-on slice; this commit ships
the read surface so notebooks/scripts/MCP can drive replay today.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from ...portfolio.event_log import EventLogReader
from ...portfolio.replay import replay
from ...portfolio.snapshots import SnapshotStore

router = APIRouter()

_MAX_EVENTS_PER_PAGE = 5000


@router.get("/{session_id}/events")
def list_events(
    session_id: str,
    request: Request,
    since: Optional[int] = Query(None, description="Return events with seq > since"),
    limit: int = Query(200, ge=1, le=_MAX_EVENTS_PER_PAGE),
):
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(500, "Store not available")

    reader = EventLogReader(store)
    if since is not None:
        # `read_after_seq` requires a target_ts upper bound; use a
        # far-future ts so the only filter that matters is `seq > since`.
        events = reader.read_after_seq(session_id, after_seq=int(since), target_ts=2**62)
    else:
        events = reader.read_all(session_id)

    page = events[:limit]
    return {
        "session_id": session_id,
        "since": since,
        "limit": limit,
        "count": len(page),
        "has_more": len(events) > limit,
        "events": [
            {"seq": e.seq, "ts": e.ts, "kind": e.kind, "payload": e.payload}
            for e in page
        ],
    }


@router.get("/{session_id}/state")
def get_state(
    session_id: str,
    request: Request,
    target_ts: int = Query(..., description="Replay up to this epoch second"),
    initial_capital: float = Query(10_000.0),
    use_snapshot: bool = Query(True, description="Fast-forward via snapshot when available"),
):
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(500, "Store not available")

    state = replay(
        store, session_id,
        target_ts=int(target_ts),
        initial_capital=float(initial_capital),
        use_snapshot=use_snapshot,
    )

    # Serialize positions in a JSON-friendly shape ("venue|market" → dict).
    positions = {
        f"{venue}|{market}": {
            "market": pos.market, "venue": pos.venue,
            "side": pos.side, "size": pos.size,
            "entry_price": pos.entry_price,
        }
        for (venue, market), pos in state.positions.items()
    }

    return {
        "session_id": session_id,
        "target_ts": int(target_ts),
        "cash": state.cash,
        "initial_capital": state.initial_capital,
        "realized_pnl": state.realized_pnl,
        "total_fees": state.total_fees,
        "total_tx_costs": state.total_tx_costs,
        "total_funding": state.total_funding,
        "total_borrow_paid": state.total_borrow_paid,
        "fill_count": state.fill_count,
        "liquidation_count": state.liquidation_count,
        "order_submit_count": state.order_submit_count,
        "order_cancel_count": state.order_cancel_count,
        "positions": positions,
    }


@router.get("/{session_id}/summary")
def get_summary(session_id: str, request: Request):
    """Cheap polling target — counts only, no full payload reads."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(500, "Store not available")

    reader = EventLogReader(store)
    snaps = SnapshotStore(store)
    return {
        "session_id": session_id,
        "event_count": reader.count(session_id),
        "latest_seq": reader.latest_seq(session_id),
        "snapshot_count": snaps.count(session_id),
    }
