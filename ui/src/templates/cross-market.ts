export const CROSS_MARKET_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List


class BtcCorrelation(Strategy):
    """Cross-Market Correlation — trade SOL based on BTC momentum.

    Uses ctx.get_candles('BTC-PERP') to peek at BTC while trading SOL.
    Goes long SOL when BTC shows strong upward momentum.
    Demonstrates multi-market data access (v2 feature).

    Run on SOL-PERP with BTC-PERP data also in the DB.
    """
    def __init__(self, btc_lookback=12, threshold_pct=2.0, stop_pct=3.0):
        self.btc_lookback = btc_lookback
        self.threshold = threshold_pct / 100
        self.stop_pct = stop_pct / 100

    @property
    def name(self) -> str:
        return f"BTC-Correlation({self.btc_lookback})"

    @classmethod
    def parameters(cls):
        return {
            "btc_lookback": {"type": "int", "low": 6, "high": 48, "default": 12},
            "threshold_pct": {"type": "float", "low": 0.5, "high": 5.0, "default": 2.0},
            "stop_pct": {"type": "float", "low": 1.0, "high": 8.0, "default": 3.0},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if ctx is None or len(history) < self.btc_lookback:
            return Signal.HOLD

        # Check BTC momentum via cross-market access
        btc = ctx.get_candles("BTC-PERP", self.btc_lookback)
        if len(btc) < 2:
            return Signal.HOLD

        btc_return = (btc[-1].close - btc[0].close) / btc[0].close

        if not ctx.positions:
            if btc_return > self.threshold:
                size = (ctx.account.cash * 0.9) / candle.close
                impact = ctx.get_impact_price(candle.market, Side.LONG, size)
                if impact and abs(impact - candle.close) / candle.close > 0.002:
                    return Signal.HOLD
                if size > 0:
                    ctx.market_order(candle.market, Side.LONG, size)
                    ctx.stop_order(candle.market, Side.SHORT, size,
                                   candle.close * (1 - self.stop_pct))
            elif btc_return < -self.threshold:
                size = (ctx.account.cash * 0.9) / candle.close
                impact = ctx.get_impact_price(candle.market, Side.SHORT, size)
                if impact and abs(impact - candle.close) / candle.close > 0.002:
                    return Signal.HOLD
                if size > 0:
                    ctx.market_order(candle.market, Side.SHORT, size)
                    ctx.stop_order(candle.market, Side.LONG, size,
                                   candle.close * (1 + self.stop_pct))
        else:
            # Exit when BTC momentum reverses
            if abs(btc_return) < self.threshold * 0.3:
                ctx.close_position(candle.market)
                ctx.cancel_all(candle.market)

        return Signal.HOLD

    def reset(self) -> None:
        pass
`
