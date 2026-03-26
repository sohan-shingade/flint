export const RSI_MACD_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List
import numpy as np


class RSIMACDCombo(Strategy):
    """RSI + MACD Confluence — only trades when both agree.

    Fewer but higher quality signals. Uses v2 execution with
    stop-loss, impact checks, and proper position sizing.
    """
    def __init__(self, rsi_period=14, macd_fast=12, macd_slow=26, macd_signal=9,
                 rsi_oversold=30, rsi_overbought=70, stop_pct=4.0):
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.stop_pct = stop_pct / 100

    @property
    def name(self): return f"RSI-MACD({self.rsi_period}, {self.macd_fast}/{self.macd_slow})"

    @classmethod
    def parameters(cls):
        return {
            "rsi_period": {"type": "int", "low": 7, "high": 21, "default": 14},
            "macd_fast": {"type": "int", "low": 8, "high": 16, "default": 12},
            "macd_slow": {"type": "int", "low": 20, "high": 34, "default": 26},
            "stop_pct": {"type": "float", "low": 2.0, "high": 8.0, "default": 4.0},
        }

    def _ema(self, data, period):
        ema = [data[0]]
        m = 2 / (period + 1)
        for x in data[1:]: ema.append(x * m + ema[-1] * (1 - m))
        return ema

    def _rsi(self, closes, period):
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i-1]
            gains.append(max(d, 0)); losses.append(max(-d, 0))
        if len(gains) < period: return 50
        avg_g = sum(gains[-period:]) / period
        avg_l = sum(losses[-period:]) / period
        return 100 - 100 / (1 + avg_g / avg_l) if avg_l > 0 else 100

    def on_candle(self, candle, history, ctx=None):
        n = max(self.rsi_period + 1, self.macd_slow + self.macd_signal)
        if ctx is None or len(history) < n:
            return Signal.HOLD

        closes = [c.close for c in history]
        rsi = self._rsi(closes, self.rsi_period)
        fast = self._ema(closes, self.macd_fast)
        slow = self._ema(closes, self.macd_slow)
        macd = [f - s for f, s in zip(fast, slow)]
        sig = self._ema(macd[-self.macd_signal*2:], self.macd_signal)
        macd_bull = len(sig) >= 2 and macd[-1] > sig[-1] and macd[-2] <= sig[-2]
        macd_bear = len(sig) >= 2 and macd[-1] < sig[-1] and macd[-2] >= sig[-2]

        if not ctx.positions:
            side = None
            if rsi < self.rsi_oversold and macd_bull:
                side = Side.LONG
            elif rsi > self.rsi_overbought and macd_bear:
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

    def reset(self): pass
`
