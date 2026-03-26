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
    Uses a 50-bar SMA trend filter to avoid fighting the primary trend,
    and a cooldown period after stop-outs to avoid whipsaw re-entries.
    """
    def __init__(self, lookback=30, entry_pct=3.0, stop_pct=5.0, tp_pct=12.0):
        self.lookback = lookback
        self.entry_pct = entry_pct / 100
        self.stop_pct = stop_pct / 100
        self.tp_pct = tp_pct / 100
        self._in_trade = False
        self._cooldown = 0

    @property
    def name(self) -> str:
        return f"MomentumStops({self.lookback})"

    @classmethod
    def parameters(cls):
        return {
            "lookback": {"type": "int", "low": 10, "high": 60, "default": 30},
            "entry_pct": {"type": "float", "low": 1.0, "high": 8.0, "default": 3.0},
            "stop_pct": {"type": "float", "low": 2.0, "high": 8.0, "default": 5.0},
            "tp_pct": {"type": "float", "low": 5.0, "high": 20.0, "default": 12.0},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if ctx is None or len(history) < max(self.lookback, 50):
            return Signal.HOLD

        # Tick down cooldown timer
        if self._cooldown > 0:
            self._cooldown -= 1

        # 50-bar SMA trend filter
        sma50 = np.mean([c.close for c in history[-50:]])
        above_sma = candle.close > sma50
        below_sma = candle.close < sma50

        if not ctx.positions and not self._in_trade:
            if self._cooldown > 0:
                return Signal.HOLD

            old_price = history[-self.lookback].close
            ret = (candle.close - old_price) / old_price

            if ret > self.entry_pct and above_sma:
                # Strong upward momentum + trending up — go long
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

            elif ret < -self.entry_pct and below_sma:
                # Strong downward momentum + trending down — go short
                size = (ctx.account.cash * 0.9) / candle.close
                impact = ctx.get_impact_price(candle.market, Side.SHORT, size)
                if impact and abs(impact - candle.close) / candle.close > 0.002:
                    return Signal.HOLD
                if size > 0:
                    ctx.market_order(candle.market, Side.SHORT, size)
                    ctx.stop_order(candle.market, Side.LONG, size,
                                   candle.close * (1 + self.stop_pct))
                    ctx.take_profit_order(candle.market, Side.LONG, size,
                                          candle.close * (1 - self.tp_pct))
                    self._in_trade = True

        elif ctx.positions:
            self._in_trade = True
        else:
            # Position was closed (stopped out or TP hit) — start cooldown
            if self._in_trade:
                self._cooldown = 10
            self._in_trade = False

        return Signal.HOLD

    def reset(self) -> None:
        self._in_trade = False
        self._cooldown = 0
`
