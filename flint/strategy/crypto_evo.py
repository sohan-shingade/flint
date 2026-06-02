"""CryptoEvo — MACD + Bollinger Band hybrid with regime detection.

Blends trend-following (MACD) and mean-reversion (Bollinger) signals using
a tunable weight W. Three conditional filters (volume, ATR, trend direction)
can suppress signals per-regime. Continuous position sizing from -1 to +1,
scaled by max_position_size.

13 optimizable genes across three sections:
  A — core indicator params (6): fast_ema, slow_ema, signal_period,
      bb_period, bb_std_dev, signal_weight
  B — regime-conditional filters (6): vol/atr/trend × on/regime_mask
  C — position sizing (1): max_position_size

Ported from QuantConnect LEAN to Flint v2 strategy API.
"""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Dict, List, Optional

import numpy as np

from ..models import Candle, Signal, Side
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext


class CryptoEvoStrategy(Strategy):

    def __init__(
        self,
        # Section A — core indicators
        fast_ema: int = 12,
        slow_ema: int = 26,
        signal_period: int = 9,
        bb_period: int = 20,
        bb_std_dev: float = 2.5,
        signal_weight: float = 0.75,
        # Section B — regime filters
        vol_filter_on: int = 0,
        vol_filter_regime: int = 0,
        atr_filter_on: int = 0,
        atr_filter_regime: int = 0,
        trend_filter_on: int = 1,
        trend_filter_regime: int = 1,
        # Section C — sizing
        max_position_size: float = 0.6,
    ) -> None:
        # Clamp to legal ranges
        self.fast_ema = max(5, min(20, fast_ema))
        self.slow_ema = max(15, min(50, slow_ema))
        self.signal_period = max(5, min(15, signal_period))
        self.bb_period = max(15, min(40, bb_period))
        self.bb_std_dev = max(1.5, min(3.0, bb_std_dev))
        self.signal_weight = max(0.0, min(1.0, signal_weight))
        self.max_pos = max(0.3, min(1.0, max_position_size))

        # Ensure fast < slow
        if self.fast_ema >= self.slow_ema:
            self.fast_ema = max(5, self.slow_ema - 5)

        self.vol_filter_on = vol_filter_on
        self.vol_filter_regime = vol_filter_regime
        self.atr_filter_on = atr_filter_on
        self.atr_filter_regime = atr_filter_regime
        self.trend_filter_on = trend_filter_on
        self.trend_filter_regime = trend_filter_regime

        # Internal state — set in reset()
        self._fast_ema_val: float = 0.0
        self._slow_ema_val: float = 0.0
        self._signal_ema_val: float = 0.0
        self._ema200_val: float = 0.0
        self._macd_initialized: bool = False
        self._ema200_initialized: bool = False
        self._prev_histogram: float = 0.0
        self._atr_history: deque = deque(maxlen=50)
        self._vol_sma_buf: deque = deque(maxlen=20)
        self._current_regime: int = 0
        self._bars_in_trade: int = 0

    @property
    def name(self) -> str:
        return (
            f"CryptoEvo(f{self.fast_ema}/s{self.slow_ema}/"
            f"sig{self.signal_period}, "
            f"bb{self.bb_period}/{self.bb_std_dev:.1f}, "
            f"W{self.signal_weight:.2f})"
        )

    def reset(self) -> None:
        self._fast_ema_val = 0.0
        self._slow_ema_val = 0.0
        self._signal_ema_val = 0.0
        self._ema200_val = 0.0
        self._macd_initialized = False
        self._ema200_initialized = False
        self._prev_histogram = 0.0
        self._atr_history = deque(maxlen=50)
        self._vol_sma_buf = deque(maxlen=20)
        self._current_regime = 0
        self._bars_in_trade = 0

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            # Section A
            "fast_ema":        {"type": "int",   "low": 5,   "high": 20,  "default": 12},
            "slow_ema":        {"type": "int",   "low": 15,  "high": 50,  "default": 26},
            "signal_period":   {"type": "int",   "low": 5,   "high": 15,  "default": 9},
            "bb_period":       {"type": "int",   "low": 15,  "high": 40,  "default": 20},
            "bb_std_dev":      {"type": "float", "low": 1.5, "high": 3.0, "default": 2.5},
            "signal_weight":   {"type": "float", "low": 0.0, "high": 1.0, "default": 0.75},
            # Section B
            "vol_filter_on":       {"type": "int", "low": 0, "high": 1, "default": 0},
            "vol_filter_regime":   {"type": "int", "low": 0, "high": 3, "default": 0},
            "atr_filter_on":       {"type": "int", "low": 0, "high": 1, "default": 0},
            "atr_filter_regime":   {"type": "int", "low": 0, "high": 3, "default": 0},
            "trend_filter_on":     {"type": "int", "low": 0, "high": 1, "default": 1},
            "trend_filter_regime": {"type": "int", "low": 0, "high": 3, "default": 1},
            # Section C
            "max_position_size":   {"type": "float", "low": 0.3, "high": 1.0, "default": 0.6},
        }

    # ── Incremental EMA updates ──────────────────────────

    def _init_macd(self, history: List[Candle]) -> bool:
        """Seed MACD EMAs from history. Returns True when ready."""
        if len(history) < self.slow_ema:
            return False
        closes = [c.close for c in history[-self.slow_ema:]]
        self._fast_ema_val = sum(closes[-self.fast_ema:]) / self.fast_ema
        self._slow_ema_val = sum(closes) / self.slow_ema
        macd_val = self._fast_ema_val - self._slow_ema_val
        self._signal_ema_val = macd_val
        self._prev_histogram = 0.0
        self._macd_initialized = True
        return True

    def _update_macd(self, price: float) -> float:
        """Step MACD EMAs forward one bar. Returns histogram."""
        fm = 2 / (self.fast_ema + 1)
        sm = 2 / (self.slow_ema + 1)
        self._fast_ema_val = price * fm + self._fast_ema_val * (1 - fm)
        self._slow_ema_val = price * sm + self._slow_ema_val * (1 - sm)

        macd_val = self._fast_ema_val - self._slow_ema_val
        sig_m = 2 / (self.signal_period + 1)
        self._signal_ema_val = macd_val * sig_m + self._signal_ema_val * (1 - sig_m)

        histogram = macd_val - self._signal_ema_val
        return histogram

    def _init_ema200(self, history: List[Candle]) -> bool:
        if len(history) < 200:
            return False
        self._ema200_val = sum(c.close for c in history[-200:]) / 200
        self._ema200_initialized = True
        return True

    def _update_ema200(self, price: float) -> None:
        m = 2 / 201
        self._ema200_val = price * m + self._ema200_val * (1 - m)

    # ── Signal generators ────────────────────────────────

    def _macd_signal(self, histogram: float) -> float:
        prev = self._prev_histogram
        if histogram > 0 and prev <= 0:
            return min(1.0, abs(histogram) / (self._fast_ema_val * 0.01 + 1e-9))
        elif histogram < 0 and prev >= 0:
            return max(-1.0, -abs(histogram) / (self._fast_ema_val * 0.01 + 1e-9))
        elif histogram > 0:
            return 0.3
        elif histogram < 0:
            return -0.3
        return 0.0

    def _bollinger_signal(self, price: float, history: List[Candle]) -> float:
        if len(history) < self.bb_period:
            return 0.0
        c = np.array([h.close for h in history[-self.bb_period:]])
        middle = float(np.mean(c))
        std = float(np.std(c, ddof=1))
        if std == 0:
            return 0.0

        upper = middle + self.bb_std_dev * std
        lower = middle - self.bb_std_dev * std
        band_width = upper - lower
        if band_width < 1e-9:
            return 0.0

        pct_b = (price - lower) / band_width

        if pct_b <= 0.05:
            return 1.0
        elif pct_b >= 0.95:
            return -1.0
        elif pct_b < 0.35:
            return 0.3 * (0.35 - pct_b) / 0.35
        elif pct_b > 0.65:
            return -0.3 * (pct_b - 0.65) / 0.35
        return 0.0

    # ── Regime classification ────────────────────────────

    def _compute_atr(self, history: List[Candle]) -> float:
        if len(history) < 15:
            return 0.0
        tr_vals = []
        for i in range(-14, 0):
            c = history[i]
            pc = history[i - 1].close
            tr = max(c.high - c.low, abs(c.high - pc), abs(c.low - pc))
            tr_vals.append(tr)
        return float(np.mean(tr_vals))

    def _update_regime(self, price: float, history: List[Candle]) -> None:
        atr_val = self._compute_atr(history)
        self._atr_history.append(atr_val)

        if len(self._atr_history) < 50:
            self._current_regime = 0
            return

        atr_vals = list(self._atr_history)
        atr_mean = sum(atr_vals) / len(atr_vals)
        atr_std = (sum((v - atr_mean) ** 2 for v in atr_vals) / len(atr_vals)) ** 0.5
        atr_zscore = (atr_val - atr_mean) / (atr_std + 1e-9)

        above_ema200 = price > self._ema200_val

        c = np.array([h.close for h in history[-self.bb_period:]])
        middle = float(np.mean(c))
        std = float(np.std(c, ddof=1))
        upper = middle + self.bb_std_dev * std
        lower = middle - self.bb_std_dev * std
        bb_width_pct = (upper - lower) / (middle + 1e-9)

        if atr_zscore > 1.5:
            self._current_regime = 3  # HIGH_VOL_RANGE
        elif bb_width_pct < 0.04:
            self._current_regime = 4  # LOW_VOL_RANGE
        elif above_ema200 and bb_width_pct > 0.06:
            self._current_regime = 1  # TREND_UP
        elif not above_ema200 and bb_width_pct > 0.06:
            self._current_regime = 2  # TREND_DOWN
        else:
            self._current_regime = 4  # LOW_VOL_RANGE

    # ── Filters (Section B) ──────────────────────────────

    def _regime_matches(self, mask: int) -> bool:
        r = self._current_regime
        if mask == 0:
            return True
        if mask == 1:
            return r in (1, 2)
        if mask == 2:
            return r in (3, 4)
        if mask == 3:
            return r == 3
        return True

    def _apply_filters(self, signal: float, candle: Candle) -> float:
        # Volume filter
        if self.vol_filter_on == 1 and self._regime_matches(self.vol_filter_regime):
            if len(self._vol_sma_buf) >= 20:
                avg_vol = sum(self._vol_sma_buf) / len(self._vol_sma_buf)
                if avg_vol > 0 and candle.volume < avg_vol * 0.7:
                    signal = 0.0

        # ATR filter
        if self.atr_filter_on == 1 and self._regime_matches(self.atr_filter_regime):
            if len(self._atr_history) >= 50:
                atr_vals = list(self._atr_history)
                atr_mean = sum(atr_vals) / len(atr_vals)
                atr_std = (sum((v - atr_mean) ** 2 for v in atr_vals) / len(atr_vals)) ** 0.5
                atr_z = (atr_vals[-1] - atr_mean) / (atr_std + 1e-9)
                if atr_z > 2.5:
                    signal *= 0.5

        # Trend-direction filter
        if self.trend_filter_on == 1 and self._regime_matches(self.trend_filter_regime):
            if self._current_regime == 1 and signal < 0:
                signal = 0.0
            elif self._current_regime == 2 and signal > 0:
                signal = 0.0

        return signal

    # ── Main logic ───────────────────────────────────────

    def on_candle(
        self,
        candle: Candle,
        history: List[Candle],
        ctx: Optional["ExecutionContext"] = None,
    ) -> Signal:
        if ctx is None:
            return Signal.HOLD

        warmup_needed = max(self.slow_ema, self.bb_period, 200) + 20
        if len(history) < warmup_needed:
            return Signal.HOLD

        price = candle.close

        # Initialize indicators on first valid bar
        if not self._macd_initialized:
            if not self._init_macd(history):
                return Signal.HOLD
        if not self._ema200_initialized:
            if not self._init_ema200(history):
                return Signal.HOLD

        # Update indicators
        histogram = self._update_macd(price)
        self._update_ema200(price)
        self._vol_sma_buf.append(candle.volume)

        # Update regime
        self._update_regime(price, history)

        # Compute raw signals
        macd_sig = self._macd_signal(histogram)
        bb_sig = self._bollinger_signal(price, history)

        # Apply filters
        macd_sig = self._apply_filters(macd_sig, candle)
        bb_sig = self._apply_filters(bb_sig, candle)

        # Blend
        raw_position = (
            self.signal_weight * macd_sig
            + (1.0 - self.signal_weight) * bb_sig
        )
        target_position = max(-1.0, min(1.0, raw_position * self.max_pos))

        # Execute with minimum hold period
        MIN_HOLD_BARS = 4
        pos = ctx.position(candle.market)

        if pos is not None and self._bars_in_trade < MIN_HOLD_BARS:
            self._bars_in_trade += 1
            self._prev_histogram = histogram
            return Signal.HOLD

        # Compute current fraction
        equity = ctx.account.equity
        if equity <= 0:
            self._prev_histogram = histogram
            return Signal.HOLD

        current_value = 0.0
        if pos is not None:
            current_value = pos.size * price
            if pos.side.name == "SHORT":
                current_value = -current_value
        current_fraction = current_value / equity

        # Rebalance threshold — 8% to cut fee drag
        if abs(target_position - current_fraction) > 0.08:
            if pos is not None:
                ctx.close_position(candle.market)

            if abs(target_position) > 0.05:
                size = abs(target_position) * equity / price
                side = Side.LONG if target_position > 0 else Side.SHORT
                ctx.market_order(candle.market, side, size)
            self._bars_in_trade = 0
        else:
            if pos is not None:
                self._bars_in_trade += 1
            else:
                self._bars_in_trade = 0

        self._prev_histogram = histogram
        return Signal.HOLD
