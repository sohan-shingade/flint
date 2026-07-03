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
from .ml import (
    FutureWindow,
    MLContext,
    MLEngineStrategy,
    MLStrategy,
    build_training_set,
)
from .tick import (
    TickEngineStrategy,
    TickStrategy,
    overridden_handlers,
)
from .model_store import (
    ModelStore,
    ModelStoreError,
    ModelStoreQuota,
    ModelStoreQuotaError,
    ModelNotFound,
    UnsafeModelError,
)
from .templates import (
    TemplateSpec,
    get_template,
    list_templates,
    template_names,
)

__all__ = [
    "Strategy",
    "Signal",
    "EngineStrategy",
    "StrategyRejection",
    "UnknownParamError",
    "EXECUTABLE_VENUES",
    "normalize_signals",
    # ML surface (§8.5)
    "MLStrategy",
    "MLEngineStrategy",
    "MLContext",
    "FutureWindow",
    "build_training_set",
    # tick-native surface (§8.6)
    "TickStrategy",
    "TickEngineStrategy",
    "overridden_handlers",
    "ModelStore",
    "ModelStoreQuota",
    "ModelStoreError",
    "ModelStoreQuotaError",
    "ModelNotFound",
    "UnsafeModelError",
    # template registry (§8.4) — the single source of truth surfaces read
    "TemplateSpec",
    "get_template",
    "list_templates",
    "template_names",
]
