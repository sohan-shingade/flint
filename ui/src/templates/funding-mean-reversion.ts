export const FUNDING_MEAN_REVERSION_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side
from typing import List, Optional
import numpy as np

class FundingMeanReversion(Strategy):
    """Bollinger Bands on hourly funding rate.

    Enters when funding rate touches a band, exits on mean reversion.
    Uses ctx.get_funding_rates() — needs funding data downloaded.
    """
    def __init__(self, bb_lookback=24, bb_std=2.0, max_hold_hours=12):
        self.bb_lookback = bb_lookback
        self.bb_std = bb_std
        self.max_hold_hours = max_hold_hours
        self.entry_ts = 0

    @property
    def name(self): return "FundingMeanReversion"
    def reset(self): self.entry_ts = 0

    @classmethod
    def parameters(cls):
        return {
            "bb_lookback": {"type": "int", "low": 12, "high": 48, "default": 24},
            "bb_std": {"type": "float", "low": 1.5, "high": 3.0, "default": 2.0},
            "max_hold_hours": {"type": "int", "low": 4, "high": 24, "default": 12},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if ctx is None:
            return Signal.HOLD
        rates = ctx.get_funding_rates(candle.market, lookback=self.bb_lookback)
        if len(rates) < self.bb_lookback:
            return Signal.HOLD
        values = np.array([r[1] for r in rates[-self.bb_lookback:]])
        mean = float(np.mean(values))
        std = float(np.std(values, ddof=1))
        if std < 1e-10:
            return Signal.HOLD
        upper = mean + self.bb_std * std
        lower = mean - self.bb_std * std
        current = values[-1]

        if self.entry_ts > 0:
            if (candle.ts - self.entry_ts) / 3600 >= self.max_hold_hours:
                ctx.close_position(candle.market)
                self.entry_ts = 0
            elif lower <= current <= upper:
                ctx.close_position(candle.market)
                self.entry_ts = 0
            return Signal.HOLD

        if current < lower:
            size = (ctx.account.cash * 0.5) / candle.close
            if size > 0:
                ctx.market_order(candle.market, Side.LONG, size)
                self.entry_ts = candle.ts
        elif current > upper:
            size = (ctx.account.cash * 0.5) / candle.close
            if size > 0:
                ctx.market_order(candle.market, Side.SHORT, size)
                self.entry_ts = candle.ts
        return Signal.HOLD
`
