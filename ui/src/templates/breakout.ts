export const BREAKOUT_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List
import numpy as np


class BreakoutMomentum(Strategy):
    """Breakout with volume confirmation.

    Enters on breakout above N-period high when volume spikes.
    Uses v2 execution: stop at recent low, impact checks, position sizing.
    """
    def __init__(self, lookback=20, volume_mult=1.5):
        self.lookback = lookback
        self.volume_mult = volume_mult

    @property
    def name(self) -> str:
        return f"Breakout({self.lookback})"

    @classmethod
    def parameters(cls):
        return {
            "lookback": {"type": "int", "low": 10, "high": 50, "default": 20},
            "volume_mult": {"type": "float", "low": 1.0, "high": 3.0, "default": 1.5},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < self.lookback + 1:
            return Signal.HOLD
        if ctx is None:
            return Signal.HOLD

        window = history[-(self.lookback + 1):-1]
        highest = max(c.high for c in window)
        avg_vol = sum(c.volume for c in window) / len(window)
        lowest = min(c.low for c in window[-5:]) if len(window) >= 5 else candle.low

        if candle.close > highest and candle.volume > avg_vol * self.volume_mult and not ctx.positions:
            size = (ctx.account.cash * 0.9) / candle.close
            impact = ctx.get_impact_price(candle.market, Side.LONG, size)
            if impact and abs(impact - candle.close) / candle.close > 0.002:
                ctx.log(f"Skip entry: impact too high ({abs(impact - candle.close)/candle.close*10000:.0f}bps)")
                return Signal.HOLD
            if size > 0:
                ctx.market_order(candle.market, Side.LONG, size)
                ctx.stop_order(candle.market, Side.SHORT, size, lowest)

        elif candle.close < lowest and ctx.positions:
            ctx.close_position(candle.market)
            ctx.cancel_all(candle.market)

        return Signal.HOLD

    def reset(self) -> None:
        pass
`
