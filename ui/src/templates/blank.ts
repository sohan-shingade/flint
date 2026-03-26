export const BLANK_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List


class MyStrategy(Strategy):
    """Blank strategy template — v2 execution context.

    Uses ctx.market_order() for entries with impact checks and stop-losses.
    Customize the on_candle logic below.
    """
    def __init__(self, lookback=20, stop_pct=3.0):
        self.lookback = lookback
        self.stop_pct = stop_pct / 100

    @property
    def name(self) -> str:
        return "my_strategy"

    @classmethod
    def parameters(cls):
        return {
            "lookback": {"type": "int", "low": 5, "high": 50, "default": 20},
            "stop_pct": {"type": "float", "low": 1.0, "high": 8.0, "default": 3.0},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if ctx is None or len(history) < self.lookback:
            return Signal.HOLD

        # --- Your entry logic here ---
        # Example: uncomment to enter long with impact check + stop-loss
        # if not ctx.positions:
        #     size = (ctx.account.cash * 0.9) / candle.close
        #     impact = ctx.get_impact_price(candle.market, Side.LONG, size)
        #     if impact and abs(impact - candle.close) / candle.close > 0.002:
        #         return Signal.HOLD  # skip if impact too high
        #     if size > 0:
        #         ctx.market_order(candle.market, Side.LONG, size)
        #         ctx.stop_order(candle.market, Side.SHORT, size,
        #                        candle.close * (1 - self.stop_pct))

        return Signal.HOLD

    def reset(self) -> None:
        pass
`
