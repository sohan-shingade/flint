"""research — trust tooling: walk-forward (+ purge/embargo), Deflated Sharpe, look-ahead linter, Optuna optimize, tearsheet, Run Library (§11, §17)."""

from __future__ import annotations

from .walkforward import (
    BacktestRunner,
    ParamSpace,
    WalkForwardConfig,
    WalkForwardResult,
    WalkForwardWarning,
    Window,
    WindowResult,
    plan_windows,
    run_walk_forward,
)

__all__ = [
    "BacktestRunner",
    "ParamSpace",
    "WalkForwardConfig",
    "WalkForwardResult",
    "WalkForwardWarning",
    "Window",
    "WindowResult",
    "plan_windows",
    "run_walk_forward",
]
