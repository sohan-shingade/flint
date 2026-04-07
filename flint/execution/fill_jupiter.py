"""Jupiter Perps fill model — keeper-delay-aware pool-based execution.

Jupiter Perps is pool-based (no orderbook). Execution at oracle price with:
- 2-step keeper model: trader submits request, keeper fulfills after a delay.
  Fill price is interpolated between candle open/close based on the delay time
  (NOT rounded to bar boundaries).
- Quadratic price impact: impact_usd = (notional / scalar) * notional
  where scalar defaults to 1e9 USD for major markets.
- Flat 6 bps trading fee handled separately by the fee model, not here.
"""
from __future__ import annotations

import random
from typing import List, Optional

from ..models import Candle, Fill, Order, OrderbookSnapshot, Side
from .fill_models import FillModel

# Per-market impact scalars (USD).  Larger scalar = less impact per unit notional.
_IMPACT_SCALARS: dict[str, float] = {
    "SOL-PERP": 1e9,
    "ETH-PERP": 1e9,
    "BTC-PERP": 1e9,
}
_DEFAULT_IMPACT_SCALAR = 1e9


class JupiterFillModel(FillModel):
    """Fill model for Jupiter Perps: pool-based, keeper-delay-aware.

    Parameters
    ----------
    base_latency_s:
        Median keeper delay in seconds (default 12 s, ~2 Solana slots).
    latency_jitter_s:
        Uniform half-range applied to the delay: actual delay is drawn from
        ``Uniform(base_latency_s - jitter, base_latency_s + jitter)``,
        clamped to [0, ∞).
    seed:
        Optional RNG seed for reproducibility.
    """

    def __init__(
        self,
        base_latency_s: float = 12.0,
        latency_jitter_s: float = 8.0,
        seed: Optional[int] = None,
    ) -> None:
        self._base_latency = base_latency_s
        self._jitter = latency_jitter_s
        self._rng = random.Random(seed)
        self._candle_buffer: List[Candle] = []

    # ------------------------------------------------------------------
    # FillModel interface
    # ------------------------------------------------------------------

    def set_orderbook(self, book: Optional[OrderbookSnapshot]) -> None:
        """No-op: Jupiter Perps is pool-based and does not use an orderbook."""

    def set_candle_buffer(self, candles: List[Candle]) -> None:
        """Provide lookahead candles used for cross-bar keeper delay interpolation.

        The engine should call this before each bar with the full candle series
        (or at least a window of future candles) so that delays exceeding the
        current bar duration can be resolved into the next bar.
        """
        self._candle_buffer = candles

    def fill_market(self, order: Order, candle: Candle) -> Optional[Fill]:
        """Fill a market order at the keeper-delayed oracle price plus impact."""
        delay_s = max(
            0.0,
            self._base_latency + self._rng.uniform(-self._jitter, self._jitter),
        )
        fill_price = self._interpolate_price(candle, delay_s)

        # Quadratic price impact
        notional = order.size * fill_price
        scalar = _IMPACT_SCALARS.get(order.market, _DEFAULT_IMPACT_SCALAR)
        impact_usd = (notional / scalar) * notional
        impact_per_unit = impact_usd / order.size if order.size > 0 else 0.0

        if order.side == Side.LONG:
            fill_price += impact_per_unit
        else:
            fill_price -= impact_per_unit

        return Fill(
            order_id=order.order_id,
            market=order.market,
            side=order.side,
            price=fill_price,
            size=order.size,
            fee=0.0,  # handled by FlatFeeModel at 6 bps
            ts=candle.ts,
            venue="jupiter",
        )

    def fill_limit(self, order: Order, candle: Candle) -> Optional[Fill]:
        """Fill a limit order if the candle's range crosses the limit price."""
        if order.side == Side.LONG and candle.low <= order.price:
            return Fill(
                order_id=order.order_id,
                market=order.market,
                side=order.side,
                price=order.price,
                size=order.size,
                fee=0.0,
                ts=candle.ts,
                venue="jupiter",
            )
        if order.side == Side.SHORT and candle.high >= order.price:
            return Fill(
                order_id=order.order_id,
                market=order.market,
                side=order.side,
                price=order.price,
                size=order.size,
                fee=0.0,
                ts=candle.ts,
                venue="jupiter",
            )
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _interpolate_price(self, candle: Candle, delay_s: float) -> float:
        """Return the oracle price at ``candle.ts + delay_s`` by linear interpolation.

        - Within the current bar: lerp between open and close.
        - Beyond the current bar: continue into the next bar from the buffer.
        - If no next bar is available: return the current bar's close.
        """
        bar_duration = candle.resolution_s
        if bar_duration <= 0:
            return candle.close

        if delay_s <= bar_duration:
            frac = delay_s / bar_duration
            return candle.open + frac * (candle.close - candle.open)

        # Delay extends into the next bar
        remaining = delay_s - bar_duration
        next_c = self._find_next_candle(candle)
        if next_c is None:
            return candle.close
        frac = min(remaining / next_c.resolution_s, 1.0)
        return next_c.open + frac * (next_c.close - next_c.open)

    def _find_next_candle(self, current: Candle) -> Optional[Candle]:
        """Look up the immediately following candle in the buffer."""
        next_ts = current.ts + current.resolution_s
        for c in self._candle_buffer:
            if c.ts == next_ts and c.market == current.market:
                return c
        return None
