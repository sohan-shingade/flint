export const RSI_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List
import numpy as np


class RSIMeanReversion(Strategy):
    """RSI Mean Reversion — buy oversold, sell overbought.

    Uses v2 execution: stop-loss, impact checks, proper sizing.
    Trend filter: only mean-revert in the direction of a 100-bar SMA.
    """
    def __init__(self, period=14, oversold=30, overbought=70, stop_pct=5.0, trend_period=100):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.stop_pct = stop_pct / 100
        self.trend_period = trend_period

    @property
    def name(self) -> str:
        return f"RSI({self.period})"

    @classmethod
    def parameters(cls):
        return {
            "period": {"type": "int", "low": 5, "high": 30, "default": 14},
            "oversold": {"type": "float", "low": 15, "high": 40, "default": 30},
            "overbought": {"type": "float", "low": 60, "high": 85, "default": 70},
            "stop_pct": {"type": "float", "low": 2.0, "high": 10.0, "default": 5.0},
            "trend_period": {"type": "int", "low": 50, "high": 200, "default": 100},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        min_bars = max(self.period + 1, self.trend_period)
        if ctx is None or len(history) < min_bars:
            return Signal.HOLD
        closes = [c.close for c in history[-(self.period + 1):]]
        deltas = np.diff(closes)
        avg_gain = float(np.mean(np.where(deltas > 0, deltas, 0)))
        avg_loss = float(np.mean(np.where(deltas < 0, -deltas, 0)))
        rsi = 100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss > 0 else 100

        # Trend filter: 100-bar SMA
        trend_closes = [c.close for c in history[-self.trend_period:]]
        sma = np.mean(trend_closes)
        uptrend = candle.close > sma

        if not ctx.positions:
            side = None
            if uptrend and rsi < self.oversold:
                side = Side.LONG
            elif not uptrend and rsi > self.overbought:
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
            if 40 < rsi < 60:
                ctx.close_position(candle.market)
                ctx.cancel_all(candle.market)
        return Signal.HOLD

    def reset(self) -> None:
        pass
`
