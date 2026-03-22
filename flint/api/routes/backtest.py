"""Backtest API routes."""
from __future__ import annotations

import logging
import uuid
import time
import threading
from datetime import datetime as dt, timezone as tz
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ...backtest.engine import BacktestEngine
from ...analytics.tearsheet import generate_tearsheet
from ...analytics.monte_carlo import run_monte_carlo
from ...data.quality import check_candle_quality
from ...providers.drift_candles import DriftCandleProvider
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

logger = logging.getLogger("flint.backtest")

router = APIRouter()

# In-memory state — protected by _state_lock, capped at 200 entries
_MAX_ENTRIES = 200
_state_lock = threading.Lock()
_results: Dict[str, dict] = {}
_status: Dict[str, str] = {}
_progress: Dict[str, dict] = {}


def _evict_old():
    """Remove oldest entries if over cap. Called inside _state_lock."""
    if len(_status) > _MAX_ENTRIES:
        oldest = list(_status.keys())[:len(_status) - _MAX_ENTRIES]
        for k in oldest:
            _status.pop(k, None)
            _results.pop(k, None)
            _progress.pop(k, None)


def _set_status(run_id: str, status: str):
    with _state_lock:
        _status[run_id] = status
        _evict_old()


def _set_progress(run_id: str, **kwargs):
    with _state_lock:
        if run_id not in _progress:
            _progress[run_id] = {}
        _progress[run_id].update(kwargs)


def _set_result(run_id: str, result: dict):
    with _state_lock:
        _results[run_id] = result


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
    if code:
        return load_user_strategy(code, params or None)

    if name.startswith("user:"):
        from pathlib import Path
        strat_name = name[5:]
        path = Path(__file__).resolve().parents[3] / "strategies" / "user" / f"{strat_name}.py"
        if not path.exists():
            return None
        return load_user_strategy(path.read_text(encoding="utf-8"), params or None)

    builders = {
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
    }
    builder = builders.get(name)
    return builder(params) if builder else None


_DEFAULTS = {
    "ma_crossover": {"fast_period": 10, "slow_period": 30},
    "ema_crossover": {"fast_period": 12, "slow_period": 26},
    "rsi": {"period": 14, "oversold": 30, "overbought": 70},
    "bollinger": {"period": 20, "num_std": 2.0},
    "momentum": {"lookback": 24, "threshold_pct": 5.0},
}


