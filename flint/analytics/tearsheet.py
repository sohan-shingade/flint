"""Tearsheet generator — produces a JSON-serialisable report for the UI."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from ..models import BacktestResult, Candle
from .metrics import compute_metrics


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
    market: str = ""
    venue: str = "default"


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
    # benchmark comparison
    buy_hold_equity: List[List[float]]
    buy_hold_return_pct: float
    # multi-market info
    benchmark_label: str = "BUY & HOLD"
    markets_used: List[str] = field(default_factory=list)
    # per-instrument exposure timeline: {market: [{ts, exposure, notional}, ...]}
    instrument_exposure: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    # rolling 30-day Sharpe ratio: [{ts, sharpe}, ...]
    rolling_sharpe: List[Dict[str, Any]] = field(default_factory=list)
    # individual trade breakdown with entry/exit details
    trade_breakdown: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def generate_tearsheet(
    result: BacktestResult,
    candles: List[Candle],
    strategy_name: str = "",
    initial_capital: float = 10_000.0,
    all_candles: Optional[Dict[str, List[Candle]]] = None,
) -> Tearsheet:
    metrics = compute_metrics(result, initial_capital)

    # Inject margin tracking metrics if present
    metrics_dict = asdict(metrics)
    if hasattr(result, 'margin_stats') and result.margin_stats is not None:
        ms = result.margin_stats
        metrics_dict["max_leverage"] = round(ms.max_leverage, 2)
        metrics_dict["avg_leverage"] = round(ms.avg_leverage, 2)
        metrics_dict["margin_calls"] = ms.margin_calls
        metrics_dict["max_margin_utilization_pct"] = round(ms.max_utilization_pct, 1)

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

    # --- Trade log (with market/venue) ---
    trades = []
    for p in result.positions:
        rec = TradeRecord(
            entry_ts=p.entry_ts,
            exit_ts=p.exit_ts,
            side="long" if p.size > 0 else "short",
            entry_price=p.entry_price,
            exit_price=p.exit_price,
            size=abs(p.size),
            pnl=p.pnl,
            holding_s=p.exit_ts - p.entry_ts,
        )
        trades.append(asdict(rec))

    # Match trades to fills by entry timestamp (not by index — each trade has 2+ fills)
    fill_by_ts = {}
    for f in result.fills:
        if f.ts not in fill_by_ts:
            fill_by_ts[f.ts] = f
    for t in trades:
        entry_fill = fill_by_ts.get(t["entry_ts"])
        if entry_fill:
            t["market"] = entry_fill.market
            t["venue"] = entry_fill.venue
        elif result.fills:
            t["market"] = result.fills[0].market
            t["venue"] = result.fills[0].venue if result.fills[0].venue else "default"

    # --- Benchmark: equal-weight portfolio of all markets ---
    markets_used = [market]
    if all_candles and len(all_candles) > 1:
        markets_used = sorted(all_candles.keys())
        bh_equity, bh_return, bh_label = _equal_weight_benchmark(
            all_candles, initial_capital, timestamps
        )
    else:
        bh_equity, bh_return = _buy_and_hold(candles, initial_capital)
        bh_label = f"BUY & HOLD {market}"

    buy_hold_curve = [
        [timestamps[i], bh_equity[i]] for i in range(min(len(timestamps), len(bh_equity)))
    ]

    # --- Per-instrument exposure timeline ---
    instrument_exposure = _compute_instrument_exposure(result, timestamps)

    # --- Rolling Sharpe ratio (30-day window) ---
    rolling_sharpe = _compute_rolling_sharpe(timestamps, eq, resolution_s)

    # --- Trade breakdown from closed positions ---
    trade_breakdown = _compute_trade_breakdown(result)

    return Tearsheet(
        strategy_name=strategy_name,
        market=market,
        resolution_s=resolution_s,
        period_start=timestamps[0] if timestamps else 0,
        period_end=timestamps[-1] if timestamps else 0,
        initial_capital=initial_capital,
        metrics=metrics_dict,
        equity_curve=equity_curve,
        drawdown_curve=drawdown_curve,
        monthly_returns=monthly,
        trades=trades,
        buy_hold_equity=buy_hold_curve,
        buy_hold_return_pct=bh_return,
        benchmark_label=bh_label,
        markets_used=markets_used,
        instrument_exposure=instrument_exposure,
        rolling_sharpe=rolling_sharpe,
        trade_breakdown=trade_breakdown,
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


def _equal_weight_benchmark(
    all_candles: Dict[str, List[Candle]],
    capital: float,
    timestamps: List[int],
) -> tuple:
    """Equal-weight buy-and-hold across all markets.

    Splits capital equally, buys each at first close, holds.
    Returns (equity_list, return_pct, label).
    """
    n_markets = len(all_candles)
    if n_markets == 0:
        return [capital] * len(timestamps), 0.0, "BENCHMARK"

    capital_per = capital / n_markets

    # Index each market's candles by timestamp for alignment
    market_prices: Dict[str, Dict[int, float]] = {}
    for mkt, mkt_candles in all_candles.items():
        market_prices[mkt] = {c.ts: c.close for c in mkt_candles}

    # Compute per-market sizes at entry
    sizes = {}
    for mkt, mkt_candles in all_candles.items():
        if mkt_candles:
            entry_price = mkt_candles[0].close
            sizes[mkt] = capital_per / entry_price if entry_price else 0
        else:
            sizes[mkt] = 0

    # Build equity curve aligned to primary timestamps
    equity = []
    for ts in timestamps:
        total = 0.0
        for mkt in all_candles:
            price = market_prices[mkt].get(ts)
            if price is None:
                # Use nearest earlier price
                mkt_ts = sorted(t for t in market_prices[mkt] if t <= ts)
                price = market_prices[mkt][mkt_ts[-1]] if mkt_ts else 0
            total += sizes[mkt] * price
        equity.append(total)

    ret_pct = (equity[-1] / capital - 1) * 100 if capital and equity else 0
    label = "EQ-WEIGHT " + "+".join(sorted(all_candles.keys()))
    return equity, ret_pct, label


def _compute_instrument_exposure(
    result: BacktestResult, timestamps: List[int]
) -> Dict[str, List[Dict[str, Any]]]:
    """Compute per-instrument exposure over time from trade history.

    Returns {market: [{ts, direction, size}, ...]} where direction is
    +1 (long), -1 (short), or 0 (flat).
    """
    if not result.positions or not timestamps:
        return {}

    # Collect all markets from trades (match by entry timestamp)
    fill_by_ts = {}
    for f in result.fills:
        if f.ts not in fill_by_ts:
            fill_by_ts[f.ts] = f
    markets = set()
    for p in result.positions:
        entry_fill = fill_by_ts.get(p.entry_ts)
        m = entry_fill.market if entry_fill else (
            result.fills[0].market if result.fills else "unknown")
        markets.add(m)

    if not markets or markets == {"unknown"}:
        return {}

    # Build per-market exposure events from fills
    exposure: Dict[str, List[Dict[str, Any]]] = {}
    for mkt in sorted(markets):
        # Get fills for this market
        mkt_fills = [f for f in result.fills if f.market == mkt]
        if not mkt_fills:
            continue

        events = []
        pos_size = 0.0
        pos_side = 0  # +1 long, -1 short, 0 flat

        for f in sorted(mkt_fills, key=lambda x: x.ts):
            fill_dir = 1 if f.side.value == "long" else -1

            # Track net position
            if pos_side == 0:
                pos_size = f.size
                pos_side = fill_dir
            elif fill_dir == pos_side:
                pos_size += f.size
            else:
                pos_size -= f.size
                if pos_size <= 0.0001:
                    pos_size = 0
                    pos_side = 0

            events.append({
                "ts": f.ts,
                "direction": pos_side,
                "size": pos_size,
                "notional": pos_size * f.price if pos_size > 0 else 0,
            })

        if events:
            exposure[mkt] = events

    return exposure


def _compute_rolling_sharpe(
    timestamps: List[int], equity: List[float], resolution_s: int
) -> List[Dict[str, Any]]:
    """Compute rolling 30-day Sharpe ratio from the equity curve.

    Window size adapts to resolution: 30 days * 24 hours * 3600 / resolution_s bars.
    Uses bar-to-bar returns and annualises assuming 365 * 24 * 3600 / resolution_s bars/year.
    """
    if len(equity) < 2 or len(timestamps) < 2:
        return []

    window = max(2, int(30 * 24 * 3600 / resolution_s))
    bars_per_year = 365 * 24 * 3600 / resolution_s

    eq_arr = np.array(equity[: len(timestamps)])
    returns = np.diff(eq_arr) / np.where(eq_arr[:-1] == 0, 1, eq_arr[:-1])

    result = []
    for i in range(window, len(returns) + 1):
        w = returns[i - window : i]
        std = float(np.std(w, ddof=1)) if len(w) > 1 else 0.0
        if std > 1e-12:
            sharpe = float(np.mean(w)) / std * np.sqrt(bars_per_year)
        else:
            sharpe = 0.0
        # timestamp corresponds to the last bar in the window
        ts_idx = i  # returns[i-1] corresponds to timestamps[i]
        if ts_idx < len(timestamps):
            result.append({"ts": timestamps[ts_idx], "sharpe": round(sharpe, 3)})

    return result


def _compute_trade_breakdown(result: "BacktestResult") -> List[Dict[str, Any]]:
    """Format closed positions into a trade breakdown list.

    Each entry contains entry/exit timestamps, prices, side, size, PnL,
    duration in hours, and total funding paid during the trade's lifetime.
    """
    if not result.positions:
        return []

    # Pre-compute per-trade funding paid by distributing total funding
    # proportionally across trades by duration (best approximation when
    # per-trade funding is not tracked separately).
    total_funding = result.funding_paid
    total_duration = sum(
        max(1, p.exit_ts - p.entry_ts)
        for p in result.positions
        if p.closed
    )

    breakdown = []
    for p in result.positions:
        if not p.closed:
            continue
        duration_s = max(1, p.exit_ts - p.entry_ts)
        duration_h = round(duration_s / 3600.0, 2)

        # Proportional funding estimate
        funding_paid = 0.0
        if total_duration > 0 and total_funding != 0:
            funding_paid = round(total_funding * duration_s / total_duration, 4)

        breakdown.append({
            "entry_ts": p.entry_ts,
            "exit_ts": p.exit_ts,
            "side": "long" if p.size > 0 else "short",
            "size": abs(p.size),
            "entry_price": p.entry_price,
            "exit_price": p.exit_price,
            "pnl": round(p.pnl, 4),
            "duration_h": duration_h,
            "funding_paid": funding_paid,
        })

    return breakdown
