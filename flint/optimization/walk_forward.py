"""Walk-forward analysis — rolling train/test optimization with overfitting detection.

Splits data into N overlapping windows, optimizes on each train portion,
validates on out-of-sample test portion, and measures parameter stability.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Type

from ..backtest.engine import BacktestEngine
from ..models import Candle
from ..strategy.base import Strategy
from .optimizer import StrategyOptimizer

logger = logging.getLogger("flint.optimization")


@dataclass
class WindowResult:
    window_idx: int
    train_start_ts: int
    train_end_ts: int
    test_start_ts: int
    test_end_ts: int
    best_params: Dict[str, Any]
    in_sample_sharpe: float
    in_sample_pnl: float
    out_of_sample_sharpe: float
    out_of_sample_pnl: float
    out_of_sample_trades: int


@dataclass
class WalkForwardResult:
    strategy_name: str
    metric: str
    n_windows: int
    windows: List[WindowResult] = field(default_factory=list)
    avg_is_sharpe: float = 0.0
    avg_oos_sharpe: float = 0.0
    avg_is_pnl: float = 0.0
    avg_oos_pnl: float = 0.0
    overfitting_ratio: float = 0.0  # IS Sharpe / OOS Sharpe — >2 is suspicious
    parameter_stability: Dict[str, float] = field(default_factory=dict)  # param → coeff of variation


def run_walk_forward(
    strategy_cls: Type[Strategy],
    candles: List[Candle],
    n_windows: int = 5,
    train_pct: float = 0.7,
    metric: str = "sharpe_ratio",
    trials_per_window: int = 30,
    initial_capital: float = 10_000.0,
    fee_rate: float = 0.0005,
) -> WalkForwardResult:
    """Run walk-forward analysis.

    Splits candles into overlapping windows. For each window:
    1. Optimize parameters on the training portion
    2. Evaluate out-of-sample on the test portion
    3. Track parameter stability across windows

    Returns aggregated results with overfitting detection.
    """
    total = len(candles)
    step_size = total // (n_windows + 1)
    window_size = int(total * 0.6)  # each window sees 60% of data

    windows: List[WindowResult] = []
    param_history: Dict[str, List[float]] = {}

    for i in range(n_windows):
        start = i * step_size
        end = min(start + window_size, total)
        if end - start < 30:
            continue

        window_candles = candles[start:end]
        split_idx = int(len(window_candles) * train_pct)
        train = window_candles[:split_idx]
        test = window_candles[split_idx:]

        if len(train) < 15 or len(test) < 10:
            continue

        # Optimize on train
        try:
            opt = StrategyOptimizer(
                strategy_cls, train,
                metric=metric,
                n_trials=trials_per_window,
                initial_capital=initial_capital,
                fee_rate=fee_rate,
            )
            opt_result = opt.optimize()
        except ValueError:
            continue

        # Evaluate on test
        try:
            strategy = strategy_cls(**opt_result.best_params)
        except (TypeError, ValueError):
            continue

        engine = BacktestEngine(strategy, initial_capital, fee_rate)
        test_result = engine.run(test)

        # Also run on train to get IS metrics
        strategy.reset()
        engine_is = BacktestEngine(strategy, initial_capital, fee_rate)
        train_result = engine_is.run(train)

        wr = WindowResult(
            window_idx=i,
            train_start_ts=train[0].ts,
            train_end_ts=train[-1].ts,
            test_start_ts=test[0].ts,
            test_end_ts=test[-1].ts,
            best_params=opt_result.best_params,
            in_sample_sharpe=train_result.sharpe_ratio,
            in_sample_pnl=train_result.total_pnl,
            out_of_sample_sharpe=test_result.sharpe_ratio,
            out_of_sample_pnl=test_result.total_pnl,
            out_of_sample_trades=test_result.total_trades,
        )
        windows.append(wr)

        # Track parameter values for stability analysis
        for param_name, param_val in opt_result.best_params.items():
            if isinstance(param_val, (int, float)):
                if param_name not in param_history:
                    param_history[param_name] = []
                param_history[param_name].append(float(param_val))

    if not windows:
        return WalkForwardResult(
            strategy_name=strategy_cls.__name__,
            metric=metric,
            n_windows=0,
        )

    # Compute aggregates
    avg_is_sharpe = sum(w.in_sample_sharpe for w in windows) / len(windows)
    avg_oos_sharpe = sum(w.out_of_sample_sharpe for w in windows) / len(windows)
    avg_is_pnl = sum(w.in_sample_pnl for w in windows) / len(windows)
    avg_oos_pnl = sum(w.out_of_sample_pnl for w in windows) / len(windows)

    # Overfitting ratio: IS/OOS Sharpe. > 2.0 is suspicious
    overfitting_ratio = (
        avg_is_sharpe / avg_oos_sharpe
        if avg_oos_sharpe != 0 else float("inf")
    )

    # Parameter stability: coefficient of variation per param
    param_stability = {}
    for name, values in param_history.items():
        if len(values) >= 2:
            mean = sum(values) / len(values)
            if mean != 0:
                variance = sum((v - mean) ** 2 for v in values) / len(values)
                cv = (variance ** 0.5) / abs(mean)
                param_stability[name] = round(cv, 4)

    return WalkForwardResult(
        strategy_name=strategy_cls.__name__,
        metric=metric,
        n_windows=len(windows),
        windows=windows,
        avg_is_sharpe=avg_is_sharpe,
        avg_oos_sharpe=avg_oos_sharpe,
        avg_is_pnl=avg_is_pnl,
        avg_oos_pnl=avg_oos_pnl,
        overfitting_ratio=overfitting_ratio,
        parameter_stability=param_stability,
    )
