export const SCALPER_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List
import numpy as np


class Scalper(Strategy):
    """Scalper — fast mean-reversion on short timeframes.

    Enters on extreme short-term deviation from VWAP-like average.
    Exits quickly near the mean. Designed for 5m-15m candles.
    Uses v2 execution with tight stops and impact checks.
    """
    def __init__(self, window=10, entry_dev=0.008, exit_dev=0.002, stop_pct=1.5):
        self.window = window
        self.entry_dev = entry_dev
        self.exit_dev = exit_dev
        self.stop_pct = stop_pct / 100

    @property
    def name(self) -> str:
        return f"Scalper({self.window})"

    @classmethod
    def parameters(cls):
        return {
            "window": {"type": "int", "low": 5, "high": 20, "default": 10},
            "entry_dev": {"type": "float", "low": 0.003, "high": 0.02, "default": 0.008},
            "exit_dev": {"type": "float", "low": 0.001, "high": 0.005, "default": 0.002},
            "stop_pct": {"type": "float", "low": 0.5, "high": 3.0, "default": 1.5},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if ctx is None or len(history) < self.window:
            return Signal.HOLD

        recent = history[-self.window:]
        total_vol = sum(c.volume for c in recent)
        if total_vol == 0:
            return Signal.HOLD
        vwap = sum(c.close * c.volume for c in recent) / total_vol
        deviation = (candle.close - vwap) / vwap

        if not ctx.positions:
            side = None
            if deviation < -self.entry_dev:
                side = Side.LONG
            elif deviation > self.entry_dev:
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
            if abs(deviation) < self.exit_dev:
                ctx.close_position(candle.market)
                ctx.cancel_all(candle.market)

        return Signal.HOLD

    def reset(self) -> None:
        pass
`
