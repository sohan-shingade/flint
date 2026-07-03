"""``Identity`` — resolve who the current user is (§2.7).

The one port that turns an ambient request into a ``TenantContext``. Laptop:
a single local user. Cloud: Clerk/Auth0 maps a session to a tenant. Everything
downstream takes the resulting ``TenantContext`` explicitly, so identity is
resolved once, at the edge, and never re-derived deep in the domain.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .tenant import TenantContext


class Identity(ABC):
    """Resolve the acting user into a ``TenantContext``."""

    @abstractmethod
    def current(self) -> TenantContext:
        """Return the ``TenantContext`` for the acting user."""
        ...
