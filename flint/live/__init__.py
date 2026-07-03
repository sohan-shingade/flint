"""live — the minimal v1 live executor (D20): Hyperliquid only, caps, kill switch; same code path as paper (§17).

Slice 5.6 lands the **paper** half: the :class:`~flint.live.session.PaperSession`
runner (the same engine fed by the LiveFeed, restart-safe via the event log),
the :mod:`~flint.live.drift` parity metrics (structural → alert, market → chart),
and the :mod:`~flint.live.alerts` rule engine + webhook channel. The HL live
executor (caps/kill-switch) is Phase 7 over this same runner.
"""

from __future__ import annotations

from .alerts import (
    Alert,
    AlertChannel,
    AlertContext,
    AlertEngine,
    AlertRule,
    CollectingChannel,
    DriftBreachRule,
    FundingSpreadFlipRule,
    HeartbeatRule,
    LiqDistanceRule,
    WebhookChannel,
    default_rules,
)
from .drift import (
    AttributionRow,
    DriftReport,
    MarketDrift,
    SlippageBaseline,
    StructuralDrift,
    build_drift_report,
)
from .executor import (
    DriftAlert,
    LiveCaps,
    LiveExecutor,
    LiveReject,
    LiveStartRefused,
    StopReport,
    stop_all_live,
)
from .session import PaperSession, RunResult, build_adapter

__all__ = [
    "PaperSession",
    "RunResult",
    "build_adapter",
    "LiveExecutor",
    "LiveCaps",
    "LiveReject",
    "LiveStartRefused",
    "DriftAlert",
    "StopReport",
    "stop_all_live",
    "DriftReport",
    "StructuralDrift",
    "MarketDrift",
    "AttributionRow",
    "SlippageBaseline",
    "build_drift_report",
    "Alert",
    "AlertContext",
    "AlertEngine",
    "AlertRule",
    "AlertChannel",
    "CollectingChannel",
    "WebhookChannel",
    "LiqDistanceRule",
    "DriftBreachRule",
    "FundingSpreadFlipRule",
    "HeartbeatRule",
    "default_rules",
]
