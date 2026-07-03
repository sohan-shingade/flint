"""engine — the simulator/executor shared by backtest and paper.

Pure domain logic: it reaches infrastructure only through ports and consumes
market data as already-loaded in-memory fixtures, never doing I/O itself (§4,
§6, §17). Slice 3.1 ships the per-bar loop (``BacktestEngine``) and the state,
fill, funding, and liquidation primitives it composes.
"""

from __future__ import annotations

from .loop import (
    BacktestEngine,
    EngineConfig,
    EngineContext,
    NoopStrategy,
    Strategy,
)
from .money import ZERO, Money, money
from .orders import OrderRecord, OrderStatus
from .portfolio import BookState, fold
from .state import Account, PortfolioState

__all__ = [
    "BacktestEngine",
    "EngineConfig",
    "EngineContext",
    "Strategy",
    "NoopStrategy",
    "PortfolioState",
    "Account",
    "OrderRecord",
    "OrderStatus",
    "BookState",
    "fold",
    "Money",
    "money",
    "ZERO",
]
