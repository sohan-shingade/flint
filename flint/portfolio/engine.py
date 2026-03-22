"""Portfolio engine — run multiple strategies and aggregate results."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..backtest.engine import BacktestEngine, _max_drawdown, _sharpe_ratio
from ..models import BacktestResult, Candle
from ..strategy.base import Strategy
from .allocator import Allocator, EqualWeightAllocator


@dataclass
class PortfolioResult:
    """Aggregated results from a multi-strategy backtest."""
    total_pnl: float
    sharpe_ratio: float
    max_drawdown: float
    combined_equity: List[float] = field(default_factory=list)
    allocations: Dict[str, float] = field(default_factory=dict)
    per_strategy: Dict[str, BacktestResult] = field(default_factory=dict)
    correlation_matrix: Dict[str, Dict[str, float]] = field(default_factory=dict)


class PortfolioEngine:
    """Run multiple strategies on the same candles with capital allocation."""

    def __init__(
        self,
        strategies: List[Tuple[str, Strategy]],
        initial_capital: float = 10_000.0,
        fee_rate: float = 0.0005,
        allocator: Optional[Allocator] = None,
    ):
        self.strategies = strategies  # [(name, strategy), ...]
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.allocator = allocator or EqualWeightAllocator()

    def run(self, candles: List[Candle]) -> PortfolioResult:
        """Run all strategies and aggregate results."""
        if not candles or not self.strategies:
            return PortfolioResult(total_pnl=0, sharpe_ratio=0, max_drawdown=0)

        names = [name for name, _ in self.strategies]

        # Phase 1: Run each strategy independently with equal capital to get
        # volatility estimates for allocation
        prelim_results = {}
        equal_alloc = 1.0 / len(self.strategies)
        for name, strategy in self.strategies:
            capital = self.initial_capital * equal_alloc
            engine = BacktestEngine(strategy, capital, self.fee_rate)
            result = engine.run(candles)
            prelim_results[name] = result

        # Phase 2: Compute allocations based on preliminary results
        allocations = self.allocator.allocate(names, prelim_results)

        # Phase 3: Re-run with actual allocations
        final_results = {}
        for name, strategy in self.strategies:
            weight = allocations.get(name, 0)
            capital = self.initial_capital * weight
            if capital <= 0:
                continue
            strategy.reset()
            engine = BacktestEngine(strategy, capital, self.fee_rate)
            result = engine.run(candles)
            final_results[name] = result

        # Phase 4: Combine equity curves
        n_candles = len(candles)
        combined = [0.0] * n_candles
        for name, result in final_results.items():
            for i, eq in enumerate(result.equity_curve):
                if i < n_candles:
                    combined[i] += eq

        # Fill any gaps (strategies with fewer equity points)
        for i in range(n_candles):
            if combined[i] == 0:
                combined[i] = combined[i - 1] if i > 0 else self.initial_capital

        total_pnl = combined[-1] - self.initial_capital if combined else 0
        sharpe = _sharpe_ratio(combined)
        max_dd = _max_drawdown(combined)

        # Correlation matrix between strategy returns
        correlation = self._compute_correlations(final_results)

        return PortfolioResult(
            total_pnl=total_pnl,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            combined_equity=combined,
            allocations=allocations,
            per_strategy=final_results,
            correlation_matrix=correlation,
        )

    def _compute_correlations(
        self, results: Dict[str, BacktestResult]
    ) -> Dict[str, Dict[str, float]]:
        """Compute pairwise return correlations between strategies."""
        names = list(results.keys())
        if len(names) < 2:
            return {}

        returns_map = {}
        min_len = float("inf")
        for name in names:
            eq = results[name].equity_curve
            if len(eq) < 2:
                return {}
            arr = np.array(eq)
            rets = np.diff(arr) / arr[:-1]
            returns_map[name] = rets
            min_len = min(min_len, len(rets))

        # Truncate to same length
        min_len = int(min_len)
        matrix = {}
        for a in names:
            matrix[a] = {}
            for b in names:
                ra = returns_map[a][:min_len]
                rb = returns_map[b][:min_len]
                if np.std(ra) == 0 or np.std(rb) == 0:
                    matrix[a][b] = 0.0
                else:
                    matrix[a][b] = round(float(np.corrcoef(ra, rb)[0, 1]), 4)
        return matrix
