"""Optimization API routes."""
from __future__ import annotations

import logging
import uuid
import time
import threading
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ...optimization.optimizer import StrategyOptimizer
from ...store import FlintStore
from ...strategy.loader import load_user_strategy

logger = logging.getLogger("flint.optimize")
router = APIRouter()

_MAX_ENTRIES = 100
_state_lock = threading.Lock()
_status: Dict[str, str] = {}
_progress: Dict[str, dict] = {}
_results: Dict[str, dict] = {}


def _set(run_id, status=None, progress=None, result=None):
    with _state_lock:
        # Evict old entries
        if len(_status) > _MAX_ENTRIES:
            for k in list(_status.keys())[:len(_status) - _MAX_ENTRIES]:
                _status.pop(k, None)
                _results.pop(k, None)
                _progress.pop(k, None)
        if status:
            _status[run_id] = status
        if progress:
            if run_id not in _progress:
                _progress[run_id] = {}
            _progress[run_id].update(progress)
        if result is not None:
            _results[run_id] = result


class OptimizeRequest(BaseModel):
    code: str
    market: str = "SOL-PERP"
    resolution_s: int = 3600
    start_ts: int
    end_ts: int
    initial_capital: float = Field(default=10_000.0, gt=0, le=1e12)
    fee_rate: float = Field(default=0.0005, ge=0, le=0.5)
    metric: str = "sharpe_ratio"
    trials: int = Field(default=50, ge=1, le=500)
    # Match backtest execution config for parity
    fill_model: str = "pipeline"
    slippage_bps: float = Field(default=10.0, ge=0, le=1000)
    latency_enabled: bool = True
    impact_coefficient: Optional[float] = Field(default=None, ge=0, le=1.0)
    margin_tracking: bool = False
    capital_allocation: Optional[Dict[str, float]] = None


