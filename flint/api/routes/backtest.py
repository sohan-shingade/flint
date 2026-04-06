"""Backtest API routes."""
from __future__ import annotations

import logging
import re
import uuid
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime as dt, timezone as tz
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ...backtest.engine import BacktestEngine
from ...models import Candle
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
    FundingHarvestStrategy,
    MeanReversionStrategy,
    BreakoutMomentumStrategy,
    GridTraderStrategy,
    DualTimeframeStrategy,
    VWAPReversionStrategy,
    MACDDivergenceStrategy,
    ATRBreakoutStrategy,
    MultiVenueFundingStrategy,
    RSIMACDComboStrategy,
    FundingMeanReversionStrategy,
    MomentumBreakoutStrategy,
    FundingArbStrategy,
    BasisTradeStrategy,
    MevArbMonitor,
)
from ...strategy.loader import load_user_strategy, StrategyLoadError

logger = logging.getLogger("flint.backtest")

router = APIRouter()

# In-memory state — protected by _state_lock, capped at 200 entries
_MAX_ENTRIES = 200
_MAX_AGE_S = 3600
_state_lock = threading.Lock()

# Concurrency and timeout limits
_MAX_CONCURRENT = 5
_MAX_BACKTEST_SECONDS = 300
_concurrency: Dict[str, int] = {"active": 0}


@dataclass
class _BacktestEntry:
    status: str
    result: Optional[dict] = None
    progress: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


_entries: Dict[str, _BacktestEntry] = {}


def configure_concurrency(max_concurrent: int):
    """Called at startup to set concurrency from config."""
    global _MAX_CONCURRENT
    _MAX_CONCURRENT = max_concurrent


def _evict_old():
    """Remove oldest/expired entries. Called inside _state_lock."""
    now = time.time()
    expired = [k for k, v in _entries.items() if now - v.created_at > _MAX_AGE_S]
    for k in expired:
        _entries.pop(k, None)
    if len(_entries) > _MAX_ENTRIES:
        by_age = sorted(_entries.keys(), key=lambda k: _entries[k].created_at)
        for k in by_age[:len(_entries) - _MAX_ENTRIES]:
            _entries.pop(k, None)


def _set_status(run_id: str, status: str):
    with _state_lock:
        if run_id in _entries:
            _entries[run_id].status = status
        else:
            _entries[run_id] = _BacktestEntry(status=status)
        _evict_old()


def _set_progress(run_id: str, **kwargs):
    with _state_lock:
        if run_id not in _entries:
            _entries[run_id] = _BacktestEntry(status="running")
        _entries[run_id].progress.update(kwargs)


def _set_result(run_id: str, result: dict):
    with _state_lock:
        if run_id not in _entries:
            _entries[run_id] = _BacktestEntry(status="running")
        _entries[run_id].result = result


class BacktestRequest(BaseModel):
    strategy: str = "ma_crossover"
    code: Optional[str] = None
    market: str = "SOL-PERP"          # primary market (backwards compat)
    markets: Optional[List[str]] = None  # additional markets for multi-market strategies
    resolution_s: int = 3600
    start_ts: int
    end_ts: int
    initial_capital: float = Field(default=10_000.0, gt=0, le=1e12)
    fee_rate: float = Field(default=0.0005, ge=0, le=0.5)
    params: Optional[Dict] = None
    # v0.3 execution features (opt-in)
    margin_tracking: bool = False
    capital_allocation: Optional[Dict[str, float]] = None  # e.g. {"drift": 5000, "hyperliquid": 3000}
    # Fill pipeline configuration
    fill_model: str = "pipeline"  # "pipeline", "slippage", "close", "next_bar_open"
    slippage_bps: float = Field(default=10.0, ge=0, le=1000)
    latency_enabled: bool = True
    latency_seed: Optional[int] = None
    impact_coefficient: Optional[float] = Field(default=None, ge=0, le=1.0)
    aggregate: bool = False  # Run independently on each market, return per-market + summary


