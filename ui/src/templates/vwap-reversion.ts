export const VWAP_REVERSION_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List
import numpy as np


class VWAPReversion(Strategy):
    """VWAP Reversion — buy below VWAP, sell on reversion.

    Uses v2 execution with stop-loss, impact checks, proper sizing.
    """
    def __init__(self, period=20, entry_pct=2.0, exit_pct=0.5, stop_pct=3.0):
        self.period = period
        self.entry_pct = entry_pct / 100
        self.exit_pct = exit_pct / 100
        self.stop_pct = stop_pct / 100

    @property
    def name(self): return f"VWAPReversion({self.period})"

    @classmethod
    def parameters(cls):
        return {
            "period": {"type": "int", "low": 10, "high": 50, "default": 20},
            "entry_pct": {"type": "float", "low": 0.5, "high": 5.0, "default": 2.0},
            "exit_pct": {"type": "float", "low": 0.1, "high": 2.0, "default": 0.5},
            "stop_pct": {"type": "float", "low": 1.0, "high": 6.0, "default": 3.0},
        }

    def on_candle(self, candle, history, ctx=None):
        if ctx is None or len(history) < self.period:
            return Signal.HOLD
        window = history[-self.period:]
        vol_sum = sum(c.volume for c in window)
        if vol_sum == 0:
            return Signal.HOLD
        vwap = sum(c.close * c.volume for c in window) / vol_sum
        dev = (candle.close - vwap) / vwap

        if not ctx.positions:
            side = None
            if dev < -self.entry_pct:
                side = Side.LONG
            elif dev > self.entry_pct:
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
            if abs(dev) < self.exit_pct:
                ctx.close_position(candle.market)
                ctx.cancel_all(candle.market)

        return Signal.HOLD

    def reset(self): pass
`
