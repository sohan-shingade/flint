"""Tearsheet generator — produces a JSON-serialisable report for the UI."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from ..models import BacktestResult, Candle, Position
from .metrics import MetricsSummary, compute_metrics


@dataclass
class TradeRecord:
    entry_ts: int
    exit_ts: int
    side: str
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    holding_s: int


@dataclass
class Tearsheet:
    strategy_name: str
    market: str
    resolution_s: int
    period_start: int
    period_end: int
    initial_capital: float
    # metrics
    metrics: Dict[str, Any]
    # curves (list of [ts, value] pairs for charting)
    equity_curve: List[List[float]]
    drawdown_curve: List[List[float]]
    # monthly returns: {year: {month: return_pct}}
    monthly_returns: Dict[int, Dict[int, float]]
    # trade log
    trades: List[Dict[str, Any]]
    # buy-and-hold comparison
    buy_hold_equity: List[List[float]]
    buy_hold_return_pct: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def generate_tearsheet(
    result: BacktestResult,
    candles: List[Candle],
    strategy_name: str = "",
    initial_capital: float = 10_000.0,
) -> Tearsheet:
    metrics = compute_metrics(result, initial_capital)

    # Timestamps from candles
    timestamps = [c.ts for c in candles]
    market = candles[0].market if candles else ""
    resolution_s = candles[0].resolution_s if candles else 3600

    # --- Equity curve ---
    eq = result.equity_curve
    equity_curve = [[timestamps[i], eq[i]] for i in range(min(len(timestamps), len(eq)))]

    # --- Drawdown curve ---
    eq_arr = np.array(eq) if eq else np.array([initial_capital])
    peak = np.maximum.accumulate(eq_arr)
    dd = (peak - eq_arr) / np.where(peak == 0, 1, peak)
    drawdown_curve = [
        [timestamps[i], float(dd[i])] for i in range(min(len(timestamps), len(dd)))
    ]

    # --- Monthly returns ---
    monthly = _compute_monthly_returns(timestamps, eq)

    # --- Trade log ---
    trades = [
        asdict(TradeRecord(
            entry_ts=p.entry_ts,
            exit_ts=p.exit_ts,
            side="long" if p.size > 0 else "short",
            entry_price=p.entry_price,
            exit_price=p.exit_price,
            size=abs(p.size),
            pnl=p.pnl,
            holding_s=p.exit_ts - p.entry_ts,
        ))
        for p in result.positions
    ]

    # --- Buy and hold comparison ---
    bh_equity, bh_return = _buy_and_hold(candles, initial_capital)
    buy_hold_curve = [
        [timestamps[i], bh_equity[i]] for i in range(min(len(timestamps), len(bh_equity)))
    ]

    return Tearsheet(
        strategy_name=strategy_name,
        market=market,
        resolution_s=resolution_s,
        period_start=timestamps[0] if timestamps else 0,
        period_end=timestamps[-1] if timestamps else 0,
        initial_capital=initial_capital,
        metrics=asdict(metrics),
        equity_curve=equity_curve,
        drawdown_curve=drawdown_curve,
        monthly_returns=monthly,
        trades=trades,
        buy_hold_equity=buy_hold_curve,
        buy_hold_return_pct=bh_return,
    )


def _compute_monthly_returns(
    timestamps: List[int], equity: List[float]
) -> Dict[int, Dict[int, float]]:
    """Compute monthly return % grouped by year and month."""
    if len(timestamps) < 2 or len(equity) < 2:
        return {}

    monthly: Dict[int, Dict[int, float]] = {}
    prev_month_eq = equity[0]
    prev_dt = datetime.fromtimestamp(timestamps[0], tz=timezone.utc)

    for i in range(1, min(len(timestamps), len(equity))):
        dt = datetime.fromtimestamp(timestamps[i], tz=timezone.utc)
        if dt.month != prev_dt.month or dt.year != prev_dt.year:
            ret = (equity[i - 1] / prev_month_eq - 1) * 100 if prev_month_eq else 0
            monthly.setdefault(prev_dt.year, {})[prev_dt.month] = round(ret, 2)
            prev_month_eq = equity[i - 1]
        prev_dt = dt

    # Final partial month
    if timestamps:
        dt = datetime.fromtimestamp(timestamps[-1], tz=timezone.utc)
        ret = (equity[-1] / prev_month_eq - 1) * 100 if prev_month_eq else 0
        monthly.setdefault(dt.year, {})[dt.month] = round(ret, 2)

    return monthly


def _buy_and_hold(candles: List[Candle], capital: float) -> tuple:
    """Simulate buying at first candle close and holding."""
    if not candles:
        return [capital], 0.0
    entry = candles[0].close
    size = capital / entry if entry else 0
    bh = [size * c.close for c in candles]
    ret_pct = (bh[-1] / capital - 1) * 100 if capital else 0
    return bh, ret_pct