def _build_strategy(name: str, params: Dict, code: str = None):
    """Instantiate a strategy by name, user file, or inline code."""
    if code:
        return load_user_strategy(code, params or None)

    if name.startswith("user:"):
        from pathlib import Path
        strat_name = name[5:]
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_-]{0,63}$', strat_name):
            return None
        user_dir = Path(__file__).resolve().parents[3] / "strategies" / "user"
        path = (user_dir / f"{strat_name}.py").resolve()
        if not str(path).startswith(str(user_dir.resolve())):
            return None
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
        "funding_mean_reversion": lambda p: FundingMeanReversionStrategy(
            bb_lookback=int(p.get("bb_lookback", 24)),
            bb_std=float(p.get("bb_std", 2.0)),
            max_hold_hours=int(p.get("max_hold_hours", 12)),
            position_size_pct=float(p.get("position_size_pct", 0.5)),
            candle_resolution_s=int(p.get("candle_resolution_s", 3600)),
        ),
        "momentum_breakout": lambda p: MomentumBreakoutStrategy(
            breakout_lookback=int(p.get("breakout_lookback", 20)),
            trailing_stop_pct=float(p.get("trailing_stop_pct", 0.02)),
            oracle_confirmation=int(p.get("oracle_confirmation", 1)),
            candle_resolution_s=int(p.get("candle_resolution_s", 3600)),
        ),
        "funding_arb": lambda p: FundingArbStrategy(
            min_spread_bps=float(p.get("min_spread_bps", 5.0)),
            exit_spread_bps=float(p.get("exit_spread_bps", 1.0)),
            max_hold_hours=int(p.get("max_hold_hours", 24)),
            position_size_usd=float(p.get("position_size_usd", 1000.0)),
            min_spread_duration=int(p.get("min_spread_duration", 1)),
            candle_resolution_s=int(p.get("candle_resolution_s", 60)),
        ),
        "basis_trade": lambda p: BasisTradeStrategy(
            entry_basis_bps=float(p.get("entry_basis_bps", 30.0)),
            exit_basis_bps=float(p.get("exit_basis_bps", 5.0)),
            max_hold_hours=int(p.get("max_hold_hours", 12)),
            position_size_usd=float(p.get("position_size_usd", 1000.0)),
            candle_resolution_s=int(p.get("candle_resolution_s", 3600)),
        ),
        "mev_arb_monitor": lambda p: MevArbMonitor(
            min_profit_bps=float(p.get("min_profit_bps", 10.0)),
            max_hops=int(p.get("max_hops", 3)),
            alert_enabled=int(p.get("alert_enabled", 0)),
            candle_resolution_s=int(p.get("candle_resolution_s", 60)),
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
    "funding_harvest": {"entry_threshold": 0.00003, "exit_threshold": 0.000005, "stop_loss_pct": 0.05, "lookback": 8},
    "mean_reversion": {"period": 20, "entry_z": 2.0, "exit_z": 0.5, "stop_loss_pct": 0.05},
    "breakout_momentum": {},
    "grid_trader": {},
    "dual_timeframe": {},
    "vwap_reversion": {"period": 20, "entry_pct": 2.0, "exit_pct": 0.5},
    "macd_divergence": {"fast": 12, "slow": 26, "signal": 9},
    "atr_breakout": {"period": 20, "atr_period": 14, "multiplier": 2.0},
    "multi_venue_funding": {"entry_threshold": 0.00003, "exit_threshold": 0.000005, "lookback": 12},
    "rsi_macd_combo": {"rsi_period": 14, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "rsi_oversold": 30, "rsi_overbought": 70},
    "funding_mean_reversion": {"bb_lookback": 24, "bb_std": 2.0, "max_hold_hours": 12, "position_size_pct": 0.5, "candle_resolution_s": 3600},
    "momentum_breakout": {"breakout_lookback": 20, "trailing_stop_pct": 0.02, "oracle_confirmation": 1, "candle_resolution_s": 3600},
    "funding_arb": {"min_spread_bps": 5.0, "exit_spread_bps": 1.0, "max_hold_hours": 24, "position_size_usd": 1000.0, "min_spread_duration": 1, "candle_resolution_s": 60},
    "basis_trade": {"entry_basis_bps": 30.0, "exit_basis_bps": 5.0, "max_hold_hours": 12, "position_size_usd": 1000.0, "candle_resolution_s": 3600},
    "mev_arb_monitor": {"min_profit_bps": 10.0, "max_hops": 3, "alert_enabled": 0, "candle_resolution_s": 60},
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

    # Concurrent backtest limit
    with _state_lock:
        if _concurrency["active"] >= _MAX_CONCURRENT:
            raise HTTPException(429, "Too many concurrent backtests — try again shortly")
        _concurrency["active"] += 1

    run_id = str(uuid.uuid4())[:8]
    started = time.time()
    _set_status(run_id, "running")
    _set_progress(run_id, phase="init", pct=0, detail="Initializing...", started_at=started)

    def _run():
        try:
            # Phase 1: Build strategy (pure CPU, no DB)
            _set_progress(run_id, phase="strategy", pct=5, detail="Loading strategy...")

            extra_candles: Dict[str, List[Candle]] = {}

            params = req.params or ({} if req.code else _DEFAULTS.get(req.strategy, {}))
            strategy = _build_strategy(req.strategy, params, req.code)
            if strategy is None:
                _set_status(run_id, "failed")
                _set_result(run_id, {"error": f"Unknown strategy: {req.strategy}"})
                return

            # Phase 2: Load candles
            _set_progress(run_id, phase="data", pct=15, detail=f"Checking local data for {req.market}...")

            start_ts = req.start_ts
            end_ts = req.end_ts

            # Aggregate mode: run independently on each market
            if req.aggregate and req.markets:
                all_markets = [req.market] + [m for m in req.markets if m != req.market]
                per_market = {}
                sharpes = []

                for idx, mkt in enumerate(all_markets):
                    pct = 15 + int(70 * idx / len(all_markets))
                    _set_progress(run_id, phase="backtest", pct=pct,
                                  detail=f"Running {strategy.name} on {mkt} ({idx+1}/{len(all_markets)})...")

                    mkt_candles = store.query_candles(mkt, req.resolution_s, start_ts, end_ts) if store else []
                    if not mkt_candles:
                        per_market[mkt] = {"error": "No data available"}
                        continue

                    strategy.reset()
                    eng = BacktestEngine(strategy, req.initial_capital, req.fee_rate)
                    res = eng.run(mkt_candles)
                    mkt_metrics = {
                        "total_pnl": round(res.total_pnl, 4),
                        "total_trades": res.total_trades,
                        "win_rate": round(res.win_rate, 4),
                        "sharpe_ratio": round(res.sharpe_ratio, 4),
                        "max_drawdown": round(res.max_drawdown, 4),
                    }
                    per_market[mkt] = mkt_metrics
                    sharpes.append(res.sharpe_ratio)

                aggregate_out = {}
                if sharpes:
                    aggregate_out = {
                        "min_sharpe": round(min(sharpes), 4),
                        "max_sharpe": round(max(sharpes), 4),
                        "avg_sharpe": round(sum(sharpes) / len(sharpes), 4),
                        "n_markets": len(sharpes),
                    }

                _set_result(run_id, {
                    "strategy_name": strategy.name,
                    "per_market": per_market,
                    "aggregate": aggregate_out,
                })
                _set_status(run_id, "complete")
                return

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

            # Phase 2.6: Load extra markets for multi-market strategies
            all_market_names = [req.market]
            if req.markets:
                extra_market_list = [m for m in req.markets if m != req.market]
                all_market_names.extend(extra_market_list)
                for i, extra_m in enumerate(extra_market_list):
                    _set_progress(run_id, phase="data", pct=79,
                                  detail=f"Loading {extra_m} ({i+1}/{len(extra_market_list)})...")
                    extra_c = []
                    if store is not None:
                        extra_c = store.query_candles(extra_m, req.resolution_s, start_ts, end_ts)
                    if not extra_c:
                        # Try downloading
                        try:
                            api_provider = DriftCandleProvider()
                            extra_c = api_provider.fetch_candles(extra_m, req.resolution_s, start_ts, end_ts)
                            api_provider.close()
                            if extra_c and store is not None:
                                store.upsert_candles(extra_c)
                        except Exception as e:
                            logger.warning("Failed to load %s: %s", extra_m, e)
                    if extra_c:
                        extra_candles[extra_m] = extra_c

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

            # Phase 2.7: Load funding rates for the backtest period
            funding_rates = []
            try:
                funding_rates = store.query_funding_rates(
                    req.market, start_ts=req.start_ts, end_ts=req.end_ts
                )
                if funding_rates:
                    logger.info("Loaded %d funding rates for %s", len(funding_rates), req.market)
            except Exception as e:
                logger.debug("No funding rates available for %s: %s", req.market, e)

            # Phase 2.8: Load open interest for the backtest period
            open_interest = []
            try:
                open_interest = store.query_open_interest(
                    req.market, start_ts=req.start_ts, end_ts=req.end_ts
                )
                if open_interest:
                    logger.info("Loaded %d OI snapshots for %s", len(open_interest), req.market)
            except Exception as e:
                logger.debug("No OI data for %s: %s", req.market, e)

            # Phase 2.9: Load orderbook snapshots for the backtest period
            orderbook_snapshots = []
            try:
                orderbook_snapshots = store.query_orderbook_snapshots(
                    req.market, start_ts=req.start_ts, end_ts=req.end_ts
                )
                if orderbook_snapshots:
                    logger.info("Loaded %d orderbook snapshots for %s", len(orderbook_snapshots), req.market)
            except Exception as e:
                logger.debug("No orderbook data available for %s: %s", req.market, e)

            # Phase 3: Run backtest
            first_date = dt.fromtimestamp(candles[0].ts, tz=tz.utc).strftime("%Y-%m-%d")
            last_date = dt.fromtimestamp(candles[-1].ts, tz=tz.utc).strftime("%Y-%m-%d")
            _set_progress(run_id, phase="backtest", pct=80,
                          detail=f"Running {strategy.name} on {len(candles):,} candles ({first_date} to {last_date})",
                          candles=len(candles))

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

            # Build fill model
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
                # Default: pipeline
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
                    latency_seed=req.latency_seed,
                    latency_enabled=req.latency_enabled,
                )

            engine = BacktestEngine(strategy, req.initial_capital, req.fee_rate,
                                    fill_model=fill_model,
                                    funding_rates=funding_rates,
                                    orderbook_snapshots=orderbook_snapshots,
                                    open_interest=open_interest,
                                    margin_engine=margin_eng,
                                    capital_allocator=cap_alloc,
                                    max_runtime_s=_MAX_BACKTEST_SECONDS)
            if extra_candles:
                # Multi-market: pass primary candles + extras separately
                # so the engine doesn't pick a different primary by count
                result = engine.run(candles, extra_markets=extra_candles)
            else:
                result = engine.run(candles)

            # Phase 4: Generate tearsheet
            _set_progress(run_id, phase="tearsheet", pct=90,
                          detail=f"Generating tearsheet — {result.total_trades} trades, PnL ${result.total_pnl:+,.2f}")

            # Build all_candles dict for multi-market tearsheet
            tearsheet_candles = None
            if extra_candles:
                tearsheet_candles = {req.market: candles}
                tearsheet_candles.update(extra_candles)

            tearsheet = generate_tearsheet(
                result, candles,
                strategy_name=strategy.name,
                initial_capital=req.initial_capital,
                all_candles=tearsheet_candles,
            )

            _set_progress(run_id, phase="done", pct=100,
                          detail=f"Complete — {result.total_trades} trades, PnL ${result.total_pnl:+,.2f}")

            # Embed data quality and Monte Carlo into tearsheet
            ts_dict = tearsheet.to_dict()
            # Collect strategy warnings (e.g. missing funding data)
            if result.strategy_warnings:
                data_warnings.extend(result.strategy_warnings)

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
                mc = run_monte_carlo(trade_pnls, req.initial_capital, n_simulations=500,
                                     period_seconds=req.end_ts - req.start_ts)
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
        finally:
            with _state_lock:
                _concurrency["active"] = max(0, _concurrency["active"] - 1)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return {"id": run_id, "status": "running", "data": data_info}


