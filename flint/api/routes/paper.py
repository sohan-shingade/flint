"""Paper trading API routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ...strategy import (
    MACrossoverStrategy, EMACrossoverStrategy, RSIStrategy,
    BollingerStrategy, MomentumStrategy,
    FundingHarvestStrategy, MeanReversionStrategy,
    BreakoutMomentumStrategy, GridTraderStrategy,
    DualTimeframeStrategy, VWAPReversionStrategy,
    MACDDivergenceStrategy, ATRBreakoutStrategy,
    MultiVenueFundingStrategy, RSIMACDComboStrategy,
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


# Strategy builders — mirrors flint/api/routes/backtest.py _build_strategy
_BUILDERS = {
    "ma_crossover": lambda p: MACrossoverStrategy(
        fast_period=int(p.get("fast_period", 10)),
        slow_period=int(p.get("slow_period", 30)),
    ),
    "ema_crossover": lambda p: EMACrossoverStrategy(
        fast_period=int(p.get("fast_period", 12)),
        slow_period=int(p.get("slow_period", 26)),
    ),
    "rsi": lambda p: RSIStrategy(
        period=int(p.get("period", 14)),
        oversold=float(p.get("oversold", 30)),
        overbought=float(p.get("overbought", 70)),
    ),
    "bollinger": lambda p: BollingerStrategy(
        period=int(p.get("period", 20)),
        num_std=float(p.get("num_std", 2.0)),
    ),
    "momentum": lambda p: MomentumStrategy(
        lookback=int(p.get("lookback", 24)),
        threshold_pct=float(p.get("threshold_pct", 5.0)),
    ),
    "funding_harvest": lambda p: FundingHarvestStrategy(
        entry_threshold=float(p.get("entry_threshold", 0.001)),
        exit_threshold=float(p.get("exit_threshold", 0.0002)),
        stop_loss_pct=float(p.get("stop_loss_pct", 0.05)),
        lookback=int(p.get("lookback", 8)),
    ),
    "mean_reversion": lambda p: MeanReversionStrategy(
        period=int(p.get("period", 20)),
        entry_z=float(p.get("entry_z", 2.0)),
        exit_z=float(p.get("exit_z", 0.5)),
        stop_loss_pct=float(p.get("stop_loss_pct", 0.05)),
    ),
    "breakout_momentum": lambda p: BreakoutMomentumStrategy(),
    "grid_trader": lambda p: GridTraderStrategy(),
    "dual_timeframe": lambda p: DualTimeframeStrategy(),
    "vwap_reversion": lambda p: VWAPReversionStrategy(
        period=int(p.get("period", 20)),
        entry_pct=float(p.get("entry_pct", 2.0)),
        exit_pct=float(p.get("exit_pct", 0.5)),
    ),
    "macd_divergence": lambda p: MACDDivergenceStrategy(
        fast=int(p.get("fast", 12)),
        slow=int(p.get("slow", 26)),
        signal=int(p.get("signal", 9)),
    ),
    "atr_breakout": lambda p: ATRBreakoutStrategy(
        period=int(p.get("period", 20)),
        atr_period=int(p.get("atr_period", 14)),
        multiplier=float(p.get("multiplier", 2.0)),
    ),
    "multi_venue_funding": lambda p: MultiVenueFundingStrategy(
        entry_threshold=float(p.get("entry_threshold", 0.0005)),
        exit_threshold=float(p.get("exit_threshold", 0.0001)),
        lookback=int(p.get("lookback", 12)),
    ),
    "rsi_macd_combo": lambda p: RSIMACDComboStrategy(
        rsi_period=int(p.get("rsi_period", 14)),
        macd_fast=int(p.get("macd_fast", 12)),
        macd_slow=int(p.get("macd_slow", 26)),
        macd_signal=int(p.get("macd_signal", 9)),
        rsi_oversold=float(p.get("rsi_oversold", 30)),
        rsi_overbought=float(p.get("rsi_overbought", 70)),
    ),
}


def _build_strategy(req: StartRequest):
    if req.code:
        return load_user_strategy(req.code, req.params)
    builder = _BUILDERS.get(req.strategy)
    if builder is None:
        return None
    return builder(req.params or {})


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
