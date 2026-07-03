"""engine — the simulator/executor shared by backtest and paper.

Pure domain logic: it reaches infrastructure only through ports and consumes
market data as already-loaded in-memory fixtures, never doing I/O itself (§4,
§6, §17). Slice 3.1 ships the per-bar loop (``BacktestEngine``) and the state,
fill, funding, and liquidation primitives it composes.
"""

from __future__ import annotations

from .api import EngineFeed, EngineRunSpec, SimulationEngine
from .context import AccountView, OpenInterestSnapshot
from .loop import (
    BacktestEngine,
    EngineConfig,
    EngineContext,
    NoopStrategy,
    SignalValidationError,
    Strategy,
)
from .money import ZERO, Money, money
from .orders import OrderRecord, OrderStatus
from .portfolio import BookState, fold
from .select import LegacyBarEngine, UnknownEngineError, engine_for
from .state import Account, PortfolioState
from .tearsheet import InvariantError, Tearsheet, build_tearsheet, check_invariants

__all__ = [
    "BacktestEngine",
    "EngineConfig",
    "EngineContext",
    "Strategy",
    "NoopStrategy",
    "PortfolioState",
    "Account",
    "AccountView",
    "OpenInterestSnapshot",
    "OrderRecord",
    "OrderStatus",
    "SignalValidationError",
    "BookState",
    "fold",
    "Tearsheet",
    "build_tearsheet",
    "check_invariants",
    "InvariantError",
    "Money",
    "money",
    "ZERO",
    # The engine seam (§6.0, D29) — substrate-agnostic run contract + dispatch.
    "EngineFeed",
    "EngineRunSpec",
    "SimulationEngine",
    "engine_for",
    "LegacyBarEngine",
    "UnknownEngineError",
]