@router.get("/list")
def list_backtests():
    """List all tracked backtest runs with their statuses."""
    with _state_lock:
        runs = []
        for run_id, entry in _entries.items():
            runs.append({
                "id": run_id,
                "status": entry.status,
                "phase": entry.progress.get("phase", ""),
                "detail": entry.progress.get("detail", ""),
            })
    return {"runs": runs}


@router.get("/{run_id}/status")
def get_status(run_id: str):
    with _state_lock:
        if run_id not in _entries:
            raise HTTPException(404, "Backtest not found")
        return {"id": run_id, "status": _entries[run_id].status}


@router.get("/{run_id}/results")
def get_results(run_id: str):
    import json
    from starlette.responses import JSONResponse
    try:
        with _state_lock:
            if run_id not in _entries:
                raise HTTPException(404, "Backtest not found")
            entry = _entries[run_id]
            status = entry.status
            progress = dict(entry.progress)
            result = dict(entry.result) if isinstance(entry.result, dict) else entry.result

        elapsed = time.time() - progress.get("started_at", time.time())
        progress_out = {
            "phase": progress.get("phase", "init"),
            "pct": progress.get("pct", 0),
            "detail": progress.get("detail", ""),
            "elapsed_s": round(elapsed, 1),
            "candles": progress.get("candles", 0),
        }

        # Detect hung backtests (stuck in "running" beyond timeout + buffer)
        if status == "running" and elapsed > _MAX_BACKTEST_SECONDS + 60:
            _set_status(run_id, "failed")
            _set_result(run_id, {"error": "Backtest exceeded maximum runtime"})
            return {"id": run_id, "status": "failed",
                    "results": {"error": "Backtest exceeded maximum runtime"},
                    "progress": progress_out}

        if status == "running":
            return {"id": run_id, "status": "running", "results": None, "progress": progress_out}

        response = {"id": run_id, "status": status, "results": result, "progress": progress_out}
        # Pre-validate JSON serializability to catch errors before FastAPI tries
        json.dumps(response)
        return response
    except HTTPException:
        raise
    except Exception as e:
        logging.getLogger("flint.backtest").exception("Error in get_results for %s", run_id)
        return JSONResponse(status_code=500, content={
            "id": run_id, "status": "error",
            "results": {"error": f"Results retrieval failed: {type(e).__name__}: {e}"},
            "progress": {},
        })


