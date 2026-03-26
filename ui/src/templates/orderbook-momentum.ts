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
    - 50-bar SMA trend filter: only LONG above SMA, only SHORT below
    - Trailing stop via ctx.stop_order() for risk management

    Enable 'MARGIN TRACKING' for realistic leverage limits.
    """
    def __init__(self, lookback=20, entry_pct=5.0, stop_pct=3.0, max_slippage_bps=15.0, max_spread_bps=10.0):
        self.lookback = lookback
        self.entry_pct = entry_pct / 100
        self.stop_pct = stop_pct / 100
        self.max_slippage_bps = max_slippage_bps
        self.max_spread_bps = max_spread_bps
        self._in_trade = False
        self._cooldown = 0
        self._entry_price = 0.0
        self._trade_side = None

    @property
    def name(self): return f"OB-Momentum(lb={self.lookback})"

    @classmethod
    def parameters(cls):
        return {
            "lookback": {"type": "int", "low": 10, "high": 50, "default": 20},
            "entry_pct": {"type": "float", "low": 2.0, "high": 10.0, "default": 5.0},
            "stop_pct": {"type": "float", "low": 1.0, "high": 6.0, "default": 3.0},
            "max_slippage_bps": {"type": "float", "low": 5.0, "high": 30.0, "default": 15.0},
            "max_spread_bps": {"type": "float", "low": 3.0, "high": 20.0, "default": 10.0},
        }

    def on_candle(self, candle, history, ctx=None):
        if ctx is None or len(history) < max(self.lookback, 50):
            return Signal.HOLD

        # Tick down cooldown timer
        if self._cooldown > 0:
            self._cooldown -= 1

        # 50-bar SMA trend filter
        sma50 = np.mean([c.close for c in history[-50:]])
        above_sma = candle.close > sma50
        below_sma = candle.close < sma50

        # Check if we should exit via trailing stop logic
        if ctx.positions:
            if self._trade_side == Side.LONG:
                pnl_pct = (candle.close - self._entry_price) / self._entry_price
                if pnl_pct < -self.stop_pct:
                    ctx.close_position(candle.market)
                    ctx.cancel_all(candle.market)
                    self._in_trade = False
                    self._cooldown = 5
            elif self._trade_side == Side.SHORT:
                pnl_pct = (self._entry_price - candle.close) / self._entry_price
                if pnl_pct < -self.stop_pct:
                    ctx.close_position(candle.market)
                    ctx.cancel_all(candle.market)
                    self._in_trade = False
                    self._cooldown = 5
            return Signal.HOLD

        # Cooldown after exit — wait before re-entering
        if self._cooldown > 0:
            return Signal.HOLD

        # Momentum signal
        old_price = history[-self.lookback].close
        ret = (candle.close - old_price) / old_price
        if abs(ret) < self.entry_pct:
            return Signal.HOLD

        # Trend filter: only LONG above SMA, only SHORT below SMA
        if ret > 0 and not above_sma:
            return Signal.HOLD
        if ret < 0 and not below_sma:
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

        # Enter with stop order for risk management
        if size > 0:
            ctx.market_order(candle.market, side, size)
            stop_side = Side.SHORT if side == Side.LONG else Side.LONG
            stop_price = candle.close * ((1 - self.stop_pct) if side == Side.LONG else (1 + self.stop_pct))
            ctx.stop_order(candle.market, stop_side, size, stop_price)
            self._in_trade = True
            self._entry_price = candle.close
            self._trade_side = side
        return Signal.HOLD

    def reset(self):
        self._in_trade = False
        self._cooldown = 0
        self._entry_price = 0.0
        self._trade_side = None
`
