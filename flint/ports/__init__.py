"""ports — the interfaces the domain depends on instead of concrete infra (§2.7).

Six ports plus the ``TenantContext`` threaded through the user-scoped ones. The
engine imports from here, never from DuckDB/S3/Redis directly; adapters in
``flint.adapters`` fulfil these promises. See §2.7/§2.8/§17.
"""

from __future__ import annotations

from .events import EventBusPort, EventHandler
from .identity import Identity
from .jobs import JobRunnerPort, ResourceQuota
from .records import RunRecord
from .secrets import SecretsPort
from .storage import MarketDataPort, UserDataPort
from .tenant import TenantContext

__all__ = [
    "TenantContext",
    "RunRecord",
    "MarketDataPort",
    "UserDataPort",
    "JobRunnerPort",
    "ResourceQuota",
    "SecretsPort",
    "EventBusPort",
    "EventHandler",
    "Identity",
]
