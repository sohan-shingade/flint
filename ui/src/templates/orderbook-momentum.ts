export const ORDERBOOK_MOMENTUM_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List
import numpy as np


class OrderbookMomentum(Strategy):
    """Orderbook-Aware Momentum — only enters when liquidity supports the trade.

    Checks orderbook impact price before placing orders. If slippage
    exceeds threshold, skips the trade. Uses real L2 book data from Drift DLOB.

    FEATURES DEMONSTRATED:
    - ctx.get_orderbook() — raw orderbook access
    - ctx.get_impact_price() — volume-weighted fill price estimation
    - Slippage-aware position sizing

    Enable 'MARGIN TRACKING' for realistic leverage limits.
    """
    def __init__(self, lookback=20, entry_pct=3.0, max_slippage_bps=15.0, max_spread_bps=10.0):
        self.lookback = lookback
        self.entry_pct = entry_pct / 100
        self.max_slippage_bps = max_slippage_bps
        self.max_spread_bps = max_spread_bps
        self._in_trade = False

    @property
    def name(self): return f"OB-Momentum(lb={self.lookback})"

    @classmethod
    def parameters(cls):
        return {
            "lookback": {"type": "int", "low": 10, "high": 50, "default": 20},
            "entry_pct": {"type": "float", "low": 1.0, "high": 8.0, "default": 3.0},
            "max_slippage_bps": {"type": "float", "low": 5.0, "high": 30.0, "default": 15.0},
            "max_spread_bps": {"type": "float", "low": 3.0, "high": 20.0, "default": 10.0},
        }

    def on_candle(self, candle, history, ctx=None):
        if ctx is None or len(history) < self.lookback:
            return Signal.HOLD

        # Check if we should exit
        if ctx.positions:
            old = history[-5].close if len(history) >= 5 else history[0].close
            ret = (candle.close - old) / old
            if abs(ret) < 0.005:
                ctx.close_position(candle.market)
                self._in_trade = False
            return Signal.HOLD

        # Momentum signal
        old_price = history[-self.lookback].close
        ret = (candle.close - old_price) / old_price
        if abs(ret) < self.entry_pct:
            return Signal.HOLD

        # === ORDERBOOK CHECKS ===
        book = ctx.get_orderbook(candle.market)
        if book and book.bids and book.asks:
            # Check spread
            spread_bps = (book.asks[0].price - book.bids[0].price) / book.bids[0].price * 10000
            if spread_bps > self.max_spread_bps:
                ctx.log(f"Skip: spread too wide ({spread_bps:.1f}bps > {self.max_spread_bps}bps)")
                return Signal.HOLD

        # Check impact price
        side = Side.LONG if ret > 0 else Side.SHORT
        size = (ctx.account.cash * 0.9) / candle.close
        impact = ctx.get_impact_price(candle.market, side, size)
        if impact is not None:
            mid = candle.close
            slippage_bps = abs(impact - mid) / mid * 10000
            if slippage_bps > self.max_slippage_bps:
                ctx.log(f"Skip: impact too high ({slippage_bps:.1f}bps for {size:.1f} units)")
                return Signal.HOLD
            ctx.log(f"Impact OK: {slippage_bps:.1f}bps for {size:.1f} units")

        # Enter
        if size > 0:
            ctx.market_order(candle.market, side, size)
            ctx.stop_order(candle.market,
                Side.SHORT if side == Side.LONG else Side.LONG,
                size, candle.close * (0.97 if side == Side.LONG else 1.03))
            self._in_trade = True
        return Signal.HOLD

    def reset(self):
        self._in_trade = False
`
