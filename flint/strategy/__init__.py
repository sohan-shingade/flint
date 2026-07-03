"""strategy — the user's surface: Strategy base class, Signal model, read-only ctx, the OS-isolated sandbox, and templates (§8, §17).

The public surface a strategy author touches: :class:`Strategy` (subclass +
``params`` + ``on_candle``) and :class:`~flint.core.models.Signal` (the declared
intent). :class:`EngineStrategy` is the internal adapter the runner wraps a
strategy in before handing it to the engine (§8.1, D28); :class:`StrategyRejection`
is the structured record of a signal refused before the fill path (§19.1).
"""

from __future__ import annotations

from flint.core.models import Signal

from .base import (
    EXECUTABLE_VENUES,
    EngineStrategy,
    Strategy,
    StrategyRejection,
    UnknownParamError,
    normalize_signals,
)

__all__ = [
    "Strategy",
    "Signal",
    "EngineStrategy",
    "StrategyRejection",
    "UnknownParamError",
    "EXECUTABLE_VENUES",
    "normalize_signals",
]
