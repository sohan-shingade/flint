"""Alerts + heartbeat — the unattended contract (§6.7).

A paper/live session runs while nobody is watching, so it must shout when
something needs a human. v1 ships a small **rule engine** over an
:class:`AlertContext` the runner assembles each bar, and one delivery channel —
a **webhook** (covers Discord/Telegram/Slack) — plus persistence. The four v1
rules (§6.7):

* liquidation proximity — a position within ``liq_distance_pct`` of its
  liquidation price (default 10%);
* structural drift breach — the simulator disagreeing with reality (§drift);
* funding-spread sign flip — the funding edge a funding strategy relies on
  reversed sign;
* **process death** — a heartbeat: silence longer than ``2×`` the bar interval
  since the last venue event means the feed (or the process) is down.

The channel is an injected seam. Tests use :class:`CollectingChannel` (no
network); :class:`WebhookChannel` takes a ``poster`` callable so the HTTP call
itself is injected and this module never imports a network library.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .drift import DriftReport

WARNING = "warning"
CRITICAL = "critical"


@dataclass(frozen=True)
class Alert:
    """One fired alert — persisted and dispatched to the channel (§6.7)."""

    rule: str
    severity: str
    message: str
    ts: int
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AlertContext:
    """The per-evaluation snapshot the rules read (assembled by the runner)."""

    now_ts: int  # the session clock = latest venue event ts (PaperClock.now, §6.7)
    bar_interval_s: int
    last_event_ts: int | None = None
    liq_distances_pct: Mapping[str, float] = field(default_factory=dict)
    drift: DriftReport | None = None
    funding_spread: float | None = None
    prev_funding_spread: float | None = None


class AlertRule(Protocol):
    def evaluate(self, ctx: AlertContext) -> list[Alert]:
        ...


@dataclass(frozen=True)
class LiqDistanceRule:
    """Fire when any position sits within ``threshold_pct`` of liquidation (§6.7)."""

    threshold_pct: float = 10.0

    def evaluate(self, ctx: AlertContext) -> list[Alert]:
        out: list[Alert] = []
        for market, pct in ctx.liq_distances_pct.items():
            if pct < self.threshold_pct:
                out.append(
                    Alert(
                        rule="liq_distance",
                        severity=CRITICAL,
                        message=f"{market} is {pct:.1f}% from liquidation (< {self.threshold_pct:.0f}%)",
                        ts=ctx.now_ts,
                        detail={"market": market, "liq_distance_pct": pct},
                    )
                )
        return out


@dataclass(frozen=True)
class DriftBreachRule:
    """Fire when structural drift breaches — the simulator is wrong (§6.7)."""

    def evaluate(self, ctx: AlertContext) -> list[Alert]:
        if ctx.drift is None:
            return []
        reasons = ctx.drift.structural_breaches()
        if not reasons:
            return []
        return [
            Alert(
                rule="drift_breach",
                severity=CRITICAL,
                message="structural drift: " + "; ".join(reasons),
                ts=ctx.now_ts,
                detail={"reasons": reasons},
            )
        ]


@dataclass(frozen=True)
class FundingSpreadFlipRule:
    """Fire when the funding spread a strategy harvests reverses sign (§6.7)."""

    def evaluate(self, ctx: AlertContext) -> list[Alert]:
        cur, prev = ctx.funding_spread, ctx.prev_funding_spread
        if cur is None or prev is None:
            return []
        if (prev > 0 > cur) or (prev < 0 < cur):
            return [
                Alert(
                    rule="funding_spread_flip",
                    severity=WARNING,
                    message=f"funding spread flipped sign: {prev:+.4f} -> {cur:+.4f}",
                    ts=ctx.now_ts,
                    detail={"prev": prev, "current": cur},
                )
            ]
        return []


@dataclass(frozen=True)
class HeartbeatRule:
    """Fire when the venue has been silent longer than ``multiple ×`` a bar (§6.7).

    Silence is measured in venue time (``now_ts`` is ``PaperClock.now``), so a
    stalled feed or a dead process trips it even though no wall-clock timer runs.
    """

    multiple: float = 2.0

    def evaluate(self, ctx: AlertContext) -> list[Alert]:
        if ctx.last_event_ts is None:
            return []
        silence_ms = ctx.now_ts - ctx.last_event_ts
        limit_ms = self.multiple * ctx.bar_interval_s * 1000
        if silence_ms > limit_ms:
            return [
                Alert(
                    rule="heartbeat",
                    severity=CRITICAL,
                    message=(
                        f"no venue event for {silence_ms / 1000:.0f}s "
                        f"(> {self.multiple:g}× the {ctx.bar_interval_s}s bar)"
                    ),
                    ts=ctx.now_ts,
                    detail={"silence_ms": silence_ms, "limit_ms": limit_ms},
                )
            ]
        return []


def default_rules(
    *, liq_threshold_pct: float = 10.0, heartbeat_multiple: float = 2.0
) -> list[AlertRule]:
    """The four v1 rules with their default thresholds (§6.7)."""
    return [
        LiqDistanceRule(liq_threshold_pct),
        DriftBreachRule(),
        FundingSpreadFlipRule(),
        HeartbeatRule(heartbeat_multiple),
    ]


class AlertChannel(ABC):
    """Where a fired alert goes. One implementation per delivery mechanism."""

    @abstractmethod
    def send(self, alert: Alert) -> None:
        ...


class CollectingChannel(AlertChannel):
    """An in-memory channel — the test/UI seam; no network."""

    def __init__(self) -> None:
        self.sent: list[Alert] = []

    def send(self, alert: Alert) -> None:
        self.sent.append(alert)


class WebhookChannel(AlertChannel):
    """Posts an alert as JSON to a webhook (Discord/Telegram/Slack) (§6.7).

    The HTTP call is the injected ``poster`` (``poster(url, payload)``), so this
    module imports no network library and tests drive it with a fake poster.
    """

    def __init__(self, url: str, poster: Callable[[str, Mapping[str, Any]], None]) -> None:
        self._url = url
        self._poster = poster

    def send(self, alert: Alert) -> None:
        self._poster(
            self._url,
            {
                "rule": alert.rule,
                "severity": alert.severity,
                "message": alert.message,
                "ts": alert.ts,
                "detail": dict(alert.detail),
            },
        )


class AlertEngine:
    """Runs the rule set over a context, dispatches, and retains fired alerts."""

    def __init__(self, rules: Sequence[AlertRule], channel: AlertChannel) -> None:
        self._rules = list(rules)
        self._channel = channel
        self.fired: list[Alert] = []

    def evaluate(self, ctx: AlertContext) -> list[Alert]:
        """Evaluate every rule, dispatch each fired alert, and record it."""
        fired: list[Alert] = []
        for rule in self._rules:
            for alert in rule.evaluate(ctx):
                self._channel.send(alert)
                self.fired.append(alert)
                fired.append(alert)
        return fired
