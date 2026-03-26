export const CROSS_VENUE_PAIRS_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List


class CrossVenuePairs(Strategy):
    """Cross-Venue SOL/BTC Pairs — multi-venue, multi-market stat arb.

    Trades the SOL/BTC ratio across different venues.
    Short SOL on Drift, long BTC on Hyperliquid (or vice versa).
    Uses venue-specific margin and tracks per-venue P&L.

    FEATURES DEMONSTRATED:
    - venue="drift" / venue="hyperliquid" — multi-venue positions
    - ctx.venue_balance() — per-venue capital checks
    - ctx.position(market, venue=) — venue-specific position queries
    - ctx.get_candles("BTC-PERP") — cross-market data access

    SETUP: Download both SOL-PERP and BTC-PERP data.
    Enable 'MARGIN TRACKING' for realistic leverage limits.
    """
    def __init__(self, lookback=48, entry_z=2.0, exit_z=0.5, alloc_pct=0.4):
        self.lookback = lookback
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.alloc_pct = alloc_pct
        self._ratio_history = []

    @property
    def name(self): return f"XV-Pairs(z={self.entry_z})"

    @classmethod
    def parameters(cls):
        return {
            "lookback": {"type": "int", "low": 20, "high": 100, "default": 48},
            "entry_z": {"type": "float", "low": 1.0, "high": 3.0, "default": 2.0},
            "exit_z": {"type": "float", "low": 0.1, "high": 1.0, "default": 0.5},
            "alloc_pct": {"type": "float", "low": 0.2, "high": 0.5, "default": 0.4},
        }

    def on_candle(self, candle, history, ctx=None):
        if ctx is None: return Signal.HOLD

        btc = ctx.get_candles("BTC-PERP", self.lookback + 10)
        if len(btc) < self.lookback or len(history) < self.lookback:
            return Signal.HOLD

        # SOL/BTC ratio z-score
        ratio = candle.close / btc[-1].close if btc[-1].close else 0
        self._ratio_history.append(ratio)
        if len(self._ratio_history) < self.lookback: return Signal.HOLD

        window = self._ratio_history[-self.lookback:]
        mean = sum(window) / len(window)
        std = (sum((r - mean)**2 for r in window) / len(window)) ** 0.5
        if std == 0: return Signal.HOLD
        z = (ratio - mean) / std

        sol_pos = ctx.position("SOL-PERP", venue="drift")
        btc_pos = ctx.position("BTC-PERP", venue="hyperliquid")
        in_trade = sol_pos is not None or btc_pos is not None

        # Exit
        if in_trade:
            if abs(z) < self.exit_z or abs(z) > 4.0:
                ctx.log(f"EXIT pairs: z={z:.2f}, ratio={ratio:.6f}")
                if sol_pos: ctx.close_position("SOL-PERP", venue="drift")
                if btc_pos: ctx.close_position("BTC-PERP", venue="hyperliquid")
            return Signal.HOLD

        # Entry — check venue balances
        drift_cash = ctx.venue_balance("drift")
        hl_cash = ctx.venue_balance("hyperliquid")
        sol_alloc = min(drift_cash * self.alloc_pct, ctx.account.cash * self.alloc_pct)
        btc_alloc = min(hl_cash * self.alloc_pct, ctx.account.cash * self.alloc_pct)

        if z > self.entry_z:
            # SOL rich → short SOL on Drift, long BTC on Hyperliquid
            sol_size = sol_alloc / candle.close
            btc_size = btc_alloc / btc[-1].close
            if sol_size > 0 and btc_size > 0:
                ctx.market_order("SOL-PERP", Side.SHORT, sol_size, venue="drift")
                ctx.market_order("BTC-PERP", Side.LONG, btc_size, venue="hyperliquid")
                ctx.log(f"ENTRY: Short SOL@Drift + Long BTC@HL | z={z:.2f}")
        elif z < -self.entry_z:
            sol_size = sol_alloc / candle.close
            btc_size = btc_alloc / btc[-1].close
            if sol_size > 0 and btc_size > 0:
                ctx.market_order("SOL-PERP", Side.LONG, sol_size, venue="drift")
                ctx.market_order("BTC-PERP", Side.SHORT, btc_size, venue="hyperliquid")
                ctx.log(f"ENTRY: Long SOL@Drift + Short BTC@HL | z={z:.2f}")
        return Signal.HOLD

    def reset(self): self._ratio_history = []
`
