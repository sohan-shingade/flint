"""``SecretsPort`` — fetch an API key without knowing where it is stored (§2.7).

Laptop: read a server-side ``.env``. Cloud: KMS / a vault. Secrets are
per-tenant and never cross the sandbox boundary — a user strategy cannot reach
this port (§8.3); only the trusted host process resolves secrets, e.g. when a
live executor needs a venue key.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .tenant import TenantContext


class SecretsPort(ABC):
    """Resolve a named secret for a tenant, or ``None`` if it is unset."""

    @abstractmethod
    def get_secret(self, tenant: TenantContext, name: str) -> str | None:
        """Return the secret ``name`` for ``tenant``, or ``None`` if absent."""
        ...
