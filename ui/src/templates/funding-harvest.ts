export const FUNDING_HARVEST_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List


class FundingHarvest(Strategy):
    """Funding Rate Harvest — Solana-native strategy.

    Reads REAL funding rates via ctx.get_funding_rates().
    Goes long when funding is deeply negative (longs get paid).
    Goes short when funding is deeply positive (shorts get paid).

    IMPORTANT: Download funding data in Data Explorer first!
    Returns HOLD when no funding data is available.
    """
    def __init__(self, entry_threshold=0.00003, exit_threshold=0.000005,
                 stop_loss_pct=0.05, lookback=8):
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.stop_loss_pct = stop_loss_pct
        self.lookback = lookback

    @property
    def name(self) -> str:
        return f"FundingHarvest({self.entry_threshold})"

    @classmethod
    def parameters(cls):
        return {
            "entry_threshold": {"type": "float", "low": 0.00001, "high": 0.0001, "default": 0.00003},
            "exit_threshold": {"type": "float", "low": 0.000001, "high": 0.00002, "default": 0.000005},
            "stop_loss_pct": {"type": "float", "low": 0.02, "high": 0.10, "default": 0.05},
            "lookback": {"type": "int", "low": 4, "high": 24, "default": 8},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if ctx is None or len(history) < self.lookback:
            return Signal.HOLD

        # Read real funding rates from the execution context
        rates = ctx.get_funding_rates(candle.market, lookback=self.lookback)
        if len(rates) < 3:
            return Signal.HOLD

        avg_funding = sum(r for _, r in rates) / len(rates)

        pos = ctx.position(candle.market)
        if pos is None:
            if avg_funding < -self.entry_threshold:
                size = (ctx.account.cash * 0.9) / candle.close
                impact = ctx.get_impact_price(candle.market, Side.LONG, size)
                if impact and abs(impact - candle.close) / candle.close > 0.002:
                    return Signal.HOLD
                if size > 0:
                    ctx.market_order(candle.market, Side.LONG, size)
                    ctx.stop_order(candle.market, Side.SHORT, size,
                                   candle.close * (1 - self.stop_loss_pct))
            elif avg_funding > self.entry_threshold:
                size = (ctx.account.cash * 0.9) / candle.close
                impact = ctx.get_impact_price(candle.market, Side.SHORT, size)
                if impact and abs(impact - candle.close) / candle.close > 0.002:
                    return Signal.HOLD
                if size > 0:
                    ctx.market_order(candle.market, Side.SHORT, size)
                    ctx.stop_order(candle.market, Side.LONG, size,
                                   candle.close * (1 + self.stop_loss_pct))
        else:
            if abs(avg_funding) < self.exit_threshold:
                ctx.close_position(candle.market)
                ctx.cancel_all(candle.market)

        return Signal.HOLD

    def reset(self) -> None:
        pass
`
