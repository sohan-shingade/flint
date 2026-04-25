"""Backtest service — synchronous one-shot runner for in-process callers.

The HTTP route in `flint/api/routes/backtest.py` is async, threaded, and
emits progress to a polling endpoint. MCP and other in-process callers
(scripts, notebooks, the future `flint backtest --json` CLI) don't want
that machinery — they want "give me a dict, fail loud, no daemon
required". This service does the minimum: build strategy, load candles
from store (no download fallback), run engine, return tearsheet dict.

Out of scope (deliberately): progress callbacks, sandbox routing,
multi-market aggregate mode, Monte Carlo gating, journal autosave.
Callers that need those go through the HTTP route.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..analytics.tearsheet import generate_tearsheet
from ..backtest.engine import BacktestEngine
from ..models import Candle
from ..store import FlintStore
from .strategies import build_strategy

logger = logging.getLogger("flint.services.backtest")


@dataclass
class BacktestRunRequest:
    market: str
    start_ts: int
    end_ts: int
    resolution_s: int = 3600
    strategy: str = "ma_crossover"
    code: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    initial_capital: float = 10_000.0
    fee_rate: float = 0.0005
    fill_model: str = "close"
    extra_markets: List[str] = field(default_factory=list)


def run_backtest_sync(req: BacktestRunRequest, store: FlintStore) -> Dict[str, Any]:
    """Run a backtest end-to-end and return the tearsheet dict.

    Raises ValueError when the request is malformed or the data is
    insufficient. Returns the same dict shape as the HTTP route's
    `_set_result(...)` payload (so MCP can pretty-print it without
    knowing whether the call went through HTTP or in-process).
    """
    if req.start_ts >= req.end_ts:
        raise ValueError("start_ts must be < end_ts")

    strategy = build_strategy(req.strategy, req.params, req.code)
    if strategy is None:
        raise ValueError(f"Unknown strategy: {req.strategy}")

    candles: List[Candle] = store.query_candles(
        req.market, req.resolution_s, req.start_ts, req.end_ts
    )
    if not candles:
        raise ValueError(
            f"No candles for {req.market} in [{req.start_ts}, {req.end_ts}]. "
            f"Download via `flint download` or HTTP /api/v1/data/download first."
        )

    extra_candles: Dict[str, List[Candle]] = {}
    for mkt in req.extra_markets:
        if mkt == req.market:
            continue
        c = store.query_candles(mkt, req.resolution_s, req.start_ts, req.end_ts)
        if c:
            extra_candles[mkt] = c

    funding_rates = []
    try:
        funding_rates = store.query_funding_rates(
            req.market, start_ts=req.start_ts, end_ts=req.end_ts
        )
    except Exception as e:
        logger.debug("No funding rates for %s: %s", req.market, e)

    open_interest = []
    try:
        open_interest = store.query_open_interest(
            req.market, start_ts=req.start_ts, end_ts=req.end_ts
        )
    except Exception as e:
        logger.debug("No OI for %s: %s", req.market, e)

    from ..execution.fill_models import (
        ClosePriceFill, FillPipeline, NextBarOpenFill, SlippageFill,
    )

    if req.fill_model == "slippage":
        fill = SlippageFill()
    elif req.fill_model == "next_bar_open":
        fill = NextBarOpenFill()
    elif req.fill_model == "pipeline":
        fill = FillPipeline()
    else:
        fill = ClosePriceFill()

    engine = BacktestEngine(
        strategy, req.initial_capital, req.fee_rate,
        fill_model=fill,
        funding_rates=funding_rates,
        open_interest=open_interest,
    )
    if extra_candles:
        result = engine.run(candles, extra_markets=extra_candles)
    else:
        result = engine.run(candles)

    tearsheet_candles = {req.market: candles, **extra_candles} if extra_candles else None
    tearsheet = generate_tearsheet(
        result, candles,
        strategy_name=strategy.name,
        initial_capital=req.initial_capital,
        all_candles=tearsheet_candles,
    )

    out = tearsheet.to_dict()
    out["engine_used"] = getattr(result, "engine_used", None)
    out["fallback_reason"] = getattr(result, "fallback_reason", None)

    # Tearsheet metrics intentionally omit `winning_trades`, `losing_trades`,
    # and `total_fees` — surface them here for parity with the HTTP route.
    out["winning_trades"] = result.winning_trades
    out["losing_trades"] = result.losing_trades
    out["total_fees"] = result.total_fees
    return out