@router.post("/run")
def run_optimization(req: OptimizeRequest, request: Request):
    """Submit an optimization job."""
    run_id = str(uuid.uuid4())[:8]
    started = time.time()
    _set(run_id, status="running", progress={"phase": "init", "pct": 0, "detail": "Starting...", "started_at": started})

    store: Optional[FlintStore] = getattr(request.app.state, "store", None)

    def _run():
        try:
            _set(run_id, progress={"phase": "strategy", "pct": 5, "detail": "Loading strategy..."})

            strategy = load_user_strategy(req.code)
            strategy_cls = type(strategy)
            params = strategy_cls.parameters()

            if not params:
                _set(run_id, status="failed", result={"error": f"{strategy_cls.__name__} has no parameters() defined"})
                return

            _set(run_id, progress={"phase": "data", "pct": 10, "detail": "Loading candles..."})

            candles = []
            if store:
                candles = store.query_candles(req.market, req.resolution_s, req.start_ts, req.end_ts)

            if not candles:
                from ...providers.drift_candles import DriftCandleProvider
                provider = DriftCandleProvider()
                try:
                    candles = provider.fetch_candles(req.market, req.resolution_s, req.start_ts, req.end_ts)
                finally:
                    provider.close()
                if candles and store:
                    try:
                        store.upsert_candles(candles)
                    except Exception:
                        pass

            if not candles:
                _set(run_id, status="failed", result={"error": "No candle data available"})
                return

            # Load market data (same as backtest route for parity)
            funding_rates = []
            orderbook_snapshots = []
            open_interest = []
            if store:
                try:
                    funding_rates = store.query_funding_rates(req.market, start_ts=req.start_ts, end_ts=req.end_ts)
                except Exception:
                    pass
                try:
                    orderbook_snapshots = store.query_orderbook_snapshots(req.market, start_ts=req.start_ts, end_ts=req.end_ts)
                except Exception:
                    pass
                try:
                    open_interest = store.query_open_interest(req.market, start_ts=req.start_ts, end_ts=req.end_ts)
                except Exception:
                    pass

            # Build fill model (same logic as backtest route)
            from ...execution.fill_models import (
                FillPipeline, SlippageFill, ClosePriceFill, NextBarOpenFill,
            )
            if req.fill_model == "slippage":
                fill_model = SlippageFill(slippage_bps=req.slippage_bps)
            elif req.fill_model == "close":
                fill_model = ClosePriceFill()
            elif req.fill_model == "next_bar_open":
                fill_model = NextBarOpenFill()
            else:
                from ...execution.venue_config import get_venue_config
                venue_name = "default"
                if req.capital_allocation:
                    venue_name = max(req.capital_allocation, key=req.capital_allocation.get)
                vcfg = get_venue_config(venue_name)
                fill_model = FillPipeline(
                    impact_coefficient=req.impact_coefficient or vcfg.impact_coefficient,
                    fallback_bps=req.slippage_bps,
                    base_latency_s=vcfg.base_latency_s,
                    latency_jitter_s=vcfg.latency_jitter_s,
                    latency_seed=42,  # fixed seed for deterministic optimization
                    latency_enabled=req.latency_enabled,
                )

            # Build optional execution features
            margin_eng = None
            cap_alloc = None
            if req.margin_tracking:
                from ...execution.margin import MarginEngine
                from ...execution.venue_config import VENUE_DEFAULTS
                margin_eng = MarginEngine(VENUE_DEFAULTS)
            if req.capital_allocation:
                from ...execution.capital import VenueAllocator
                cap_alloc = VenueAllocator(req.capital_allocation)

            _set(run_id, progress={
                "phase": "optimize", "pct": 15,
                "detail": f"Optimizing {strategy_cls.__name__} ({req.trials} trials, {len(candles)} candles)...",
                "candles": len(candles),
            })

            optimizer = StrategyOptimizer(
                strategy_cls, candles,
                metric=req.metric,
                n_trials=req.trials,
                initial_capital=req.initial_capital,
                fee_rate=req.fee_rate,
                fill_model=fill_model,
                funding_rates=funding_rates,
                orderbook_snapshots=orderbook_snapshots,
                open_interest=open_interest,
                margin_engine=margin_eng,
                capital_allocator=cap_alloc,
            )
            opt_result = optimizer.optimize()

            def _safe(v, decimals=4):
                """Sanitize float for JSON (replace inf/nan with None)."""
                import math
                if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
                    return None
                return round(v, decimals)

            trials_list = []
            for t in opt_result.trials[:20]:
                trials_list.append({
                    "params": t.params,
                    "metric_value": _safe(t.metric_value),
                    "total_pnl": _safe(t.total_pnl, 2),
                    "sharpe_ratio": _safe(t.sharpe_ratio),
                    "max_drawdown": _safe(t.max_drawdown),
                    "win_rate": _safe(t.win_rate),
                    "total_trades": t.total_trades,
                })

            best_val = _safe(opt_result.best_value)
            _set(run_id, status="complete", progress={
                "phase": "done", "pct": 100,
                "detail": f"Best {req.metric}: {best_val}",
            }, result={
                "best_params": opt_result.best_params,
                "best_value": best_val,
                "metric": opt_result.metric,
                "n_trials": opt_result.n_trials,
                "trials": trials_list,
                "strategy_name": strategy_cls.__name__,
                "market": req.market,
                "candles": len(candles),
            })

        except Exception as e:
            logger.exception("Optimization %s failed", run_id)
            _set(run_id, status="failed", result={"error": f"{type(e).__name__}: {e}"})

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return {"id": run_id, "status": "running"}


@router.get("/{run_id}/results")
def get_results(run_id: str):
    with _state_lock:
        if run_id not in _status:
            raise HTTPException(404, "Not found")
        status = _status[run_id]
        progress = dict(_progress.get(run_id, {}))
        result = _results.get(run_id)

    elapsed = time.time() - progress.get("started_at", time.time())
    return {
        "id": run_id, "status": status,
        "progress": {"phase": progress.get("phase", ""), "pct": progress.get("pct", 0),
                      "detail": progress.get("detail", ""), "elapsed_s": round(elapsed, 1)},
        "results": result,
    }
