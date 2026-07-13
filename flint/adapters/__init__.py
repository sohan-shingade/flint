"""adapters — concrete port implementations (§2.7, §17).

Phase 1 ships in-memory reference adapters (``local``) that fulfil every port on
a laptop with no network, proving the seams. The **durable** DuckDB market/user
store adapters live with the data layer: ``flint.data.store.DuckDBUserData(path)``
/ ``DuckDBMarketData(path)`` — inject those wherever runs/events must survive a
restart (e.g. a resumable paper session).
"""

from __future__ import annotations

from .local import (
    EnvSecrets,
    InMemoryEventBus,
    InMemoryMarketData,
    InMemoryUserData,
    InProcessJobRunner,
    LocalIdentity,
)

__all__ = [
    "InMemoryMarketData",
    "InMemoryUserData",
    "InProcessJobRunner",
    "EnvSecrets",
    "InMemoryEventBus",
    "LocalIdentity",
]
