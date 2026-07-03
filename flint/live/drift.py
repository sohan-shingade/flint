"""Drift — the parity promise with numbers, not vibes (§6.7).

Paper trades run the *same* engine as the backtest, so any divergence between the
two is measurable, and the review's rule is that we say which kind it is:

* **Structural drift → alert.** The simulator is wrong and must be fixed: fill
  slippage that has walked off the backtest's slippage distribution (z-score > 3
  over a rolling 20-trade window), a funding payment that doesn't match the
  backtest beyond rounding, or any liquidation / rejection the backtest never
  produced. ``StructuralDrift.breached`` feeds the alert engine.
* **Market drift → chart, not alarm.** Performance decay versus the backtest
  period (rolling Sharpe, hit rate). The regime changed; that is a finding, not a
  bug, so it is chart data only.

Everything here is computed by reading the append-only event log (the FILL /
FUNDING / LIQUIDATION / EQUITY events the engine already wrote) against a
backtest baseline — no engine change, no second source of truth.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from flint.engine.money import Money, ZERO, money
from flint.engine.portfolio import (
    FILL,
    FUNDING,
    LIQUIDATION,
    ORDER_REJECTED,
    Event,
)
from flint.engine.portfolio.events import EQUITY

SLIPPAGE_WINDOW = 20  # trades in the rolling window (§6.7)
SLIPPAGE_Z_LIMIT = 3.0  # |z| above this is structural drift (§6.7)
FUNDING_ROUNDING_TOL = money("0.01")  # a mismatch below this is rounding, not drift


@dataclass(frozen=True)
class SlippageBaseline:
    """The backtest's fill-slippage distribution, in basis points (§6.7)."""

    mean_bps: float
    std_bps: float

    @classmethod
    def from_fills(cls, slippages_bps: Sequence[float]) -> "SlippageBaseline":
        n = len(slippages_bps)
        if n == 0:
            return cls(0.0, 0.0)
        mean = sum(slippages_bps) / n
        var = sum((s - mean) ** 2 for s in slippages_bps) / n if n > 1 else 0.0
        return cls(mean_bps=mean, std_bps=math.sqrt(var))


@dataclass(frozen=True)
class StructuralDrift:
    """The 'simulator is wrong' signals — each one is alert-worthy (§6.7)."""

    slippage_z: float | None  # rolling-window mean z vs baseline; None if < a full window
    slippage_breach: bool
    funding_mismatch: Money  # actual settled − backtest-expected
    funding_breach: bool
    unexpected: tuple[str, ...]  # descriptions of liquidations / rejections

    @property
    def breached(self) -> bool:
        return self.slippage_breach or self.funding_breach or bool(self.unexpected)

    def reasons(self) -> list[str]:
        out: list[str] = []
        if self.slippage_breach and self.slippage_z is not None:
            out.append(f"fill slippage z={self.slippage_z:.1f} (>|{SLIPPAGE_Z_LIMIT}|)")
        if self.funding_breach:
            out.append(f"funding mismatch {self.funding_mismatch} beyond rounding")
        out.extend(self.unexpected)
        return out


@dataclass(frozen=True)
class MarketDrift:
    """Performance decay vs the backtest — chart data, never an alarm (§6.7)."""

    rolling_sharpe: tuple[float, ...]
    hit_rate: float | None


@dataclass(frozen=True)
class AttributionRow:
    """One line of the per-component attribution table (§6.7)."""

    component: str
    paper: str
    sim: str
    matched: bool


@dataclass(frozen=True)
class DriftReport:
    structural: StructuralDrift
    market: MarketDrift
    attribution: tuple[AttributionRow, ...]

    def structural_breaches(self) -> list[str]:
        """The structural reasons an alert should fire on (empty = in parity)."""
        return self.structural.reasons()


# --- event-log readers -----------------------------------------------------


def _fills(events: Iterable[Event]) -> list[dict]:
    return [dict(e.payload) for e in events if e.kind == FILL]


def _slippage_z(fills: Sequence[dict], baseline: SlippageBaseline) -> tuple[float | None, bool]:
    """z-score of the last-``SLIPPAGE_WINDOW`` mean slippage vs the baseline.

    A full window is required before the z is trusted (a mean over 3 trades is
    noise) — under that, it is ``None`` and never breaches.
    """
    slips = [float(f.get("slippage_bps", 0.0)) for f in fills]
    if len(slips) < SLIPPAGE_WINDOW or baseline.std_bps <= 0.0:
        return None, False
    window = slips[-SLIPPAGE_WINDOW:]
    mean = sum(window) / len(window)
    # Standard error of the window mean under the baseline distribution.
    se = baseline.std_bps / math.sqrt(len(window))
    z = (mean - baseline.mean_bps) / se
    return z, abs(z) > SLIPPAGE_Z_LIMIT


