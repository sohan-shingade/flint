"""Cross-Venue Funding Dislocation v2 — with spot hedge and basis.

Upgrades from v1:
1. Uses multi-market ctx.get_candles("SOL") for spot price → real basis calc
2. Trades both perp (short) and tracks spot as hedge reference
3. Computes perp premium vs spot (not just vs rolling average)

To backtest with spot data:
    flint data download --market SOL --days 365
    flint backtest strategies/user/funding_dislocation_v2.py --market SOL-PERP -p 6m

The engine auto-loads SOL spot data from DB for basis calculation.
Cross-venue funding benchmark uses stored funding rate data from DuckDB.
"""
from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from flint.indicators import sma, z_score, rsi
from typing import List, Dict


class FundingDislocationV2(Strategy):
    """Short rich perps when funding is extreme, with spot basis confirmation.

    Uses:
    - ctx.get_candles("SOL") for spot price (basis calculation)
    - Price z-score as funding proxy (until real cross-venue data is wired)
    - Perp premium vs spot as basis confirmation
    """

    def __init__(
        self,
        funding_lookback: int = 48,
        z_entry: float = 2.0,
        z_exit: float = 0.5,
        basis_entry_bps: float = 30,  # 30 basis points premium to enter
        max_hold: int = 168,
        stop_pct: float = 3.0,
    ):
        self.funding_lookback = funding_lookback
        self.z_entry = z_entry
        self.z_exit = z_exit
        self.basis_entry = basis_entry_bps / 10000
        self.max_hold = max_hold
        self.stop_pct = stop_pct / 100
        self._entry_price = 0.0
        self._hold_count = 0
        self._in_position = False

    @property
    def name(self) -> str:
        return f"FundingV2(z={self.z_entry})"

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "funding_lookback": {"type": "int", "low": 24, "high": 96, "default": 48},
            "z_entry": {"type": "float", "low": 1.5, "high": 3.5, "default": 2.0},
            "z_exit": {"type": "float", "low": 0.2, "high": 1.0, "default": 0.5},
            "basis_entry_bps": {"type": "float", "low": 10, "high": 100, "default": 30},
            "max_hold": {"type": "int", "low": 48, "high": 336, "default": 168},
            "stop_pct": {"type": "float", "low": 1.0, "high": 5.0, "default": 3.0},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < self.funding_lookback + 2:
            return Signal.HOLD

        # ── Funding proxy: price z-score ──
        funding_z = z_score(history, self.funding_lookback)

        # ── Basis calculation ──
        # Try to get spot price from multi-market data
        basis = 0.0
        spot_market = candle.market.replace("-PERP", "")

        if ctx is not None:
            spot_candles = ctx.get_candles(spot_market, 5)
            if spot_candles and len(spot_candles) > 0:
                spot_price = spot_candles[-1].close
                if spot_price > 0:
                    # Real basis: (perp - spot) / spot
                    basis = (candle.close - spot_price) / spot_price
            else:
                # Fallback: perp price vs its own rolling average
                avg = sma(history, min(24, len(history)))
                if avg > 0:
                    basis = (candle.close - avg) / avg
        else:
            # No context: use rolling average as fair value proxy
            avg = sma(history, min(24, len(history)))
            if avg > 0:
                basis = (candle.close - avg) / avg

        # ── Position management ──
        if self._in_position:
            self._hold_count += 1

            # Exit: funding normalized
            if abs(funding_z) < self.z_exit:
                self._in_position = False
                return Signal.BUY

            # Exit: basis compressed (premium gone)
            if basis < 0:
                self._in_position = False
                return Signal.BUY

            # Exit: max hold time
            if self._hold_count >= self.max_hold:
                self._in_position = False
                return Signal.BUY

            # Exit: stop-loss
            if self._entry_price > 0:
                adverse = (candle.close - self._entry_price) / self._entry_price
                if adverse > self.stop_pct:
                    self._in_position = False
                    return Signal.BUY

            return Signal.HOLD

        # ── Entry: short when funding is extreme AND basis is positive ──
        if funding_z > self.z_entry and basis > self.basis_entry:
            self._in_position = True
            self._entry_price = candle.close
            self._hold_count = 0
            return Signal.SELL

        return Signal.HOLD

    def reset(self) -> None:
        self._entry_price = 0.0
        self._hold_count = 0
        self._in_position = False
