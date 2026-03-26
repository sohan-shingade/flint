export const MEAN_REVERSION_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List
import numpy as np


class MeanReversion(Strategy):
    """Z-Score Mean Reversion with stop-loss.

    Buys when price is N standard deviations below the rolling mean.
    Sells when price reverts. Uses v2 execution with impact checks.
    """
    def __init__(self, period=20, entry_z=2.0, exit_z=0.5, stop_pct=5.0):
        self.period = period
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.stop_pct = stop_pct / 100

    @property
    def name(self) -> str:
        return f"MeanReversion(z={self.entry_z})"

    @classmethod
    def parameters(cls):
        return {
            "period": {"type": "int", "low": 10, "high": 50, "default": 20},
            "entry_z": {"type": "float", "low": 1.0, "high": 3.5, "default": 2.0},
            "exit_z": {"type": "float", "low": 0.0, "high": 1.5, "default": 0.5},
            "stop_pct": {"type": "float", "low": 2.0, "high": 10.0, "default": 5.0},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if ctx is None or len(history) < self.period:
            return Signal.HOLD
        closes = np.array([c.close for c in history[-self.period:]])
        mean = float(np.mean(closes))
        std = float(np.std(closes))
        if std == 0:
            return Signal.HOLD
        z = (candle.close - mean) / std

        if not ctx.positions:
            side = None
            if z <= -self.entry_z:
                side = Side.LONG
            elif z >= self.entry_z:
                side = Side.SHORT
            if side:
                size = (ctx.account.cash * 0.9) / candle.close
                impact = ctx.get_impact_price(candle.market, side, size)
                if impact and abs(impact - candle.close) / candle.close > 0.002:
                    return Signal.HOLD
                if size > 0:
                    ctx.market_order(candle.market, side, size)
                    stop_side = Side.SHORT if side == Side.LONG else Side.LONG
                    stop_price = candle.close * ((1 - self.stop_pct) if side == Side.LONG else (1 + self.stop_pct))
                    ctx.stop_order(candle.market, stop_side, size, stop_price)
        else:
            if abs(z) <= self.exit_z:
                ctx.close_position(candle.market)
                ctx.cancel_all(candle.market)
        return Signal.HOLD

    def reset(self) -> None:
        pass
`