def _funding_total(events: Iterable[Event]) -> Money:
    total = ZERO
    for e in events:
        if e.kind == FUNDING:
            total += money(e.payload["amount"])
    return total


def _unexpected(events: Iterable[Event]) -> tuple[str, ...]:
    out: list[str] = []
    for e in events:
        if e.kind == LIQUIDATION:
            p = e.payload
            out.append(f"unexpected liquidation on {p.get('venue')}:{p.get('market')} @ ts={e.ts}")
        elif e.kind == ORDER_REJECTED:
            p = e.payload
            out.append(f"unexpected rejection {p.get('client_order_id')} ({p.get('reason', '')})")
    return tuple(out)


def _equity_series(events: Iterable[Event]) -> list[float]:
    return [float(e.payload["equity"]) for e in events if e.kind == EQUITY]


def _rolling_sharpe(equity: Sequence[float], window: int) -> tuple[float, ...]:
    if len(equity) < 2:
        return ()
    rets = [
        (equity[i] - equity[i - 1]) / equity[i - 1] if equity[i - 1] else 0.0
        for i in range(1, len(equity))
    ]
    out: list[float] = []
    for i in range(window - 1, len(rets)):
        w = rets[i - window + 1 : i + 1]
        mean = sum(w) / len(w)
        var = sum((r - mean) ** 2 for r in w) / len(w)
        std = math.sqrt(var)
        out.append(mean / std if std > 0 else 0.0)
    return tuple(out)


def _hit_rate(fills: Sequence[dict]) -> float | None:
    """Fraction of *closing* fills (nonzero realized PnL) that were winners."""
    realized = [money(f.get("realized_pnl", "0")) for f in fills]
    closes = [r for r in realized if r != ZERO]
    if not closes:
        return None
    wins = sum(1 for r in closes if r > ZERO)
    return wins / len(closes)


# --- the report ------------------------------------------------------------


def build_drift_report(
    events: Sequence[Event],
    *,
    slippage_baseline: SlippageBaseline,
    expected_funding: Money | None = None,
    latency_p50_ms: float | None = None,
    modeled_latency_ms: float | None = None,
    sharpe_window: int = 20,
) -> DriftReport:
    """Fold the event log into a structural + market + attribution drift report."""
    fills = _fills(events)
    slip_z, slip_breach = _slippage_z(fills, slippage_baseline)

    actual_funding = _funding_total(events)
    if expected_funding is None:
        funding_mismatch: Money = ZERO
        funding_breach = False
    else:
        funding_mismatch = actual_funding - expected_funding
        funding_breach = abs(funding_mismatch) > FUNDING_ROUNDING_TOL

    unexpected = _unexpected(events)
    structural = StructuralDrift(
        slippage_z=slip_z,
        slippage_breach=slip_breach,
        funding_mismatch=funding_mismatch,
        funding_breach=funding_breach,
        unexpected=unexpected,
    )

    market = MarketDrift(
        rolling_sharpe=_rolling_sharpe(_equity_series(events), sharpe_window),
        hit_rate=_hit_rate(fills),
    )

    mean_slip = (sum(float(f.get("slippage_bps", 0.0)) for f in fills) / len(fills)) if fills else 0.0
    attribution = (
        AttributionRow(
            component="fills",
            paper=f"{mean_slip:+.1f} bps",
            sim=f"{slippage_baseline.mean_bps:+.1f} bps",
            matched=not slip_breach,
        ),
        AttributionRow(
            component="funding",
            paper=str(actual_funding),
            sim="—" if expected_funding is None else str(expected_funding),
            matched=not funding_breach,
        ),
        AttributionRow(
            component="latency",
            paper="—" if latency_p50_ms is None else f"p50 {latency_p50_ms:.0f} ms",
            sim="—" if modeled_latency_ms is None else f"modeled {modeled_latency_ms:.0f} ms",
            matched=True,
        ),
        AttributionRow(
            component="events",
            paper=f"{len(unexpected)} unexpected",
            sim="0 unexpected",
            matched=not unexpected,
        ),
    )

    return DriftReport(structural=structural, market=market, attribution=attribution)
