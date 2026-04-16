"""Paper trading API routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
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
    venue: str = "drift"


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
        is_active = s.get("status") in ("running", "live")
        if is_active:
            total_equity += equity
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
