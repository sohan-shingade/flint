"""Backtest API routes."""
from __future__ import annotations

import uuid
import threading
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...backtest.engine import BacktestEngine
from ...analytics.tearsheet import generate_tearsheet
from ...providers.drift_s3 import DriftS3Provider
from ...store import FlintStore
from ...strategy import (
    MACrossoverStrategy,
    EMACrossoverStrategy,
    RSIStrategy,
    BollingerStrategy,
    MomentumStrategy,
)
from ...strategy.loader import load_user_strategy, StrategyLoadError

router = APIRouter()

# In-memory store for backtest results (production would use DB/Redis)
_results: Dict[str, dict] = {}
_status: Dict[str, str] = {}  # id -> "running" | "complete" | "failed"


class BacktestRequest(BaseModel):
    strategy: str = "ma_crossover"
    code: Optional[str] = None
    market: str = "SOL-PERP"
    resolution_s: int = 3600
    start_ts: int
    end_ts: int
    initial_capital: float = 10_000.0
    fee_rate: float = 0.0005
    params: Optional[Dict] = None


def _build_strategy(name: str, params: Dict, code: str = None):
    """Instantiate a strategy by name, user file, or inline code."""
    # Inline code from editor
    if code:
        return load_user_strategy(code, params or None)

    # User strategy from disk
    if name.startswith("user:"):
        from pathlib import Path
        strat_name = name[5:]
        path = Path(__file__).resolve().parents[3] / "strategies" / "user" / f"{strat_name}.py"
        if not path.exists():
            return None
        return load_user_strategy(path.read_text(encoding="utf-8"), params or None)

    # Built-in strategies
    if name == "ma_crossover":
        return MACrossoverStrategy(
            fast_period=int(params.get("fast_period", 10)),
            slow_period=int(params.get("slow_period", 30)),
        )
    elif name == "ema_crossover":
        return EMACrossoverStrategy(
            fast_period=int(params.get("fast_period", 12)),
            slow_period=int(params.get("slow_period", 26)),
        )
    elif name == "rsi":
        return RSIStrategy(
            period=int(params.get("period", 14)),
            oversold=float(params.get("oversold", 30)),
            overbought=float(params.get("overbought", 70)),
        )
    elif name == "bollinger":
        return BollingerStrategy(
            period=int(params.get("period", 20)),
            num_std=float(params.get("num_std", 2.0)),
        )
    elif name == "momentum":
        return MomentumStrategy(
            lookback=int(params.get("lookback", 24)),
            threshold_pct=float(params.get("threshold_pct", 5.0)),
        )
    else:
        return None


# Default params per strategy
_DEFAULTS = {
    "ma_crossover": {"fast_period": 10, "slow_period": 30},
    "ema_crossover": {"fast_period": 12, "slow_period": 26},
    "rsi": {"period": 14, "oversold": 30, "overbought": 70},
    "bollinger": {"period": 20, "num_std": 2.0},
    "momentum": {"lookback": 24, "threshold_pct": 5.0},
}


@router.post("/run")
def run_backtest(req: BacktestRequest):
    """Submit a backtest. Returns an ID to poll for results."""
    run_id = str(uuid.uuid4())[:8]
    _status[run_id] = "running"

    def _run():
        try:
            params = req.params or _DEFAULTS.get(req.strategy, {})
            strategy = _build_strategy(req.strategy, params, req.code)
            if strategy is None:
                _status[run_id] = "failed"
                _results[run_id] = {"error": f"Unknown strategy: {req.strategy}"}
                return

            # Try loading from DuckDB first, then fetch from Drift S3
            store = FlintStore("./data/flint.duckdb")
            candles = store.query_candles(req.market, req.resolution_s, req.start_ts, req.end_ts)

            if not candles:
                provider = DriftS3Provider()
                candles = provider.fetch_candles(req.market, req.resolution_s, req.start_ts, req.end_ts)
                provider.close()
                if candles:
                    store.upsert_candles(candles)

            store.close()

            if not candles:
                _status[run_id] = "failed"
                _results[run_id] = {"error": "No candle data found for the requested range"}
                return

            engine = BacktestEngine(strategy, req.initial_capital, req.fee_rate)
            result = engine.run(candles)

            tearsheet = generate_tearsheet(
                result, candles,
                strategy_name=strategy.name,
                initial_capital=req.initial_capital,
            )

            _results[run_id] = tearsheet.to_dict()
            _status[run_id] = "complete"
        except Exception as e:
            _status[run_id] = "failed"
            _results[run_id] = {"error": str(e)}

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return {"id": run_id, "status": "running"}


@router.get("/{run_id}/status")
def get_status(run_id: str):
    if run_id not in _status:
        raise HTTPException(404, "Backtest not found")
    return {"id": run_id, "status": _status[run_id]}


@router.get("/{run_id}/results")
def get_results(run_id: str):
    if run_id not in _status:
        raise HTTPException(404, "Backtest not found")
    if _status[run_id] == "running":
        return {"id": run_id, "status": "running", "results": None}
    return {"id": run_id, "status": _status[run_id], "results": _results.get(run_id)}


@router.get("/compare")
def compare_backtests(ids: str):
    """Compare multiple backtests. Pass comma-separated IDs."""
    run_ids = [i.strip() for i in ids.split(",")]
    results = []
    for rid in run_ids:
        if rid in _results and _status.get(rid) == "complete":
            r = _results[rid]
            results.append({
                "id": rid,
                "strategy": r.get("strategy_name", ""),
                "metrics": r.get("metrics", {}),
                "equity_curve": r.get("equity_curve", []),
            })
    return {"comparisons": results}
