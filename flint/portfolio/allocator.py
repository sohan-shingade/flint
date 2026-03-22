"""Capital allocation strategies for multi-strategy portfolios."""
from __future__ import annotations

import abc
from typing import Dict, List

from ..models import BacktestResult


class Allocator(abc.ABC):
    """Determines how capital is split across strategies."""

    @abc.abstractmethod
    def allocate(
        self,
        strategy_names: List[str],
        results: Dict[str, BacktestResult],
    ) -> Dict[str, float]:
        """Return allocation weights (sum to 1.0)."""
        ...


class EqualWeightAllocator(Allocator):
    """Equal capital to each strategy."""

    def allocate(self, strategy_names, results):
        n = len(strategy_names)
        if n == 0:
            return {}
        weight = 1.0 / n
        return {name: weight for name in strategy_names}


class InverseVolAllocator(Allocator):
    """Allocate inversely proportional to equity curve volatility.

    Lower volatility strategies get more capital.
    """

    def allocate(self, strategy_names, results):
        import numpy as np

        vols = {}
        for name in strategy_names:
            r = results.get(name)
            if r and len(r.equity_curve) > 2:
                arr = np.array(r.equity_curve)
                returns = np.diff(arr) / arr[:-1]
                vol = float(np.std(returns)) if len(returns) > 0 else 1.0
                vols[name] = max(vol, 1e-10)  # avoid div by zero
            else:
                vols[name] = 1.0

        inv_vols = {name: 1.0 / v for name, v in vols.items()}
        total = sum(inv_vols.values())
        if total == 0:
            return EqualWeightAllocator().allocate(strategy_names, results)
        return {name: iv / total for name, iv in inv_vols.items()}
