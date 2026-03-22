"""Cross-Venue Funding Dislocation Strategy — Drift-only v1.

Based on the strategy breakdown in cross_venue_funding_dislocation_strategy.md.

This is the Layer 1 signal backtest — single venue (Drift), no spot hedge.
Tests whether extreme funding rate dislocations on Drift are mean-reverting
and whether shorting into high funding generates alpha from carry.

Signal:
1. Compute rolling z-score of Drift funding rate (approximated from price momentum)
2. Compute basis (price deviation from rolling mean as premium proxy)
3. Enter short when both funding z-score AND basis are elevated
4. Exit when funding normalizes or basis compresses

PnL decomposition:
- Funding carry: collected from longs when short during positive funding
- Basis compression: profit when premium shrinks back to fair
- Fees + slippage: cost of entry/exit

Limitations of this v1 backtest:
- No cross-venue benchmark (uses rolling mean as proxy)
- No spot hedge (net directional exposure)
- Funding rate approximated from price momentum (real funding data improves this)
- Single instrument, not delta-neutral

To run:
    flint backtest strategies/user/funding_dislocation.py --market SOL-PERP --period 1y
"""
from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from flint.indicators import sma, z_score, atr, rsi
from typing import List, Dict
import numpy as np


class FundingDislocation(Strategy):
    """Short rich perps when funding is extreme, exit on normalization.

    Parameters:
        funding_lookback: window for rolling funding proxy (hours)
        z_entry: z-score threshold to enter short
        z_exit: z-score threshold to exit (funding normalized)
        basis_lookback: window for basis/premium estimation
        basis_entry_pct: minimum premium to confirm entry (%)
        max_hold: maximum holding period (candles) before forced exit
        stop_pct: stop-loss on adverse price move (%)
    """

    def __init__(
        self,
        funding_lookback: int = 48,
        z_entry: float = 2.0,
        z_exit: float = 0.5,
        basis_lookback: int = 24,
        basis_entry_pct: float = 0.5,
        max_hold: int = 168,  # 7 days at 1h
        stop_pct: float = 3.0,
    ):
        self.funding_lookback = funding_lookback
        self.z_entry = z_entry
        self.z_exit = z_exit
        self.basis_lookback = basis_lookback
        self.basis_entry_pct = basis_entry_pct / 100
        self.max_hold = max_hold
        self.stop_pct = stop_pct / 100
        self._entry_price = 0.0
        self._hold_count = 0
        self._in_position = False

    @property
    def name(self) -> str:
        return f"FundingDislocation(z={self.z_entry}, basis={self.basis_entry_pct*100:.1f}%)"

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "funding_lookback": {"type": "int", "low": 24, "high": 96, "default": 48},
            "z_entry": {"type": "float", "low": 1.5, "high": 3.5, "default": 2.0},
            "z_exit": {"type": "float", "low": 0.2, "high": 1.0, "default": 0.5},
            "basis_lookback": {"type": "int", "low": 12, "high": 72, "default": 24},
            "basis_entry_pct": {"type": "float", "low": 0.2, "high": 2.0, "default": 0.5},
            "max_hold": {"type": "int", "low": 48, "high": 336, "default": 168},
            "stop_pct": {"type": "float", "low": 1.0, "high": 5.0, "default": 3.0},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        n = max(self.funding_lookback, self.basis_lookback)
        if len(history) < n + 2:
            return Signal.HOLD

        # ── Compute funding rate proxy ──
        # When price is elevated above long-term average, longs are crowded,
        # funding tends positive (shorts get paid). Use z-score of price
        # deviation from rolling mean as funding proxy.
        funding_z = z_score(history, self.funding_lookback)

        # ── Compute basis (premium proxy) ──
        # Basis = how far current price is above the shorter-term average
        avg_price = sma(history, self.basis_lookback)
        if avg_price == 0:
            return Signal.HOLD
        basis = (candle.close - avg_price) / avg_price

        # ── Position management ──
        if self._in_position:
            self._hold_count += 1

            # Exit conditions (from section 11 of the doc):
            # 1. Funding z-score compressed below exit threshold
            if abs(funding_z) < self.z_exit:
                self._in_position = False
                return Signal.BUY  # close short

            # 2. Basis compressed (premium gone)
            if basis < 0:
                self._in_position = False
                return Signal.BUY

            # 3. Max holding time
            if self._hold_count >= self.max_hold:
                self._in_position = False
                return Signal.BUY

            # 4. Stop-loss on adverse move (basis widening)
            if self._entry_price > 0:
                adverse_move = (candle.close - self._entry_price) / self._entry_price
                if adverse_move > self.stop_pct:
                    self._in_position = False
                    return Signal.BUY

            return Signal.HOLD

        # ── Entry conditions (from section 10 of the doc) ──
        # Enter short when BOTH conditions are met:
        # 1. Funding z-score is extreme positive (longs crowded, shorts get paid)
        # 2. Basis (premium) is positive (perp is rich vs fair value)
        entry_conditions = (
            funding_z > self.z_entry           # 1. extreme funding
            and basis > self.basis_entry_pct   # 2. perp is rich (premium)
        )

        if entry_conditions:
            self._in_position = True
            self._entry_price = candle.close
            self._hold_count = 0
            return Signal.SELL  # open short

        return Signal.HOLD

    def reset(self) -> None:
        self._entry_price = 0.0
        self._hold_count = 0
        self._in_position = False
