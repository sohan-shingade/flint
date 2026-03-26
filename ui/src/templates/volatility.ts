export const VOLATILITY_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List
import numpy as np


class VolatilityBreakout(Strategy):
    """Volatility Breakout — trade expansions from low-vol squeezes.

    Detects when volatility compresses (Bollinger Band width narrows),
    then enters on the breakout direction. Uses v2 execution with ATR-based stop.
    """
    def __init__(self, bb_period=20, squeeze_threshold=0.03, atr_period=14):
        self.bb_period = bb_period
        self.squeeze_threshold = squeeze_threshold
        self.atr_period = atr_period
        self._was_squeezed = False

    @property
    def name(self) -> str:
        return f"VolBreakout({self.bb_period})"

    @classmethod
    def parameters(cls):
        return {
            "bb_period": {"type": "int", "low": 10, "high": 40, "default": 20},
            "squeeze_threshold": {"type": "float", "low": 0.01, "high": 0.06, "default": 0.03},
            "atr_period": {"type": "int", "low": 7, "high": 21, "default": 14},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < max(self.bb_period, self.atr_period) + 1:
            return Signal.HOLD
        if ctx is None:
            return Signal.HOLD

        closes = np.array([c.close for c in history[-self.bb_period:]])
        sma = float(np.mean(closes))
        std = float(np.std(closes))
        if sma == 0:
            return Signal.HOLD

        # ATR for stop placement
        trs = []
        for i in range(-self.atr_period, 0):
            h, l, pc = history[i].high, history[i].low, history[i-1].close
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        atr = sum(trs) / len(trs)

        # Bollinger Band width as % of price
        bb_width = (2 * std) / sma

        # Squeeze detection
        is_squeezed = bb_width < self.squeeze_threshold

        if self._was_squeezed and not is_squeezed and not ctx.positions:
            # Breakout from squeeze
            if candle.close > sma:
                self._was_squeezed = False
                size = (ctx.account.cash * 0.9) / candle.close
                impact = ctx.get_impact_price(candle.market, Side.LONG, size)
                if impact and abs(impact - candle.close) / candle.close > 0.002:
                    return Signal.HOLD
                if size > 0:
                    ctx.market_order(candle.market, Side.LONG, size)
                    ctx.stop_order(candle.market, Side.SHORT, size,
                                   candle.close - 1.5 * atr)
                return Signal.HOLD
            elif candle.close < sma:
                self._was_squeezed = False
                size = (ctx.account.cash * 0.9) / candle.close
                impact = ctx.get_impact_price(candle.market, Side.SHORT, size)
                if impact and abs(impact - candle.close) / candle.close > 0.002:
                    return Signal.HOLD
                if size > 0:
                    ctx.market_order(candle.market, Side.SHORT, size)
                    ctx.stop_order(candle.market, Side.LONG, size,
                                   candle.close + 1.5 * atr)
                return Signal.HOLD

        self._was_squeezed = is_squeezed

        # Exit when price returns to mean
        if not is_squeezed and abs(candle.close - sma) / sma < 0.005 and ctx.positions:
            ctx.close_position(candle.market)
            ctx.cancel_all(candle.market)

        return Signal.HOLD

    def reset(self) -> None:
        self._was_squeezed = False
`
