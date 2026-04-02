"""FundingMeanReversionStrategy — Bollinger bands on funding rates."""
from __future__ import annotations

import logging
import math
import statistics
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from ..models import Candle, Signal, Side
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext

logger = logging.getLogger("flint.strategy.funding_mean_reversion")


class FundingMeanReversionStrategy(Strategy):
    """Mean-reversion strategy based on Bollinger Bands applied to funding rates.

    Entry conditions:
    - Rate below lower band → go long (funding is extremely negative, expect reversion up).
    - Rate above upper band → go short (funding is extremely positive, expect reversion down).

    Exit conditions:
    - Rate crosses back through the middle band (mean).
    - Position held longer than max_hold_hours.
    """

    def __init__(
        self,
        bb_lookback: int = 24,
        bb_std: float = 2.0,
        max_hold_hours: int = 12,
        position_size_pct: float = 0.5,
        candle_resolution_s: int = 3600,
    ) -> None:
        self.bb_lookback = bb_lookback
        self.bb_std = bb_std
        self.max_hold_hours = max_hold_hours
        self.position_size_pct = position_size_pct
        self.candle_resolution_s = candle_resolution_s
        self._entry_ts: int = 0
        self._entry_side: Optional[Side] = None

    @property
    def name(self) -> str:
        return "funding_mean_reversion"

    def reset(self) -> None:
        self._entry_ts = 0
        self._entry_side = None

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "bb_lookback": {"type": "int", "low": 6, "high": 72, "default": 24},
            "bb_std": {"type": "float", "low": 1.0, "high": 4.0, "default": 2.0},
            "max_hold_hours": {"type": "int", "low": 1, "high": 48, "default": 12},
            "position_size_pct": {"type": "float", "low": 0.1, "high": 1.0, "default": 0.5},
            "candle_resolution_s": {"type": "int", "low": 60, "high": 3600, "default": 3600},
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _bollinger_bands(
        self, rates: List[float]
    ) -> Tuple[float, float, float]:
        """Return (lower, middle, upper) Bollinger Band values."""
        mean = statistics.mean(rates)
        std = statistics.stdev(rates) if len(rates) > 1 else 0.0
        return mean - self.bb_std * std, mean, mean + self.bb_std * std

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def on_candle(
        self,
        candle: Candle,
        history: List[Candle],
        ctx: Optional["ExecutionContext"] = None,
    ) -> Signal:
        if ctx is None:
            return Signal.HOLD

        rates_data: List[Tuple[int, float]] = ctx.get_funding_rates(
            candle.market, self.bb_lookback
        )

        if len(rates_data) < self.bb_lookback:
            return Signal.HOLD

        rates = [r for _, r in rates_data[-self.bb_lookback :]]
        current_rate = rates[-1]
        lower, middle, upper = self._bollinger_bands(rates)

        logger.debug(
            "Funding bands: lower=%.6f mid=%.6f upper=%.6f current=%.6f",
            lower, middle, upper, current_rate,
        )

        has_position = self._entry_ts > 0

        if has_position:
            return self._check_exit(candle, ctx, current_rate, middle)
        else:
            return self._check_entry(candle, ctx, current_rate, lower, upper)

    def _check_entry(self, candle, ctx, current_rate, lower, upper):
        equity = getattr(getattr(ctx, "account", None), "equity", None)
        if equity is None or equity <= 0:
            equity = 1000.0  # fallback

        size = (equity * self.position_size_pct) / candle.close if candle.close > 0 else 0
        if size <= 0:
            return Signal.HOLD

        if current_rate < lower:
            # Funding is extremely negative → expect reversion upward → go long
            ctx.market_order(candle.market, Side.LONG, size)
            self._entry_ts = candle.ts
            self._entry_side = Side.LONG
            logger.info(
                "Entry LONG at ts=%d (rate=%.6f < lower=%.6f)", candle.ts, current_rate, lower
            )
        elif current_rate > upper:
            # Funding is extremely positive → expect reversion downward → go short
            ctx.market_order(candle.market, Side.SHORT, size)
            self._entry_ts = candle.ts
            self._entry_side = Side.SHORT
            logger.info(
                "Entry SHORT at ts=%d (rate=%.6f > upper=%.6f)", candle.ts, current_rate, upper
            )

        return Signal.HOLD

    def _check_exit(self, candle, ctx, current_rate, middle):
        hold_hours = (candle.ts - self._entry_ts) / 3600

        should_exit = False
        reason = ""

        if hold_hours >= self.max_hold_hours:
            should_exit = True
            reason = "max hold"
        elif self._entry_side == Side.LONG and current_rate >= middle:
            should_exit = True
            reason = "rate crossed middle (long exit)"
        elif self._entry_side == Side.SHORT and current_rate <= middle:
            should_exit = True
            reason = "rate crossed middle (short exit)"

        if should_exit:
            logger.info("Exit at ts=%d: %s", candle.ts, reason)
            ctx.close_position(candle.market)
            self._entry_ts = 0
            self._entry_side = None

        return Signal.HOLD
