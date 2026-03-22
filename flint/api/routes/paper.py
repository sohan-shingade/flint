"""Paper trading API routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ...strategy import (
    MACrossoverStrategy, EMACrossoverStrategy, RSIStrategy,
    BollingerStrategy, MomentumStrategy,
)
from ...strategy.loader import load_user_strategy

router = APIRouter()


class StartRequest(BaseModel):
    strategy: str = "ma_crossover"
    code: Optional[str] = None
    market: str = "SOL-PERP"
    resolution_s: int = 3600
    initial_capital: float = 10_000.0
    params: Optional[dict] = None


class StopRequest(BaseModel):
    session_id: str


BUILT_IN = {
    "ma_crossover": MACrossoverStrategy,
    "ema_crossover": EMACrossoverStrategy,
    "rsi": RSIStrategy,
    "bollinger": BollingerStrategy,
    "momentum": MomentumStrategy,
}


def _build_strategy(req: StartRequest):
    if req.code:
        return load_user_strategy(req.code, req.params)
    cls = BUILT_IN.get(req.strategy)
    if cls is None:
        return None
    if req.params:
        return cls(**req.params)
    return cls()


@router.post("/start")
async def start_paper(req: StartRequest, request: Request):
    engine = getattr(request.app.state, "paper_engine", None)
    if engine is None:
        return {"error": "Paper trading engine not available"}, 503

    strategy = _build_strategy(req)
    if strategy is None:
        return {"error": f"Unknown strategy: {req.strategy}"}, 404

    session_id = engine.start_session(
        strategy=strategy,
        market=req.market,
        resolution_s=req.resolution_s,
        initial_capital=req.initial_capital,
    )
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
        return {"error": "Session not found"}, 404
    return status


@router.get("/sessions")
async def list_sessions(request: Request):
    engine = getattr(request.app.state, "paper_engine", None)
    if engine is None:
        return {"sessions": []}
    return {"sessions": engine.list_sessions()}


@router.get("/trades/{session_id}")
async def paper_trades(session_id: str, request: Request):
    engine = getattr(request.app.state, "paper_engine", None)
    if engine is None:
        return {"trades": []}
    return {"trades": engine.get_trades(session_id)}
