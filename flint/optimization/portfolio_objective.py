"""Portfolio-level Optuna objective — Phase 6 T6.3.

Single-strategy Sharpe optimization rewards correlated winners equally
with uncorrelated ones. When running a book, that's the wrong metric —
concentrating bets in one factor looks great in-sample and collapses
out-of-sample. The portfolio objective subtracts a pairwise-correlation
penalty from Sharpe.

Usage:
    import optuna
    from flint.optimization.portfolio_objective import portfolio_objective

    def objective(trial):
        return portfolio_objective(trial, strategies_fn=build_book,
                                   runner=run_backtest,
                                   penalty_lambda=0.5)

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=50)
"""
from __future__ import annotations

from typing import Callable, Dict, List, Sequence

import numpy as np


def pairwise_correlations(
    equity_curves: Sequence[Sequence[float]],
) -> float:
    """Mean pairwise correlation of per-strategy equity-curve returns.

    Returns 0 if fewer than 2 curves or too few points. Bounded [-1, 1].
    """
    curves = [np.asarray(c, dtype=float) for c in equity_curves if len(c) >= 3]
    if len(curves) < 2:
        return 0.0

    # Align lengths (truncate to shortest)
    min_len = min(len(c) for c in curves)
    returns = []
    for c in curves:
        r = np.diff(c[:min_len]) / np.maximum(np.abs(c[:min_len - 1]), 1e-9)
        r = r[np.isfinite(r)]
        if len(r) < 2:
            return 0.0
        returns.append(r)

    n = len(returns)
    total = 0.0
    pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            # Truncate to common length (returns already aligned but be safe)
            m = min(len(returns[i]), len(returns[j]))
            a, b = returns[i][:m], returns[j][:m]
            if a.std() == 0 or b.std() == 0:
                continue
            c = float(np.corrcoef(a, b)[0, 1])
            if np.isfinite(c):
                total += c
                pairs += 1
    return total / pairs if pairs else 0.0


def portfolio_objective(
    trial,
    strategies_fn: Callable[[object], List],
    runner: Callable[[List], Dict],
    penalty_lambda: float = 0.5,
    correlation_floor: float = 0.3,
) -> float:
    """Optuna objective — `portfolio_sharpe - penalty_lambda * excess_corr`.

    Args:
        trial: Optuna trial (used by `strategies_fn` to sample params).
        strategies_fn: builds a list of strategy instances for this trial.
        runner: takes the strategy list, runs portfolio backtest, returns
            a dict with keys `portfolio_sharpe` and `equity_curves`.
        penalty_lambda: weight on excess correlation. 0 disables penalty.
        correlation_floor: correlations below this level incur no penalty;
            above, penalty scales linearly with (corr - floor).

    Return value is the score Optuna maximizes.
    """
    strategies = strategies_fn(trial)
    result = runner(strategies)

    sharpe = float(result.get("portfolio_sharpe", 0.0))
    curves = result.get("equity_curves") or []
    avg_corr = pairwise_correlations(curves)
    excess = max(0.0, avg_corr - correlation_floor)
    return sharpe - penalty_lambda * excess


def score(
    portfolio_sharpe: float,
    equity_curves: Sequence[Sequence[float]],
    penalty_lambda: float = 0.5,
    correlation_floor: float = 0.3,
) -> float:
    """Standalone scoring helper — same formula as `portfolio_objective`
    but without Optuna. Useful for post-hoc portfolio ranking."""
    avg_corr = pairwise_correlations(equity_curves)
    excess = max(0.0, avg_corr - correlation_floor)
    return portfolio_sharpe - penalty_lambda * excess
