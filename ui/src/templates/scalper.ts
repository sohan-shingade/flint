export const SCALPER_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List
import numpy as np


class Scalper(Strategy):
    """Scalper — mean-reversion in calm markets.

    Enters on extreme short-term deviation from VWAP-like average.
    Exits when price reverts toward the mean. Uses a volatility filter
    to only trade when recent volatility is below average (calm markets
    mean-revert better). Wider thresholds reduce trade count and fees.
    """
    def __init__(self, window=20, entry_dev=0.015, exit_dev=0.005, stop_pct=2.5):
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
            "window": {"type": "int", "low": 10, "high": 40, "default": 20},
            "entry_dev": {"type": "float", "low": 0.008, "high": 0.03, "default": 0.015},
            "exit_dev": {"type": "float", "low": 0.002, "high": 0.01, "default": 0.005},
            "stop_pct": {"type": "float", "low": 1.0, "high": 5.0, "default": 2.5},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if ctx is None or len(history) < self.window * 2:
            return Signal.HOLD

        recent = history[-self.window:]
        total_vol = sum(c.volume for c in recent)
        if total_vol == 0:
            return Signal.HOLD
        vwap = sum(c.close * c.volume for c in recent) / total_vol
        deviation = (candle.close - vwap) / vwap

        # Volatility filter: only trade when recent vol is below longer-term average
        recent_returns = [abs(history[i].close - history[i - 1].close) / history[i - 1].close
                          for i in range(-self.window + 1, 0)]
        long_returns = [abs(history[i].close - history[i - 1].close) / history[i - 1].close
                        for i in range(-self.window * 2 + 1, -self.window)]
        recent_vol = np.mean(recent_returns)
        long_vol = np.mean(long_returns)

        if not ctx.positions:
            # Skip entries in volatile markets — mean reversion fails when trending
            if recent_vol > long_vol:
                return Signal.HOLD

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
