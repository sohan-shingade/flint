"""Strategy optimizer — Optuna-based hyperparameter search."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type

import optuna

from ..backtest.engine import BacktestEngine
from ..execution.fill_models import FillModel
from ..models import Candle, FundingRate
from ..strategy.base import Strategy

logger = logging.getLogger("flint.optimization")

# Silence Optuna's verbose logging
optuna.logging.set_verbosity(optuna.logging.WARNING)


@dataclass
class TrialResult:
    params: Dict[str, Any]
    metric_value: float
    total_pnl: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    total_trades: int


@dataclass
class OptimizationResult:
    best_params: Dict[str, Any]
    best_value: float
    metric: str
    n_trials: int
    trials: List[TrialResult] = field(default_factory=list)


class StrategyOptimizer:
    """Optimizes strategy parameters using Optuna."""

    def __init__(
        self,
        strategy_cls: Type[Strategy],
        candles: List[Candle],
        metric: str = "sharpe_ratio",
        n_trials: int = 50,
        initial_capital: float = 10_000.0,
        fee_rate: float = 0.0005,
        train_ratio: float = 1.0,
        fill_model: Optional[FillModel] = None,
        funding_rates: Optional[List] = None,
        orderbook_snapshots: Optional[List] = None,
        open_interest: Optional[List] = None,
        margin_engine=None,
        capital_allocator=None,
    ):
        self.strategy_cls = strategy_cls
        self.candles = candles
        self.metric = metric
        self.n_trials = n_trials
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.train_ratio = train_ratio
        self._fill_model = fill_model
        self._funding_rates = funding_rates or []
        self._orderbook_snapshots = orderbook_snapshots or []
        self._open_interest = open_interest or []
        self._margin_engine = margin_engine
        self._capital_allocator = capital_allocator

        self._param_defs = strategy_cls.parameters()
        if not self._param_defs:
            raise ValueError(f"{strategy_cls.__name__} has no parameters() defined")

    def _suggest_params(self, trial: optuna.Trial) -> Dict[str, Any]:
        """Build parameter dict from Optuna trial suggestions."""
        params = {}
        for name, spec in self._param_defs.items():
            ptype = spec.get("type", "float")
            if ptype == "int":
                params[name] = trial.suggest_int(
                    name, spec["low"], spec["high"],
                    step=spec.get("step", 1),
                )
            elif ptype == "float":
                params[name] = trial.suggest_float(
                    name, spec["low"], spec["high"],
                    step=spec.get("step"),
                )
            elif ptype == "categorical":
                params[name] = trial.suggest_categorical(name, spec["choices"])
        return params

    def _get_metric(self, result) -> float:
        """Extract the target metric from a BacktestResult."""
        mapping = {
            "sharpe_ratio": result.sharpe_ratio,
            "total_pnl": result.total_pnl,
            "win_rate": result.win_rate,
            "max_drawdown": -result.max_drawdown,  # minimize DD → negate
            "calmar": (result.total_pnl / result.max_drawdown
                       if result.max_drawdown > 0 else 0),
        }
        return mapping.get(self.metric, result.sharpe_ratio)

    def optimize(self) -> OptimizationResult:
        """Run optimization. Returns best params and all trial results."""
        split = int(len(self.candles) * self.train_ratio)
        train_candles = self.candles[:split]

        trials: List[TrialResult] = []

        def objective(trial: optuna.Trial) -> float:
            params = self._suggest_params(trial)
            try:
                strategy = self.strategy_cls(**params)
            except (TypeError, ValueError) as e:
                logger.debug("Invalid params %s: %s", params, e)
                return float("-inf")

            # Filter market data to training window
            train_start = train_candles[0].ts if train_candles else 0
            train_end = train_candles[-1].ts if train_candles else 0
            funding = [f for f in self._funding_rates if train_start <= f.ts <= train_end]
            orderbooks = [o for o in self._orderbook_snapshots if train_start <= o.ts <= train_end]
            oi = [o for o in self._open_interest if train_start <= o.ts <= train_end]

            engine = BacktestEngine(
                strategy,
                initial_capital=self.initial_capital,
                fee_rate=self.fee_rate,
                fill_model=self._fill_model,
                funding_rates=funding,
                orderbook_snapshots=orderbooks,
                open_interest=oi,
                margin_engine=self._margin_engine,
                capital_allocator=self._capital_allocator,
            )
            result = engine.run(train_candles)

            if result.total_trades < 2:
                return float("-inf")

            value = self._get_metric(result)
            trials.append(TrialResult(
                params=params,
                metric_value=value,
                total_pnl=result.total_pnl,
                sharpe_ratio=result.sharpe_ratio,
                max_drawdown=result.max_drawdown,
                win_rate=result.win_rate,
                total_trades=result.total_trades,
            ))
            return value

        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=self.n_trials, show_progress_bar=False)

        best = study.best_trial
        return OptimizationResult(
            best_params=best.params,
            best_value=best.value,
            metric=self.metric,
            n_trials=len(study.trials),
            trials=sorted(trials, key=lambda t: t.metric_value, reverse=True),
        )

    def walk_forward(
        self,
        n_splits: int = 5,
        train_pct: float = 0.7,
        trials_per_split: int = 30,
    ) -> Dict[str, Any]:
        """Walk-forward optimization with rolling train/test windows.

        Returns in-sample and out-of-sample results per window.
        """
        total = len(self.candles)
        window_size = total // n_splits
        results = []

        for i in range(n_splits):
            start = i * (window_size // 2)
            end = min(start + window_size, total)
            if end - start < 20:
                continue

            window = self.candles[start:end]
            split_idx = int(len(window) * train_pct)
            train = window[:split_idx]
            test = window[split_idx:]

            if len(train) < 10 or len(test) < 5:
                continue

            # Optimize on train
            opt = StrategyOptimizer(
                self.strategy_cls, train,
                metric=self.metric,
                n_trials=trials_per_split,
                initial_capital=self.initial_capital,
                fee_rate=self.fee_rate,
                fill_model=self._fill_model,
                funding_rates=self._funding_rates,
                orderbook_snapshots=self._orderbook_snapshots,
                open_interest=self._open_interest,
                margin_engine=self._margin_engine,
                capital_allocator=self._capital_allocator,
            )
            opt_result = opt.optimize()

            # Evaluate on test with best params
            try:
                strategy = self.strategy_cls(**opt_result.best_params)
            except (TypeError, ValueError):
                continue

            test_start = test[0].ts if test else 0
            test_end = test[-1].ts if test else 0
            test_funding = [f for f in self._funding_rates if test_start <= f.ts <= test_end]
            test_ob = [o for o in self._orderbook_snapshots if test_start <= o.ts <= test_end]
            test_oi = [o for o in self._open_interest if test_start <= o.ts <= test_end]

            engine = BacktestEngine(
                strategy, self.initial_capital, self.fee_rate,
                fill_model=self._fill_model,
                funding_rates=test_funding,
                orderbook_snapshots=test_ob,
                open_interest=test_oi,
                margin_engine=self._margin_engine,
                capital_allocator=self._capital_allocator,
            )
            test_result = engine.run(test)

            results.append({
                "window": i,
                "train_size": len(train),
                "test_size": len(test),
                "best_params": opt_result.best_params,
                "in_sample": {
                    "sharpe": opt_result.best_value if self.metric == "sharpe_ratio" else 0,
                    "pnl": opt_result.trials[0].total_pnl if opt_result.trials else 0,
                },
                "out_of_sample": {
                    "sharpe": test_result.sharpe_ratio,
                    "pnl": test_result.total_pnl,
                    "trades": test_result.total_trades,
                },
            })

        return {
            "n_splits": len(results),
            "windows": results,
            "avg_oos_sharpe": (
                sum(r["out_of_sample"]["sharpe"] for r in results) / len(results)
                if results else 0
            ),
        }
