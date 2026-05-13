"""Genetic algorithm optimizer — evolutionary hyperparameter search.

Same interface as StrategyOptimizer but uses population-based evolutionary
search instead of Optuna's TPE. Implements tournament selection, mixed
crossover (blend for floats, uniform for ints), per-gene mutation, and
elitism. Inspired by the DEAP GA pattern.
"""
from __future__ import annotations

import copy
import logging
import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type, Union

from ..backtest.engine import BacktestEngine
from ..execution.fill_models import FillModel
from ..models import Candle
from ..strategy.base import Strategy
from .optimizer import OptimizationResult, TrialResult

logger = logging.getLogger("flint.optimization.ga")


@dataclass
class GAConfig:
    pop_size: int = 20
    n_generations: int = 30
    cx_prob: float = 0.5
    mut_prob: float = 0.3
    mut_gene_prob: float = 0.2
    tournament_size: int = 3
    elite_pct: float = 0.2


@dataclass
class _Individual:
    params: Dict[str, Any]
    fitness: Optional[float] = None

    def clone(self) -> _Individual:
        return _Individual(params=dict(self.params), fitness=self.fitness)


class GAOptimizer:
    """Optimizes strategy parameters using a genetic algorithm."""

    def __init__(
        self,
        strategy_cls: Type[Strategy],
        candles: Union[List[Candle], Dict[str, List[Candle]]],
        metric: str = "sharpe_ratio",
        ga_config: Optional[GAConfig] = None,
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
        self.cfg = ga_config or GAConfig()
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

    # ── Gene-level operations ──────────────────────────────────────────────

    def _random_params(self) -> Dict[str, Any]:
        params = {}
        for name, spec in self._param_defs.items():
            ptype = spec.get("type", "float")
            lo, hi = spec.get("low", 0), spec.get("high", 1)
            if ptype == "int":
                params[name] = random.randint(lo, hi)
            elif ptype == "float":
                params[name] = round(random.uniform(lo, hi), 6)
            elif ptype == "categorical":
                params[name] = random.choice(spec["choices"])
        return params

    def _clamp(self, params: Dict[str, Any]) -> Dict[str, Any]:
        clamped = {}
        for name, spec in self._param_defs.items():
            val = params.get(name)
            if val is None:
                clamped[name] = spec.get("default", spec.get("low", 0))
                continue
            ptype = spec.get("type", "float")
            if ptype == "int":
                clamped[name] = max(spec["low"], min(spec["high"], int(round(val))))
            elif ptype == "float":
                clamped[name] = round(max(spec["low"], min(spec["high"], float(val))), 6)
            else:
                clamped[name] = val
        return clamped

    def _crossover(self, p1: _Individual, p2: _Individual) -> tuple[_Individual, _Individual]:
        """Mixed crossover: blend for floats, uniform swap for ints."""
        c1_params = dict(p1.params)
        c2_params = dict(p2.params)

        for name, spec in self._param_defs.items():
            ptype = spec.get("type", "float")
            if ptype == "float":
                alpha = 0.3
                v1, v2 = p1.params[name], p2.params[name]
                lo_b = min(v1, v2) - alpha * abs(v1 - v2)
                hi_b = max(v1, v2) + alpha * abs(v1 - v2)
                c1_params[name] = round(random.uniform(lo_b, hi_b), 6)
                c2_params[name] = round(random.uniform(lo_b, hi_b), 6)
            elif ptype == "int":
                if random.random() < 0.5:
                    c1_params[name], c2_params[name] = c2_params[name], c1_params[name]
            elif ptype == "categorical":
                if random.random() < 0.5:
                    c1_params[name], c2_params[name] = c2_params[name], c1_params[name]

        return (
            _Individual(params=self._clamp(c1_params)),
            _Individual(params=self._clamp(c2_params)),
        )

    def _mutate(self, ind: _Individual) -> _Individual:
        """Per-gene mutation: Gaussian for floats, random for ints, flip for booleans."""
        params = dict(ind.params)

        for name, spec in self._param_defs.items():
            if random.random() > self.cfg.mut_gene_prob:
                continue
            ptype = spec.get("type", "float")
            lo, hi = spec.get("low", 0), spec.get("high", 1)
            if ptype == "float":
                sigma = (hi - lo) * 0.1
                params[name] = params[name] + random.gauss(0, sigma)
            elif ptype == "int":
                if hi - lo <= 1:
                    params[name] = lo if params[name] == hi else hi
                else:
                    params[name] = random.randint(lo, hi)
            elif ptype == "categorical":
                params[name] = random.choice(spec["choices"])

        return _Individual(params=self._clamp(params))

    def _tournament_select(self, pop: List[_Individual], k: int) -> _Individual:
        candidates = random.sample(pop, min(k, len(pop)))
        return max(candidates, key=lambda ind: ind.fitness or float("-inf"))

    # ── Fitness evaluation ─────────────────────────────────────────────────

    def _evaluate(self, ind: _Individual, train_candles) -> float:
        try:
            strategy = self.strategy_cls(**ind.params)
        except (TypeError, ValueError) as e:
            logger.debug("Invalid params %s: %s", ind.params, e)
            return float("-inf")

        if isinstance(train_candles, dict):
            _primary = max(train_candles.keys(), key=lambda k: len(train_candles[k]))
            _primary_list = train_candles[_primary]
            ts_start = _primary_list[0].ts if _primary_list else 0
            ts_end = _primary_list[-1].ts if _primary_list else 0
        else:
            ts_start = train_candles[0].ts if train_candles else 0
            ts_end = train_candles[-1].ts if train_candles else 0
        funding = [f for f in self._funding_rates if ts_start <= f.ts <= ts_end]
        orderbooks = [o for o in self._orderbook_snapshots if ts_start <= o.ts <= ts_end]
        oi = [o for o in self._open_interest if ts_start <= o.ts <= ts_end]

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

        return self._get_metric(result)

    def _get_metric(self, result) -> float:
        mapping = {
            "sharpe_ratio": result.sharpe_ratio,
            "total_pnl": result.total_pnl,
            "win_rate": result.win_rate,
            "max_drawdown": -result.max_drawdown,
            "calmar": (result.total_pnl / result.max_drawdown
                       if result.max_drawdown > 0 else 0),
        }
        return mapping.get(self.metric, result.sharpe_ratio)

    def _evaluate_full(self, ind: _Individual, train_candles) -> Optional[TrialResult]:
        """Full evaluation returning TrialResult for reporting."""
        try:
            strategy = self.strategy_cls(**ind.params)
        except (TypeError, ValueError):
            return None

        if isinstance(train_candles, dict):
            _primary = max(train_candles.keys(), key=lambda k: len(train_candles[k]))
            _primary_list = train_candles[_primary]
            ts_start = _primary_list[0].ts if _primary_list else 0
            ts_end = _primary_list[-1].ts if _primary_list else 0
        else:
            ts_start = train_candles[0].ts if train_candles else 0
            ts_end = train_candles[-1].ts if train_candles else 0
        funding = [f for f in self._funding_rates if ts_start <= f.ts <= ts_end]
        orderbooks = [o for o in self._orderbook_snapshots if ts_start <= o.ts <= ts_end]
        oi = [o for o in self._open_interest if ts_start <= o.ts <= ts_end]

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
            return None

        value = self._get_metric(result)
        return TrialResult(
            params=ind.params,
            metric_value=value,
            total_pnl=result.total_pnl,
            sharpe_ratio=result.sharpe_ratio,
            max_drawdown=result.max_drawdown,
            win_rate=result.win_rate,
            total_trades=result.total_trades,
        )

    # ── Main GA loop ───────────────────────────────────────────────────────

    def optimize(self) -> OptimizationResult:
        """Run GA optimization. Returns best params and all evaluated trials."""
        if isinstance(self.candles, dict):
            primary = max(self.candles.keys(), key=lambda k: len(self.candles[k]))
            split = int(len(self.candles[primary]) * self.train_ratio)
            train_candles = {k: v[:split] for k, v in self.candles.items()}
        else:
            split = int(len(self.candles) * self.train_ratio)
            train_candles = self.candles[:split]

        cfg = self.cfg
        all_trials: List[TrialResult] = []

        # Initialize population
        population = [_Individual(params=self._random_params()) for _ in range(cfg.pop_size)]

        best_ever: Optional[_Individual] = None
        convergence: List[list] = []
        total_evals = 0

        for gen in range(cfg.n_generations):
            # Evaluate individuals without fitness
            for ind in population:
                if ind.fitness is None:
                    ind.fitness = self._evaluate(ind, train_candles)
                    total_evals += 1

            # Track best
            gen_best = max(population, key=lambda x: x.fitness or float("-inf"))
            if best_ever is None or (gen_best.fitness or float("-inf")) > (best_ever.fitness or float("-inf")):
                best_ever = gen_best.clone()

            best_val = best_ever.fitness if best_ever.fitness is not None else None
            convergence.append([total_evals, round(best_val, 4) if best_val is not None and best_val > float("-inf") else None])

            logger.info(
                "Gen %d: best=%.4f, gen_best=%.4f, evals=%d",
                gen, best_val or 0, gen_best.fitness or 0, total_evals,
            )

            # Elitism: keep top N
            n_elite = max(1, int(cfg.pop_size * cfg.elite_pct))
            sorted_pop = sorted(population, key=lambda x: x.fitness or float("-inf"), reverse=True)
            elites = [ind.clone() for ind in sorted_pop[:n_elite]]

            # Selection + reproduction
            offspring: List[_Individual] = []
            while len(offspring) < cfg.pop_size - n_elite:
                p1 = self._tournament_select(population, cfg.tournament_size)
                p2 = self._tournament_select(population, cfg.tournament_size)

                if random.random() < cfg.cx_prob:
                    c1, c2 = self._crossover(p1, p2)
                else:
                    c1, c2 = p1.clone(), p2.clone()

                if random.random() < cfg.mut_prob:
                    c1 = self._mutate(c1)
                if random.random() < cfg.mut_prob:
                    c2 = self._mutate(c2)

                c1.fitness = None
                c2.fitness = None
                offspring.append(c1)
                if len(offspring) < cfg.pop_size - n_elite:
                    offspring.append(c2)

            population = elites + offspring

        # Final evaluation of best for full metrics
        if best_ever:
            trial = self._evaluate_full(best_ever, train_candles)
            if trial:
                all_trials.append(trial)

        # Also get full metrics for top elites
        sorted_final = sorted(population, key=lambda x: x.fitness or float("-inf"), reverse=True)
        for ind in sorted_final[:min(20, len(sorted_final))]:
            if ind.params == (best_ever.params if best_ever else None):
                continue
            trial = self._evaluate_full(ind, train_candles)
            if trial:
                all_trials.append(trial)

        all_trials.sort(key=lambda t: t.metric_value, reverse=True)

        best_params = best_ever.params if best_ever else {}
        best_value = best_ever.fitness if best_ever and best_ever.fitness is not None else float("-inf")

        result = OptimizationResult(
            best_params=best_params,
            best_value=best_value,
            metric=self.metric,
            n_trials=total_evals,
            trials=all_trials,
        )
        result.convergence = convergence  # type: ignore[attr-defined]
        return result
