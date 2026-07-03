"""Alert configuration service — register a paper/live alert rule (§6.7, §12).

``POST /api/v1/alerts`` configures one alert rule (liquidation-distance,
drift-breach, funding-spread-flip, heartbeat) with a threshold and a delivery
channel. The rule engine and channels themselves live in ``flint.live.alerts``
(built in 5.6); this service is the tenant-scoped registry of *which* rules a
tenant wants firing. A dedicated durable alerts store is v1.x (5.6 carry-forward
g) — v1 holds configs in memory on the running server, keyed per tenant.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from flint.ports import TenantContext

from .errors import ValidationError

# The rule keys the 5.6 engine knows (``flint.live.alerts.default_rules``).
KNOWN_RULES = frozenset(
    {"liq_distance", "drift_breach", "funding_spread_flip", "heartbeat"}
)
KNOWN_CHANNELS = frozenset({"webhook", "collect"})


@dataclass
class AlertConfigStore:
    """In-memory, tenant-scoped registry of configured alert rules (v1)."""

    _configs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def create(
        self,
        tenant: TenantContext,
        *,
        rule: str,
        threshold: float | None = None,
        channel: str = "collect",
    ) -> dict[str, Any]:
        """Validate + register an alert config, returning it with a fresh id."""
        if rule not in KNOWN_RULES:
            raise ValidationError(
                f"unknown alert rule {rule!r}",
                detail=f"known rules: {', '.join(sorted(KNOWN_RULES))}",
                hint="pick one of the built-in §6.7 rules",
            )
        if channel not in KNOWN_CHANNELS:
            raise ValidationError(
                f"unknown alert channel {channel!r}",
                detail=f"known channels: {', '.join(sorted(KNOWN_CHANNELS))}",
            )
        config = {
            "id": uuid.uuid4().hex,
            "rule": rule,
            "threshold": threshold,
            "channel": channel,
        }
        self._configs.setdefault(tenant.tenant_id, []).append(config)
        return config

    def list(self, tenant: TenantContext) -> list[dict[str, Any]]:
        return list(self._configs.get(tenant.tenant_id, []))