@router.post("/{run_id}/cancel")
def cancel_backtest(run_id: str):
    """Mark a backtest as cancelled. The thread will continue but results are discarded."""
    with _state_lock:
        if run_id not in _entries:
            raise HTTPException(404, "Backtest not found")
        if _entries[run_id].status != "running":
            return {"id": run_id, "status": _entries[run_id].status, "message": "Not running"}
        _entries[run_id].status = "cancelled"
    _set_progress(run_id, phase="cancelled", detail="Cancelled by user")
    return {"id": run_id, "status": "cancelled"}


@router.get("/compare")
def compare_backtests(ids: str):
    """Compare multiple backtests. Pass comma-separated IDs."""
    run_ids = [i.strip() for i in ids.split(",")]
    results = []
    with _state_lock:
        for rid in run_ids:
            entry = _entries.get(rid)
            if entry and entry.status == "complete" and entry.result:
                r = entry.result
                results.append({
                    "id": rid,
                    "strategy": r.get("strategy_name", ""),
                    "metrics": r.get("metrics", {}),
                    "equity_curve": r.get("equity_curve", []),
                })
    return {"comparisons": results}


@router.post("/calibrate")
def run_calibration(req: dict, request: Request):
    """Run slippage calibration (read-only, does not write to config)."""
    from ...backtest.calibration import CalibrationEngine

    store = getattr(request.app.state, "store", None)
    if store is None:
        from fastapi import HTTPException
        raise HTTPException(500, "Store not available")

    venue = req.get("venue", "drift")
    market = req.get("market", "SOL-PERP")
    lookback_days = req.get("lookback_days", 30)

    try:
        engine = CalibrationEngine(store)
        report = engine.calibrate(venue=venue, market=market, lookback_days=lookback_days)
        return report.to_dict()
    except ValueError as e:
        return {"error": str(e)}


@router.post("/parity")
def run_parity(req: dict, request: Request):
    """Run backtest-vs-paper parity test."""
    from ...backtest.parity import ParityTest

    store: Optional[FlintStore] = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(500, "Store not available")

    market = req.get("market", "SOL-PERP")
    strategy_name = req.get("strategy", "momentum")
    start_ts = req.get("start_ts", 0)
    end_ts = req.get("end_ts", 0)
    capital = req.get("capital", 10_000.0)
    fee_rate = req.get("fee_rate", 0.0005)

    candles = store.query_candles(market, 3600, start_ts=start_ts, end_ts=end_ts)
    if not candles:
        return {"error": f"No candle data for {market}"}

    strategy = _build_strategy(strategy_name, req.get("params", {}))
    if strategy is None:
        return {"error": f"Unknown strategy: {strategy_name}"}

    pt = ParityTest(
        strategy=strategy, market=market, candles=candles,
        initial_capital=capital, fee_rate=fee_rate,
    )
    report = pt.run()
    return report.to_dict()
