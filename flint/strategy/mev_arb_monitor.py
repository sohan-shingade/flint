"""MevArbMonitor — DEX arbitrage opportunity scanner (monitoring only).

This strategy never places orders. It scans for cross-DEX price
discrepancies that exceed a minimum profit threshold and logs them.
Use it to surface profitable routes for further analysis or manual
execution.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, List, Optional

from ..models import Candle, Signal
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext

logger = logging.getLogger("flint.strategy.mev_arb_monitor")


class MevArbMonitor(Strategy):
    """Monitoring strategy that scans for DEX arbitrage opportunities.

    Always returns ``Signal.HOLD`` — no orders are ever placed.

    Parameters
    ----------
    min_profit_bps:
        Minimum arb profit in basis points to log as an opportunity.
    max_hops:
        Maximum number of swap hops to consider in a route.
    alert_enabled:
        If non-zero, emit a WARNING log in addition to the DEBUG log
        when an opportunity is found.
    candle_resolution_s:
        Candle granularity hint (used by the engine scheduler).
    """

    def __init__(
        self,
        min_profit_bps: float = 10.0,
        max_hops: int = 3,
        alert_enabled: int = 0,
        candle_resolution_s: int = 60,
    ) -> None:
        self.min_profit_bps = min_profit_bps
        self.max_hops = max_hops
        self.alert_enabled = alert_enabled
        self.candle_resolution_s = candle_resolution_s
        self._opportunities_found: int = 0

    @property
    def name(self) -> str:
        return "mev_arb_monitor"

    def reset(self) -> None:
        self._opportunities_found = 0

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "min_profit_bps": {"type": "float", "low": 1.0, "high": 100.0, "default": 10.0},
            "max_hops": {"type": "int", "low": 1, "high": 5, "default": 3},
            "alert_enabled": {"type": "int", "low": 0, "high": 1, "default": 0},
            "candle_resolution_s": {"type": "int", "low": 1, "high": 3600, "default": 60},
        }

    def on_candle(
        self,
        candle: Candle,
        history: List[Candle],
        ctx: Optional["ExecutionContext"] = None,
    ) -> Signal:
        """Scan for arb opportunities and log them. Always returns HOLD."""
        if ctx is not None:
            self._scan(candle, ctx)
        return Signal.HOLD

    # ------------------------------------------------------------------
    # Internal scanning helpers
    # ------------------------------------------------------------------

    def _scan(self, candle: Candle, ctx) -> None:
        """Check for arb routes via ctx (best-effort, never raises)."""
        try:
            routes = self._get_routes(candle, ctx)
            for route in routes:
                profit_bps = route.get("profit_bps", 0.0)
                if profit_bps >= self.min_profit_bps:
                    self._opportunities_found += 1
                    msg = (
                        "Arb opportunity: market=%s profit=%.2fbps hops=%d ts=%d",
                        candle.market,
                        profit_bps,
                        route.get("hops", 1),
                        candle.ts,
                    )
                    logger.debug(*msg)
                    if self.alert_enabled:
                        logger.warning(*msg)
        except Exception as exc:  # pragma: no cover
            logger.debug("Arb scan error: %s", exc)

    def _get_routes(self, candle: Candle, ctx) -> List[dict]:
        """Retrieve candidate arb routes from ctx if available."""
        if hasattr(ctx, "get_arb_routes"):
            return ctx.get_arb_routes(candle.market, max_hops=self.max_hops) or []
        return []
