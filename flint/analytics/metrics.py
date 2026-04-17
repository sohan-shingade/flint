"""Advanced performance metrics beyond what BacktestResult provides."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List

import numpy as np

from ..models import BacktestResult, Position


@dataclass
class MetricsSummary:
    # returns
    total_return_pct: float
    annualized_return_pct: float
    # risk
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    max_drawdown_duration_s: int
    # trades
    total_trades: int
    win_rate: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    avg_holding_period_s: float
    # pnl
    total_pnl: float
    best_trade: float
    worst_trade: float
    # volatility
    annualized_volatility_pct: float = 0.0
    # extended
    turnover_annualized: float = 0.0
    calmar_ratio: float = 0.0


def compute_metrics(
    result: BacktestResult,
    initial_capital: float = 10_000.0,
    periods_per_year: float = 8760.0,
) -> MetricsSummary:
    equity = np.array(result.equity_curve) if result.equity_curve else np.array([initial_capital])
    positions = result.positions

    # --- Returns ---
    total_return_pct = (equity[-1] / initial_capital - 1) * 100 if initial_capital else 0.0
    n_periods = len(equity)
    if n_periods > 1 and initial_capital > 0:
        total_growth = equity[-1] / initial_capital
        years = n_periods / periods_per_year
        annualized = (total_growth ** (1 / years) - 1) * 100 if years > 0 else 0.0
    else:
        annualized = 0.0

    # --- Returns array ---
    if len(equity) > 1:
        returns = np.diff(equity) / np.where(equity[:-1] == 0, 1, equity[:-1])
    else:
        returns = np.array([0.0])

    # --- Sharpe (sample std, ddof=1) ---
    ret_std = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(np.mean(returns) / ret_std * np.sqrt(periods_per_year)) if ret_std > 1e-12 else 0.0
    annualized_vol = float(ret_std * np.sqrt(periods_per_year) * 100) if ret_std > 0 else 0.0

    # --- Sortino (downside deviation over ALL returns, squaring only negatives) ---
    downside_returns = np.minimum(returns, 0)
    downside_std = float(np.sqrt(np.mean(downside_returns ** 2)))
    sortino = float(np.mean(returns) / downside_std * np.sqrt(periods_per_year)) if downside_std > 1e-12 else 0.0

    # --- Drawdown ---
    peak = np.maximum.accumulate(equity)
    dd = (peak - equity) / np.where(peak == 0, 1, peak)
    max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0

    # Drawdown duration (longest stretch below peak in periods)
    max_dd_dur_periods = _max_drawdown_duration(dd)
    # Assume 1h per period for seconds conversion
    period_s = int(365.25 * 24 * 3600 / periods_per_year)
    max_dd_dur_s = max_dd_dur_periods * period_s

    # --- Trade stats ---
    pnls = [p.pnl for p in positions]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n_trades = len(positions)
    win_rate = len(wins) / n_trades if n_trades else 0.0
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    gross_wins = sum(wins) if wins else 0.0
    gross_losses = abs(sum(losses)) if losses else 0.0
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf") if gross_wins > 0 else 0.0

    # --- Holding period ---
    holding_periods = [
        p.exit_ts - p.entry_ts for p in positions if p.closed and p.exit_ts > p.entry_ts
    ]
    avg_hold = float(np.mean(holding_periods)) if holding_periods else 0.0

    best = max(pnls) if pnls else 0.0
    worst = min(pnls) if pnls else 0.0

    # --- Turnover (annualized) ---
    notional_traded = sum(abs(f.size) * f.price for f in result.fills) if result.fills else 0.0
    final_equity = float(equity[-1]) if len(equity) > 0 else initial_capital
    years = n_periods / periods_per_year if periods_per_year > 0 else 1.0
    turnover = (notional_traded / final_equity / years) if final_equity > 0 and years > 0 else 0.0

    # --- Calmar ratio (annualized return / max drawdown) ---
    calmar = (annualized / 100) / max_dd if max_dd > 0.001 else 0.0

    return MetricsSummary(
        total_return_pct=total_return_pct,
        annualized_return_pct=annualized,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        max_drawdown=max_dd,
        max_drawdown_duration_s=max_dd_dur_s,
        total_trades=n_trades,
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_win=avg_win,
        avg_loss=avg_loss,
        avg_holding_period_s=avg_hold,
        total_pnl=result.total_pnl,
        best_trade=best,
        worst_trade=worst,
        annualized_volatility_pct=annualized_vol,
        turnover_annualized=turnover,
        calmar_ratio=calmar,
    )


def _max_drawdown_duration(dd: np.ndarray) -> int:
    """Longest consecutive stretch of non-zero drawdown (in periods)."""
    if len(dd) == 0:
        return 0
    max_dur = 0
    cur = 0
    for v in dd:
        if v > 0:
            cur += 1
            max_dur = max(max_dur, cur)
        else:
            cur = 0
    return max_dur
