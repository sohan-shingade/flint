"""services — the front door; every function takes TenantContext (§4, §17).

Surfaces (api/sdk/mcp) drive the domain only through here — never into the engine
or storage directly. Phase 1 ships the backtest orchestration as a no-op walking
skeleton; paper/data/journal/optimize services land in later phases.
"""

from __future__ import annotations

from .backtest import BacktestRequest, BacktestResult, run_backtest

__all__ = [
    "run_backtest",
    "BacktestRequest",
    "BacktestResult",
]
