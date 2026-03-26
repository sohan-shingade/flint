export const DUAL_TF_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List
import numpy as np


class DualTimeframe(Strategy):
    """Dual Timeframe — trend + entry alignment.

    Uses long-period SMA for trend direction, short-period momentum for entry.
    Only enters in the direction of the higher-timeframe trend.
    Uses v2 execution: stop-loss, impact checks, position sizing.
    """
    def __init__(self, trend_period=50, entry_period=10, threshold=0.02, stop_pct=3.0):
        self.trend_period = trend_period
        self.entry_period = entry_period
        self.threshold = threshold
        self.stop_pct = stop_pct / 100

    @property
    def name(self) -> str:
        return f"DualTF({self.trend_period}/{self.entry_period})"

    @classmethod
    def parameters(cls):
        return {
            "trend_period": {"type": "int", "low": 30, "high": 100, "default": 50},
            "entry_period": {"type": "int", "low": 5, "high": 20, "default": 10},
            "threshold": {"type": "float", "low": 0.005, "high": 0.05, "default": 0.02},
            "stop_pct": {"type": "float", "low": 1.0, "high": 8.0, "default": 3.0},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < self.trend_period:
            return Signal.HOLD
        if ctx is None:
            return Signal.HOLD

        long_closes = np.array([c.close for c in history[-self.trend_period:]])
        trend_up = float(np.mean(long_closes)) > float(np.mean(long_closes[:-1]))
        short = [c.close for c in history[-self.entry_period:]]
        momentum = (short[-1] - short[0]) / short[0] if short[0] else 0

        if trend_up and momentum > self.threshold and not ctx.positions:
            size = (ctx.account.cash * 0.9) / candle.close
            impact = ctx.get_impact_price(candle.market, Side.LONG, size)
            if impact and abs(impact - candle.close) / candle.close > 0.002:
                return Signal.HOLD
            if size > 0:
                ctx.market_order(candle.market, Side.LONG, size)
                ctx.stop_order(candle.market, Side.SHORT, size,
                               candle.close * (1 - self.stop_pct))

        elif not trend_up and momentum < -self.threshold and not ctx.positions:
            size = (ctx.account.cash * 0.9) / candle.close
            impact = ctx.get_impact_price(candle.market, Side.SHORT, size)
            if impact and abs(impact - candle.close) / candle.close > 0.002:
                return Signal.HOLD
            if size > 0:
                ctx.market_order(candle.market, Side.SHORT, size)
                ctx.stop_order(candle.market, Side.LONG, size,
                               candle.close * (1 + self.stop_pct))

        elif ctx.positions:
            # Exit when momentum fades
            if abs(momentum) < self.threshold * 0.3:
                ctx.close_position(candle.market)
                ctx.cancel_all(candle.market)

        return Signal.HOLD

    def reset(self) -> None:
        pass
`
