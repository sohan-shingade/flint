"""Read-only value objects a strategy sees through ``ctx`` (§8.2, §8.3).

These are deliberately inert **value objects**: an ``AccountView`` is a snapshot
of numbers, not a live handle onto the engine, so a strategy can read its equity
but cannot reach config, secrets, or the store through it (§8.3 — the ctx holds
no reference path to those). The sizing helpers (§8.1) return a ``size_usd``
computed off *current venue equity*, so position sizes compound with the account
instead of the fixed-USD anti-pattern.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OpenInterestSnapshot:
    """One recorded open-interest reading (engine-local, §5).

    Open interest is not yet a core model — like ``TradePrint`` it lives in the
    engine until the Phase-2 recorder promotes it, and that swap goes through
    team-lead. ``ts`` is unix-ms; ``open_interest`` is in base units.
    """

    market: str
    ts: int
    open_interest: float
    venue: str


@dataclass(frozen=True)
class AccountView:
    """A per-venue equity snapshot + the §8.1 sizing helpers (a value object).

    ``equity`` and ``cross_margin_available`` are valued as-of bar start off the
    §8.2 visibility contract (marks strictly before the bar). The helpers turn a
    risk intent into a ``size_usd`` the engine then converts to base units at the
    execution bar's open (§8.1 rule 1) — never at the decision bar's close.
    """

    venue: str
    equity: float
    cross_margin_available: float

    def size_for_target_leverage(self, target: float) -> float:
        """Notional for ``target``× leverage of venue equity (e.g. 2.0 → 2× equity)."""
        return max(0.0, target * self.equity)

    def size_for_risk_pct(self, risk_pct: float, stop_distance_bps: float) -> float:
        """Notional that risks ``risk_pct`` of equity if stopped at ``stop_distance_bps``.

        Risking a fixed fraction ``r`` of equity with a stop ``d`` away sizes the
        notional at ``r × equity / d`` (loss at the stop = notional × d = r ×
        equity). A non-positive stop distance has no defined risk → 0.
        """
        if stop_distance_bps <= 0:
            return 0.0
        return max(0.0, self.equity * risk_pct / (stop_distance_bps / 10_000.0))

    def size_for_kelly(
        self, edge: float, win_rate: float, fraction: float = 0.5
    ) -> float:
        """Fractional-Kelly notional off venue equity.

        Kelly fraction ``f* = p − (1 − p) / b`` for win prob ``p`` (``win_rate``)
        and payoff odds ``b`` (``edge``); we stake ``fraction × f*`` (half-Kelly by
        default — the practical variance cut). A non-positive edge or Kelly stakes
        nothing.
        """
        if edge <= 0:
            return 0.0
        kelly = win_rate - (1.0 - win_rate) / edge
        return max(0.0, fraction * kelly * self.equity)
