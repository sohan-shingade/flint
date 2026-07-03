"""``TenantContext`` — the identity threaded through every user-scoped call (§2.8).

A *tenant* is "whose data this is." On a laptop there is a single implicit
tenant (you); in the hosted multi-user version every user-data query must be
scoped to the right ``tenant_id`` or accounts leak into each other. We thread
the context from day one — even though locally it is a constant — because
adding scoping *after* a leak has happened is how leaks happen (D-review).

Frozen and hashable so it can key a dict or live in a set (adapters use it to
partition storage).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Who a user-scoped call belongs to. Filters every ``UserDataPort`` query."""

    tenant_id: str

    @classmethod
    def local(cls) -> "TenantContext":
        """The single implicit tenant on a self-hosted/laptop install."""
        return cls(tenant_id="local")
