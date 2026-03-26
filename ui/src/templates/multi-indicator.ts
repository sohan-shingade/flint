export const MULTI_INDICATOR_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List
import numpy as np


class MultiIndicator(Strategy):
    """Multi-Indicator Confluence — RSI + MACD + Volume confirmation.

    Only enters when multiple indicators agree. Uses v2 execution
    with stop-loss, impact checks, and proper position sizing.
    Fewer trades but better win rate.
    """
    def __init__(self, rsi_period=14, macd_fast=12, macd_slow=26, vol_mult=1.5, stop_pct=3.0):
        self.rsi_period = rsi_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.vol_mult = vol_mult
        self.stop_pct = stop_pct / 100
        self._prev_macd = 0.0
        self._prev_signal = 0.0

    @property
    def name(self) -> str:
        return "MultiIndicator"

    @classmethod
    def parameters(cls):
        return {
            "rsi_period": {"type": "int", "low": 7, "high": 21, "default": 14},
            "macd_fast": {"type": "int", "low": 8, "high": 16, "default": 12},
            "macd_slow": {"type": "int", "low": 20, "high": 32, "default": 26},
            "vol_mult": {"type": "float", "low": 1.0, "high": 3.0, "default": 1.5},
            "stop_pct": {"type": "float", "low": 1.5, "high": 6.0, "default": 3.0},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        n = max(self.macd_slow + 9, self.rsi_period + 1, 20)
        if ctx is None or len(history) < n:
            return Signal.HOLD

        closes = np.array([c.close for c in history[-n:]])
        volumes = np.array([c.volume for c in history[-20:]])

        # RSI
        deltas = np.diff(closes[-(self.rsi_period + 1):])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = float(np.mean(gains)) if len(gains) > 0 else 0
        avg_loss = float(np.mean(losses)) if len(losses) > 0 else 1
        rsi = 100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss > 0 else 50

        # MACD
        ema_fast = float(np.mean(closes[-self.macd_fast:]))
        ema_slow = float(np.mean(closes[-self.macd_slow:]))
        macd = ema_fast - ema_slow
        signal_line = (macd + self._prev_macd) / 2

        # Volume confirmation
        avg_vol = float(np.mean(volumes)) if len(volumes) > 0 else 1
        vol_spike = candle.volume > avg_vol * self.vol_mult

        # Confluence: buy when RSI oversold + MACD cross up + volume spike
        buy_signal = (rsi < 35 and macd > signal_line and
                      self._prev_macd <= self._prev_signal and vol_spike)

        # Confluence: sell when RSI overbought + MACD cross down
        sell_signal = (rsi > 65 and macd < signal_line and
                       self._prev_macd >= self._prev_signal)

        self._prev_macd = macd
        self._prev_signal = signal_line

        if not ctx.positions:
            side = None
            if buy_signal:
                side = Side.LONG
            elif sell_signal:
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
            # Exit on opposing signal or RSI normalization
            if (buy_signal and sell_signal) or (45 < rsi < 55):
                ctx.close_position(candle.market)
                ctx.cancel_all(candle.market)
        return Signal.HOLD

    def reset(self) -> None:
        self._prev_macd = 0.0
        self._prev_signal = 0.0
`
