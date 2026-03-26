export const STOP_LOSS_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List
import numpy as np


class MomentumWithStops(Strategy):
    """Momentum with Stop-Loss & Take-Profit (v2 Context API).

    Demonstrates the ExecutionContext order management:
    - ctx.market_order() to enter positions
    - ctx.stop_order() to attach stop-loss
    - ctx.take_profit_order() to set profit target

    Enters on breakout with automatic risk management.
    """
    def __init__(self, lookback=20, entry_pct=3.0, stop_pct=2.0, tp_pct=6.0):
        self.lookback = lookback
        self.entry_pct = entry_pct / 100
        self.stop_pct = stop_pct / 100
        self.tp_pct = tp_pct / 100
        self._in_trade = False

    @property
    def name(self) -> str:
        return f"MomentumStops({self.lookback})"

    @classmethod
    def parameters(cls):
        return {
            "lookback": {"type": "int", "low": 10, "high": 50, "default": 20},
            "entry_pct": {"type": "float", "low": 1.0, "high": 8.0, "default": 3.0},
            "stop_pct": {"type": "float", "low": 1.0, "high": 5.0, "default": 2.0},
            "tp_pct": {"type": "float", "low": 2.0, "high": 15.0, "default": 6.0},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if ctx is None or len(history) < self.lookback:
            return Signal.HOLD

        if not ctx.positions and not self._in_trade:
            old_price = history[-self.lookback].close
            ret = (candle.close - old_price) / old_price

            if ret > self.entry_pct:
                # Strong upward momentum — go long with stops
                size = (ctx.account.cash * 0.9) / candle.close
                impact = ctx.get_impact_price(candle.market, Side.LONG, size)
                if impact and abs(impact - candle.close) / candle.close > 0.002:
                    return Signal.HOLD
                if size > 0:
                    ctx.market_order(candle.market, Side.LONG, size)
                    ctx.stop_order(candle.market, Side.SHORT, size,
                                   candle.close * (1 - self.stop_pct))
                    ctx.take_profit_order(candle.market, Side.SHORT, size,
                                          candle.close * (1 + self.tp_pct))
                    self._in_trade = True

        elif ctx.positions:
            self._in_trade = True
        else:
            self._in_trade = False

        return Signal.HOLD

    def reset(self) -> None:
        self._in_trade = False
`
