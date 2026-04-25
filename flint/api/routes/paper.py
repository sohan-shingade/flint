"""Paper trading API routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from pydantic import BaseModel

from ...services.strategies import build_strategy

router = APIRouter()


class StartRequest(BaseModel):
    strategy: str = "ma_crossover"
    code: Optional[str] = None
    market: str = "SOL-PERP"
    resolution_s: int = 3600
    initial_capital: float = 10_000.0
    params: Optional[dict] = None
    venue: str = "drift"
    risk_config: Optional[dict] = None


class StopRequest(BaseModel):
    session_id: str


def _build_strategy(req: StartRequest):
    return build_strategy(req.strategy, req.params, req.code)


@router.post("/start")
async def start_paper(req: StartRequest, request: Request):
    engine = getattr(request.app.state, "paper_engine", None)
    if engine is None:
        raise HTTPException(503, "Paper trading engine not available")

    strategy = _build_strategy(req)
    if strategy is None:
        raise HTTPException(404, f"Unknown strategy: {req.strategy}")

    session_id = engine.start_session(
        strategy=strategy,
        market=req.market,
        resolution_s=req.resolution_s,
        initial_capital=req.initial_capital,
        venue=req.venue,
        strategy_code=req.code or "",
        strategy_params=req.params or {},
        risk_config=req.risk_config,
    )

    # Auto-register strategy and advance lifecycle to "paper"
    store = getattr(request.app.state, "store", None)
    if store:
        try:
            from .backtest import _auto_register_strategy
            _auto_register_strategy(store, strategy, req.code, req.params, "paper")
        except Exception:
            pass

    return {"session_id": session_id, "status": "running"}


@router.post("/stop")
async def stop_paper(req: StopRequest, request: Request):
    engine = getattr(request.app.state, "paper_engine", None)
    if engine is None:
        return {"error": "Paper trading engine not available"}
    ok = engine.stop_session(req.session_id)
    return {"session_id": req.session_id, "stopped": ok}


@router.post("/kill")
async def kill_paper(req: StopRequest, request: Request):
    engine = getattr(request.app.state, "paper_engine", None)
    if engine is None:
        return {"error": "Paper trading engine not available"}
    ok = engine.kill_session(req.session_id)
    return {"session_id": req.session_id, "killed": ok}


@router.get("/status/{session_id}")
async def paper_status(session_id: str, request: Request):
    engine = getattr(request.app.state, "paper_engine", None)
    if engine is None:
        return {"error": "Paper trading engine not available"}
    status = engine.get_status(session_id)
    if status is None:
        raise HTTPException(404, "Session not found")

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

        # Include equity curve from session's in-memory history
        status["equity_curve"] = session.equity_history[-200:]

    return status


@router.get("/sessions")
async def list_sessions(request: Request):
    engine = getattr(request.app.state, "paper_engine", None)
    if engine is None:
        return {"sessions": []}
    return {"sessions": engine.list_sessions()}


@router.get("/portfolio")
def get_portfolio(request: Request):
    """Aggregate portfolio view across all paper sessions."""
    engine = getattr(request.app.state, "paper_engine", None)
    if engine is None:
        return {"total_equity": 0, "total_pnl": 0, "per_strategy": [],
                "active_sessions": 0, "total_sessions": 0}

    sessions = engine.list_sessions()
    total_equity = 0.0
    total_initial = 0.0
    per_strategy = []

    for s in sessions:
        equity = s.get("equity", 0)
        pnl = s.get("pnl", 0)
        initial = s.get("initial_capital", 0)
        is_active = s.get("status") in ("running", "live")
        if is_active:
            total_equity += equity
            total_initial += initial
        per_strategy.append({
            "session_id": s["session_id"],
            "strategy_name": s.get("strategy", ""),
            "market": s.get("market", ""),
            "venue": s.get("venue", "drift"),
            "equity": round(equity, 2),
            "pnl": round(pnl, 2),
            "status": s.get("status", ""),
        })

    active = sum(1 for s in sessions if s.get("status") in ("running", "live"))

    return {
        "total_equity": round(total_equity, 2),
        "total_pnl": round(total_equity - total_initial, 2),
        "total_initial_capital": round(total_initial, 2),
        "active_sessions": active,
        "total_sessions": len(sessions),
        "per_strategy": per_strategy,
    }


@router.post("/deploy")
def deploy_strategy(body: dict, request: Request):
    """Deploy a strategy from BacktestLab with replay-forward execution."""
    engine = getattr(request.app.state, "paper_engine", None)
    if engine is None:
        raise HTTPException(500, "Paper trading engine not available")

    strategy_code = body.get("strategy_code", "")
    strategy_params = body.get("strategy_params", {})
    market = body.get("market", "SOL-PERP")
    initial_capital = body.get("initial_capital", 10000.0)
    replay_start_ts = body.get("replay_start_ts", 0)
    risk_config = body.get("risk_config", {})
    resolution_s = body.get("resolution_s", 3600)
    capital_allocation = body.get("capital_allocation")
    venue = body.get("venue", "drift")

    from ...strategy.loader import load_user_strategy, StrategyLoadError
    try:
        strategy = load_user_strategy(strategy_code, strategy_params or None)
    except StrategyLoadError as e:
        raise HTTPException(400, f"Strategy load error: {e}")

    try:
        session_id = engine.deploy_session(
            strategy=strategy,
            strategy_code=strategy_code,
            strategy_params=strategy_params,
            market=market,
            resolution_s=resolution_s,
            initial_capital=initial_capital,
            replay_start_ts=replay_start_ts,
            risk_config=risk_config,
            capital_allocation=capital_allocation,
            venue=venue,
        )
    except Exception as e:
        raise HTTPException(500, f"Deploy failed: {e}")

    return {"session_id": session_id, "status": "deployed"}


@router.post("/redeploy")
def redeploy_session(body: dict, request: Request):
    """Redeploy a session from a new start date."""
    engine = getattr(request.app.state, "paper_engine", None)
    if engine is None:
        raise HTTPException(500, "Paper trading engine not available")

    session_id = body.get("session_id")
    replay_start_ts = body.get("replay_start_ts", 0)

    if not session_id:
        raise HTTPException(400, "session_id required")
    if not replay_start_ts:
        raise HTTPException(400, "replay_start_ts required")

    new_id = engine.redeploy_session(session_id, replay_start_ts)
    if new_id is None:
        raise HTTPException(404, f"Session {session_id} not found")

    return {"old_session_id": session_id, "new_session_id": new_id, "status": "redeployed"}


@router.post("/redeploy-all")
def redeploy_all_sessions(body: dict, request: Request):
    """Redeploy all active sessions from a new start date."""
    engine = getattr(request.app.state, "paper_engine", None)
    if engine is None:
        raise HTTPException(500, "Paper trading engine not available")

    replay_start_ts = body.get("replay_start_ts", 0)
    if not replay_start_ts:
        raise HTTPException(400, "replay_start_ts required")

    results = []
    for sid in list(engine.sessions.keys()):
        new_id = engine.redeploy_session(sid, replay_start_ts)
        results.append({"old_session_id": sid, "new_session_id": new_id})

    return {"redeployed": results}


@router.get("/trades/{session_id}")
async def paper_trades(session_id: str, request: Request):
    engine = getattr(request.app.state, "paper_engine", None)
    if engine is None:
        return {"trades": []}
    return {"trades": engine.get_trades(session_id)}


@router.get("/{session_id}/equity-history")
def get_equity_history(session_id: str, request: Request):
    """Get full equity curve for a session from DuckDB."""
    store = request.app.state.store if hasattr(request.app.state, "store") else None
    if store is None:
        return {"equity_curve": []}
    from ...paper.session_store import PaperSessionStore
    ss = PaperSessionStore(store)
    history = ss.get_equity_history(session_id)
    return {"equity_curve": history}


@router.post("/{session_id}/risk")
def update_risk_config(session_id: str, body: dict, request: Request):
    """Update risk configuration for a running session."""
    engine = getattr(request.app.state, "paper_engine", None)
    if engine is None:
        raise HTTPException(500, "Paper trading engine not available")

    session = engine.sessions.get(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")

    from ...paper.risk_guard import RiskConfig, RiskGuard
    rc = RiskConfig(
        max_drawdown_pct=body.get("max_drawdown_pct", 0.15),
        daily_loss_limit=body.get("daily_loss_limit", 500),
        max_position_pct=body.get("max_position_pct", 0.95),
        liquidation_enabled=body.get("liquidation_enabled", True),
    )
    session.risk_guard = RiskGuard(rc)
    session.risk_config = body

    ss = getattr(session, "session_store", None)
    if ss:
        ss.update_risk_config(session_id, body)

    return {"session_id": session_id, "risk_config": body}


# ─── D-1.4-api — Reconciliation endpoint ─────────────────────────────────────

@router.get("/{session_id}/reconciliation")
def get_reconciliation(
    session_id: str,
    request: Request,
    ts_window_s: int = 60,
):
    """Reconcile engine-recorded fills vs venue-reported fills.

    Phase 1 T1.4 shipped scripts/reconcile_fills.py. This route wraps the
    same matching logic around the engine fills for a given paper session
    so the UI / MCP can surface divergence without shelling out to the CLI.

    Today the endpoint returns the engine-only view: orphan count = 0 when
    the caller has not supplied a venue fill log (that's a POST variant for
    Phase 4 UI work). Until then the response still catches the hard
    question — "did the engine record fills at all?" — and reports stats
    on matched engine fills + surface for UI to compare live venue state.
    """
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(500, "Store not available")

    # Engine fills via encapsulated store method (Phase 2 T2.2).
    engine_fills = store.get_live_fills(session_id)
    if not engine_fills:
        return {
            "session_id": session_id,
            "engine_count": 0,
            "venue_count": 0,
            "match_count": 0,
            "engine_only_count": 0,
            "venue_only_count": 0,
            "note": "no engine fills recorded for this session",
        }

    # Side of the report the caller already owns.
    by_side: dict = {"long": 0, "short": 0}
    markets: set = set()
    for f in engine_fills:
        by_side[f.get("side", "long")] = by_side.get(f.get("side", "long"), 0) + 1
        markets.add(f.get("market", ""))

    return {
        "session_id": session_id,
        "engine_count": len(engine_fills),
        "venue_count": 0,
        "match_count": 0,
        "engine_only_count": len(engine_fills),
        "venue_only_count": 0,
        "markets": sorted(markets),
        "engine_fills_by_side": by_side,
        "note": (
            "POST /{session_id}/reconciliation with a venue fill log to get "
            "price/ts deltas; see scripts/reconcile_fills.py CLI for today."
        ),
        "ts_window_s": ts_window_s,
    }


# ─── D-1.4-ui — POST variant: upload venue CSV → match against engine fills ──

# Cap upload size to keep a hostile request from OOMing the server.
_MAX_RECONCILE_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post("/{session_id}/reconciliation")
async def post_reconciliation(
    session_id: str,
    request: Request,
    file: UploadFile = File(...),
    ts_window_s: int = 60,
):
    """Match engine fills against a user-uploaded venue fill CSV.

    Wraps `scripts.reconcile_fills.reconcile()` over the engine fills for
    `session_id` plus the freshly uploaded CSV. Returns the same dict
    shape as the CLI: counts + p50/p95/p99 deltas. UI panel reads it
    directly.

    Schema for the upload (see docs/reference/custom-data-schema.md#fills):
        ts_epoch_s,market,venue,side,size,price,fee,order_id

    Errors:
      - 400: missing columns / malformed row / file > 10 MB
      - 500: store unavailable
    """
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(500, "Store not available")

    raw = await file.read()
    if len(raw) > _MAX_RECONCILE_UPLOAD_BYTES:
        raise HTTPException(
            400,
            f"Upload exceeds {_MAX_RECONCILE_UPLOAD_BYTES // (1024*1024)} MB cap"
        )

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "Upload must be UTF-8 encoded CSV")

    # Lazy-import the reconciler to avoid pulling argparse/etc. into the
    # API process at startup.
    from scripts.reconcile_fills import (
        CSVSchemaError, Fill, parse_venue_fills_csv_text, reconcile,
    )

    try:
        venue_fills = parse_venue_fills_csv_text(text)
    except CSVSchemaError as e:
        raise HTTPException(400, str(e))

    raw_engine = store.get_live_fills(session_id)
    if not raw_engine:
        return {
            "session_id": session_id,
            "engine_count": 0,
            "venue_count": len(venue_fills),
            "match_count": 0,
            "engine_only_count": 0,
            "venue_only_count": len(venue_fills),
            "note": "no engine fills recorded for this session",
        }

    engine_fills = [
        Fill(
            ts=r["ts"], market=r["market"],
            venue=r.get("venue", "default"),
            side=r["side"], size=r["size"], price=r["price"],
            fee=r.get("fee", 0.0),
            order_id=str(r.get("order_id", "")),
            source="engine",
        )
        for r in raw_engine
    ]

    result = reconcile(engine_fills, venue_fills, ts_window_s=ts_window_s)
    result["session_id"] = session_id
    result["ts_window_s"] = ts_window_s
    return result
