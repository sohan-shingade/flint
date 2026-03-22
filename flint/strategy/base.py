"""Strategy abstract base class.

v0.2: Strategies receive an optional ExecutionContext (ctx) parameter.
- v1 strategies: return Signal from on_candle (backwards compatible)
- v2 strategies: use ctx.market_order(), ctx.limit_order(), etc.
"""
from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Dict, List, Optional

from ..models import Candle, Signal

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext


class Strategy(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self) -> str:
        ...

    @abc.abstractmethod
    def on_candle(
        self,
        candle: Candle,
        history: List[Candle],
        ctx: Optional["ExecutionContext"] = None,
    ) -> Signal:
        """Process a new candle and return a trading signal.

        v1 (Signal-based): Return BUY/SELL/HOLD. The engine handles execution.
        v2 (Context-based): Use ctx.market_order() etc. Return HOLD.
        """
        ...

    @abc.abstractmethod
    def reset(self) -> None:
        """Reset internal state for a fresh run."""
        ...

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        """Define optimizable parameters for hyperparameter search.

        Override to enable Optuna integration. Example::

            @classmethod
            def parameters(cls) -> dict:
                return {
                    "fast_period": {"type": "int", "low": 5, "high": 50},
                    "slow_period": {"type": "int", "low": 20, "high": 200},
                }

        Returns empty dict by default (no optimization).
        """
        return {}
