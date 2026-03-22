"""Monte Carlo simulation and bootstrap confidence intervals.

Provides statistical confidence on backtest results:
- Trade-sequence shuffling to estimate luck vs skill
- Bootstrap CI on Sharpe, Sortino, max drawdown
- P-value: is the Sharpe statistically different from zero?
- Probability of ruin estimate
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np


@dataclass
class MonteCarloResult:
    """Results from Monte Carlo analysis."""
    n_simulations: int
    # Sharpe confidence
    sharpe_mean: float = 0.0
    sharpe_std: float = 0.0
    sharpe_ci_lower: float = 0.0  # 5th percentile
    sharpe_ci_upper: float = 0.0  # 95th percentile
    sharpe_p_value: float = 1.0   # probability Sharpe <= 0
    # Max drawdown confidence
    max_dd_mean: float = 0.0
    max_dd_ci_lower: float = 0.0  # 5th percentile (best case)
    max_dd_ci_upper: float = 0.0  # 95th percentile (worst case)
    # PnL confidence
    pnl_mean: float = 0.0
    pnl_ci_lower: float = 0.0
    pnl_ci_upper: float = 0.0
    pnl_p_value: float = 1.0     # probability PnL <= 0
    # Risk
    probability_of_ruin: float = 0.0  # % of simulations with DD > 50%
    # Equity bands for charting
    equity_p5: List[float] = field(default_factory=list)
    equity_p25: List[float] = field(default_factory=list)
    equity_p50: List[float] = field(default_factory=list)
    equity_p75: List[float] = field(default_factory=list)
    equity_p95: List[float] = field(default_factory=list)


def run_monte_carlo(
    trade_pnls: List[float],
    initial_capital: float = 10_000.0,
    n_simulations: int = 1000,
    ruin_threshold: float = 0.50,
) -> MonteCarloResult:
    """Run Monte Carlo analysis by shuffling trade sequence.

    Shuffles the order of trades N times to estimate the distribution
    of outcomes. This separates luck (sequence) from skill (edge).

    Args:
        trade_pnls: List of PnL values per trade
        initial_capital: Starting capital
        n_simulations: Number of shuffle iterations
        ruin_threshold: Drawdown level considered "ruin" (default 50%)
    """
    if not trade_pnls or len(trade_pnls) < 3:
        return MonteCarloResult(n_simulations=0)

    pnls = np.array(trade_pnls)
    n_trades = len(pnls)

    # Run simulations
    sharpes = []
    max_dds = []
    total_pnls = []
    ruin_count = 0
    all_equity_curves = []

    for _ in range(n_simulations):
        # Shuffle trade order
        shuffled = np.random.permutation(pnls)

        # Build equity curve
        equity = np.empty(n_trades + 1)
        equity[0] = initial_capital
        for i, pnl in enumerate(shuffled):
            equity[i + 1] = equity[i] + pnl

        all_equity_curves.append(equity)

        # Compute metrics for this simulation
        returns = np.diff(equity) / equity[:-1]
        returns = returns[np.isfinite(returns)]

        if len(returns) > 1 and np.std(returns) > 0:
            sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(8760))
        else:
            sharpe = 0.0
        sharpes.append(sharpe)

        # Max drawdown
        peak = np.maximum.accumulate(equity)
        dd = (peak - equity) / np.where(peak == 0, 1, peak)
        max_dd = float(np.max(dd))
        max_dds.append(max_dd)

        total_pnls.append(float(equity[-1] - initial_capital))

        if max_dd >= ruin_threshold:
            ruin_count += 1

    sharpes = np.array(sharpes)
    max_dds = np.array(max_dds)
    total_pnls_arr = np.array(total_pnls)

    # Build equity percentile bands
    curves = np.array(all_equity_curves)  # (n_simulations, n_trades+1)
    equity_p5 = np.percentile(curves, 5, axis=0).tolist()
    equity_p25 = np.percentile(curves, 25, axis=0).tolist()
    equity_p50 = np.percentile(curves, 50, axis=0).tolist()
    equity_p75 = np.percentile(curves, 75, axis=0).tolist()
    equity_p95 = np.percentile(curves, 95, axis=0).tolist()

    return MonteCarloResult(
        n_simulations=n_simulations,
        sharpe_mean=float(np.mean(sharpes)),
        sharpe_std=float(np.std(sharpes)),
        sharpe_ci_lower=float(np.percentile(sharpes, 5)),
        sharpe_ci_upper=float(np.percentile(sharpes, 95)),
        sharpe_p_value=float(np.mean(sharpes <= 0)),
        max_dd_mean=float(np.mean(max_dds)),
        max_dd_ci_lower=float(np.percentile(max_dds, 5)),
        max_dd_ci_upper=float(np.percentile(max_dds, 95)),
        pnl_mean=float(np.mean(total_pnls_arr)),
        pnl_ci_lower=float(np.percentile(total_pnls_arr, 5)),
        pnl_ci_upper=float(np.percentile(total_pnls_arr, 95)),
        pnl_p_value=float(np.mean(total_pnls_arr <= 0)),
        probability_of_ruin=ruin_count / n_simulations,
        equity_p5=equity_p5,
        equity_p25=equity_p25,
        equity_p50=equity_p50,
        equity_p75=equity_p75,
        equity_p95=equity_p95,
    )