@router.post("/run")
def run_backtest(req: BacktestRequest, request: Request):
    """Submit a backtest. Returns an ID to poll for results."""

    # --- Validate input ---
    if req.code is not None and not req.code.strip():
        raise HTTPException(400, "Strategy code cannot be empty")

    if req.start_ts >= req.end_ts:
        raise HTTPException(400, "Start date must be before end date")

    duration_s = req.end_ts - req.start_ts
    if duration_s < req.resolution_s * 10:
        min_hours = 10 * req.resolution_s // 3600
        raise HTTPException(400, f"Date range too short — need at least 10 candles ({min_hours}h for {req.resolution_s}s resolution)")

    # --- Check data availability (fast, sync, before threading) ---
    store: Optional[FlintStore] = getattr(request.app.state, "store", None)
    data_info = None
    if store is not None:
        candle_count = len(store.query_candles(req.market, req.resolution_s, req.start_ts, req.end_ts))
        expected = duration_s // req.resolution_s
        data_info = {
            "available": candle_count,
            "expected": expected,
            "source": "local" if candle_count > 0 else "will_fetch_s3",
        }

    run_id = str(uuid.uuid4())[:8]
    started = time.time()
    _set_status(run_id, "running")
    _set_progress(run_id, phase="init", pct=0, detail="Initializing...", started_at=started)

    def _run():
        try:
            # Phase 1: Build strategy (pure CPU, no DB)
            _set_progress(run_id, phase="strategy", pct=5, detail="Loading strategy...")

            params = req.params or _DEFAULTS.get(req.strategy, {})
            strategy = _build_strategy(req.strategy, params, req.code)
            if strategy is None:
                _set_status(run_id, "failed")
                _set_result(run_id, {"error": f"Unknown strategy: {req.strategy}"})
                return

            # Phase 2: Load candles
            _set_progress(run_id, phase="data", pct=15, detail=f"Checking local data for {req.market}...")

            start_ts = req.start_ts
            end_ts = req.end_ts

            # Step 1: Check local DB
            candles = []
            if store is not None:
                candles = store.query_candles(req.market, req.resolution_s, start_ts, end_ts)

            # Step 2: Check if local data covers the range adequately
            # Compare last candle timestamp vs requested end — if local data
            # ends more than 1 day before requested end, we're missing data
            needs_download = False
            if not candles:
                needs_download = True
            elif candles[-1].ts < end_ts - 86400:
                # Local data doesn't reach the end of the range
                needs_download = True
                local_end = dt.fromtimestamp(candles[-1].ts, tz=tz.utc).strftime("%Y-%m-%d")
                req_end = dt.fromtimestamp(end_ts, tz=tz.utc).strftime("%Y-%m-%d")
                _set_progress(run_id, phase="data", pct=16,
                              detail=f"Local data ends {local_end}, need through {req_end} — downloading...")

            if needs_download:
                # Try Drift Data API first (has current data), fall back to S3 (archival)
                _set_progress(run_id, phase="download", pct=18,
                              detail=f"Fetching {req.market} from Drift Data API...")

                fetched = []
                try:
                    api_provider = DriftCandleProvider()
                    def _on_api_progress(done, total, info):
                        pct = 20 + int((done / max(total, 1)) * 55)
                        _set_progress(run_id, phase="download", pct=pct,
                                      detail=f"Fetching {req.market} candles ({info})")
                    fetched = api_provider.fetch_candles(
                        req.market, req.resolution_s, start_ts, end_ts,
                        on_progress=_on_api_progress,
                    )
                    api_provider.close()
                except Exception as api_err:
                    logger.warning("Drift Data API failed: %s — trying S3", api_err)

                # Fall back to S3 if API returned nothing
                if not fetched:
                    _set_progress(run_id, phase="download", pct=30,
                                  detail=f"API returned no data — trying Drift S3 archive...")
                    try:
                        s3_provider = DriftS3Provider()
                        def _on_s3_progress(done, total, date_str):
                            if total > 0 and date_str != "done":
                                pct = 35 + int((done / total) * 40)
                                _set_progress(run_id, phase="download", pct=pct,
                                              detail=f"Downloading {req.market} from S3 day {done}/{total}")
                        fetched = s3_provider.fetch_candles(
                            req.market, req.resolution_s, start_ts, end_ts,
                            on_progress=_on_s3_progress,
                        )
                        s3_provider.close()
                    except Exception as s3_err:
                        logger.warning("Drift S3 also failed: %s", s3_err)

                if fetched:
                    if store is not None:
                        try:
                            stored = store.upsert_candles(fetched)
                            _set_progress(run_id, phase="cached", pct=78,
                                          detail=f"Cached {stored} candles — next run will be instant")
                        except Exception:
                            pass
                    # Re-query to get merged data
                    if store is not None:
                        candles = store.query_candles(req.market, req.resolution_s, start_ts, end_ts)
                    else:
                        candles = fetched

                # After download, report what we have
                if candles and candles[-1].ts < end_ts - 86400:
                    actual_end = dt.fromtimestamp(candles[-1].ts, tz=tz.utc).strftime("%Y-%m-%d")
                    req_end = dt.fromtimestamp(end_ts, tz=tz.utc).strftime("%Y-%m-%d")
                    _set_progress(run_id, phase="data", pct=78,
                                  detail=f"Data available through {actual_end} (requested {req_end}) — running on available data")
            else:
                _set_progress(run_id, phase="data", pct=78,
                              detail=f"Loaded {len(candles):,} candles from local DB")

            # Step 3: Check if market inception is after requested start
            if candles:
                market_start = candles[0].ts
                if market_start > start_ts:
                    inception = dt.fromtimestamp(market_start, tz=tz.utc).strftime("%Y-%m-%d")
                    requested = dt.fromtimestamp(start_ts, tz=tz.utc).strftime("%Y-%m-%d")
                    _set_progress(run_id, phase="data", pct=79,
                                  detail=f"{req.market} launched {inception} (requested {requested}) — adjusted start date")

            # Step 4: No data at all
            if not candles:
                _set_status(run_id, "failed")
                _set_result(run_id, {"error": f"No data found for {req.market}. The market may not exist on Drift, "
                                     f"or no trades occurred in this period."})
                return

            # Phase 2.5: Data quality check
            quality = check_candle_quality(candles, req.resolution_s)
            data_warnings = []
            if quality.gaps:
                data_warnings.append(f"{len(quality.gaps)} gaps detected in candle data")
            if quality.duplicates > 0:
                data_warnings.append(f"{quality.duplicates} duplicate timestamps")
            if quality.outliers:
                data_warnings.append(f"{len(quality.outliers)} price outliers detected")
            if quality.completeness_pct < 80:
                data_warnings.append(f"Data completeness: {quality.completeness_pct:.0f}%")

            # Phase 3: Run backtest
            first_date = dt.fromtimestamp(candles[0].ts, tz=tz.utc).strftime("%Y-%m-%d")
            last_date = dt.fromtimestamp(candles[-1].ts, tz=tz.utc).strftime("%Y-%m-%d")
            _set_progress(run_id, phase="backtest", pct=80,
                          detail=f"Running {strategy.name} on {len(candles):,} candles ({first_date} to {last_date})",
                          candles=len(candles))

            engine = BacktestEngine(strategy, req.initial_capital, req.fee_rate)
            result = engine.run(candles)

            # Phase 4: Generate tearsheet
            _set_progress(run_id, phase="tearsheet", pct=90,
                          detail=f"Generating tearsheet — {result.total_trades} trades, PnL ${result.total_pnl:+,.2f}")

            tearsheet = generate_tearsheet(
                result, candles,
                strategy_name=strategy.name,
                initial_capital=req.initial_capital,
            )

            _set_progress(run_id, phase="done", pct=100,
                          detail=f"Complete — {result.total_trades} trades, PnL ${result.total_pnl:+,.2f}")

            # Embed data quality and Monte Carlo into tearsheet
            ts_dict = tearsheet.to_dict()
            ts_dict["data_quality"] = {
                "completeness_pct": quality.completeness_pct,
                "gaps": len(quality.gaps),
                "outliers": len(quality.outliers),
                "duplicates": quality.duplicates,
                "warnings": data_warnings,
            }

            # Run Monte Carlo if enough trades
            if result.total_trades >= 5:
                trade_pnls = [p.pnl for p in result.positions]
                mc = run_monte_carlo(trade_pnls, req.initial_capital, n_simulations=500)
                ts_dict["monte_carlo"] = {
                    "n_simulations": mc.n_simulations,
                    "sharpe_ci": [round(mc.sharpe_ci_lower, 2), round(mc.sharpe_ci_upper, 2)],
                    "sharpe_p_value": round(mc.sharpe_p_value, 4),
                    "max_dd_ci": [round(mc.max_dd_ci_lower * 100, 1), round(mc.max_dd_ci_upper * 100, 1)],
                    "pnl_ci": [round(mc.pnl_ci_lower, 2), round(mc.pnl_ci_upper, 2)],
                    "probability_of_ruin": round(mc.probability_of_ruin * 100, 1),
                }

            _set_result(run_id, ts_dict)
            _set_status(run_id, "complete")

            # Auto-save to journal
            if store is not None:
                try:
                    from ...journal.storage import JournalStorage
                    journal = JournalStorage(store)
                    journal.save_run(
                        run_id=run_id,
                        strategy_name=strategy.name,
                        market=req.market,
                        resolution_s=req.resolution_s,
                        start_ts=req.start_ts,
                        end_ts=req.end_ts,
                        initial_capital=req.initial_capital,
                        params=req.params,
                        result=result,
                    )
                except Exception as journal_err:
                    logger.warning("Journal save failed: %s", journal_err)

        except Exception as e:
            logger.exception("Backtest %s failed", run_id)
            _set_status(run_id, "failed")
            _set_result(run_id, {"error": f"{type(e).__name__}: {e}"})
            _set_progress(run_id, phase="error", pct=0, detail=f"{type(e).__name__}: {e}")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return {"id": run_id, "status": "running", "data": data_info}


@router.get("/{run_id}/status")
def get_status(run_id: str):
    with _state_lock:
        if run_id not in _status:
            raise HTTPException(404, "Backtest not found")
        return {"id": run_id, "status": _status[run_id]}


@router.get("/{run_id}/results")
def get_results(run_id: str):
    with _state_lock:
        if run_id not in _status:
            raise HTTPException(404, "Backtest not found")
        status = _status[run_id]
        progress = dict(_progress.get(run_id, {}))
        result = _results.get(run_id)

    elapsed = time.time() - progress.get("started_at", time.time())
    progress_out = {
        "phase": progress.get("phase", "init"),
        "pct": progress.get("pct", 0),
        "detail": progress.get("detail", ""),
        "elapsed_s": round(elapsed, 1),
        "candles": progress.get("candles", 0),
    }

    if status == "running":
        return {"id": run_id, "status": "running", "results": None, "progress": progress_out}
    return {"id": run_id, "status": status, "results": result, "progress": progress_out}


@router.get("/compare")
def compare_backtests(ids: str):
    """Compare multiple backtests. Pass comma-separated IDs."""
    run_ids = [i.strip() for i in ids.split(",")]
    results = []
    with _state_lock:
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
