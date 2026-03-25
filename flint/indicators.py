"""Technical indicators for strategy development.

All functions take a list of Candle objects and return numeric values.
Designed to eliminate the repetitive numpy boilerplate in every strategy.

Usage:
    from flint.indicators import sma, ema, rsi, macd, bollinger, atr, vwap

    def on_candle(self, candle, history, ctx=None):
        if rsi(history, 14) < 30:
            return Signal.BUY
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from .models import Candle


def closes(history: List[Candle], period: Optional[int] = None) -> np.ndarray:
    """Extract close prices as numpy array."""
    data = history[-period:] if period else history
    return np.array([c.close for c in data])


def highs(history: List[Candle], period: Optional[int] = None) -> np.ndarray:
    """Extract high prices as numpy array."""
    data = history[-period:] if period else history
    return np.array([c.high for c in data])


def lows(history: List[Candle], period: Optional[int] = None) -> np.ndarray:
    """Extract low prices as numpy array."""
    data = history[-period:] if period else history
    return np.array([c.low for c in data])


def volumes(history: List[Candle], period: Optional[int] = None) -> np.ndarray:
    """Extract volumes as numpy array."""
    data = history[-period:] if period else history
    return np.array([c.volume for c in data])


# ─── Moving Averages ──────────────────────────────────

def sma(history: List[Candle], period: int) -> float:
    """Simple Moving Average of close prices."""
    if len(history) < period:
        return 0.0
    return float(np.mean(closes(history, period)))


def ema(history: List[Candle], period: int) -> float:
    """Exponential Moving Average of close prices.

    Computes full EMA from the available history (seeds with SMA).
    """
    if len(history) < period:
        return 0.0
    c = closes(history)
    multiplier = 2 / (period + 1)
    ema_val = float(np.mean(c[:period]))  # seed with SMA
    for price in c[period:]:
        ema_val = float(price) * multiplier + ema_val * (1 - multiplier)
    return ema_val


def wma(history: List[Candle], period: int) -> float:
    """Weighted Moving Average — recent prices weighted more."""
    if len(history) < period:
        return 0.0
    c = closes(history, period)
    weights = np.arange(1, period + 1, dtype=float)
    return float(np.sum(c * weights) / np.sum(weights))


# ─── Oscillators ──────────────────────────────────────

def rsi(history: List[Candle], period: int = 14) -> float:
    """Relative Strength Index (0-100)."""
    if len(history) < period + 1:
        return 50.0
    c = closes(history, period + 1)
    deltas = np.diff(c)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = float(np.mean(gains))
    avg_loss = float(np.mean(losses))
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def stochastic(history: List[Candle], period: int = 14) -> Tuple[float, float]:
    """Stochastic oscillator. Returns (%K, %D).

    %K = (close - lowest low) / (highest high - lowest low) * 100
    %D = 3-period SMA of %K
    """
    if len(history) < period:
        return 50.0, 50.0

    # Compute %K for the last 3 bars, each with its own rolling window
    k_vals = []
    for offset in range(min(3, len(history) - period + 1)):
        end = len(history) - offset
        start = end - period
        sub_h = [history[j].high for j in range(start, end)]
        sub_l = [history[j].low for j in range(start, end)]
        hi = max(sub_h)
        lo = min(sub_l)
        rng = hi - lo
        if rng == 0:
            k_vals.append(50.0)
        else:
            k_vals.append((history[end - 1].close - lo) / rng * 100)

    k = k_vals[0] if k_vals else 50.0
    d = float(np.mean(k_vals)) if k_vals else k
    return k, d


# ─── MACD ─────────────────────────────────────────────

def macd(history: List[Candle], fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[float, float, float]:
    """MACD indicator. Returns (macd_line, signal_line, histogram)."""
    if len(history) < slow:
        return 0.0, 0.0, 0.0
    fast_ema = ema(history, fast)
    slow_ema = ema(history, slow)
    macd_line = fast_ema - slow_ema

    # Approximate signal line (would need full EMA history for exact)
    # Use SMA of recent MACD-like values as approximation
    if len(history) >= slow + signal:
        macd_values = []
        for i in range(signal):
            idx = len(history) - signal + i + 1
            f = ema(history[:idx], fast)
            s = ema(history[:idx], slow)
            macd_values.append(f - s)
        signal_line = float(np.mean(macd_values))
    else:
        signal_line = macd_line

    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ─── Bands & Channels ────────────────────────────────

def bollinger(history: List[Candle], period: int = 20, num_std: float = 2.0) -> Tuple[float, float, float]:
    """Bollinger Bands. Returns (upper, middle, lower)."""
    if len(history) < period:
        price = history[-1].close if history else 0
        return price, price, price
    c = closes(history, period)
    middle = float(np.mean(c))
    std = float(np.std(c, ddof=1))
    upper = middle + num_std * std
    lower = middle - num_std * std
    return upper, middle, lower


def bollinger_width(history: List[Candle], period: int = 20, num_std: float = 2.0) -> float:
    """Bollinger Band width as percentage of middle band."""
    upper, middle, lower = bollinger(history, period, num_std)
    if middle == 0:
        return 0.0
    return (upper - lower) / middle


# ─── Volatility ──────────────────────────────────────

def atr(history: List[Candle], period: int = 14) -> float:
    """Average True Range — measures volatility."""
    if len(history) < period + 1:
        return 0.0
    tr_values = []
    for i in range(-period, 0):
        c = history[i]
        prev_close = history[i - 1].close
        tr = max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))
        tr_values.append(tr)
    return float(np.mean(tr_values))


def volatility(history: List[Candle], period: int = 20) -> float:
    """Annualized volatility of returns."""
    if len(history) < period + 1:
        return 0.0
    c = closes(history, period + 1)
    returns = np.diff(c) / c[:-1]
    return float(np.std(returns, ddof=1) * np.sqrt(8760))  # annualized for hourly


# ─── Volume ──────────────────────────────────────────

def vwap(history: List[Candle], period: int = 20) -> float:
    """Volume-Weighted Average Price."""
    if len(history) < period:
        return history[-1].close if history else 0.0
    data = history[-period:]
    total_vol = sum(c.volume for c in data)
    if total_vol == 0:
        return sma(history, period)
    return sum(((c.high + c.low + c.close) / 3) * c.volume for c in data) / total_vol


def volume_ratio(history: List[Candle], period: int = 20) -> float:
    """Current volume / average volume ratio. > 1.0 = above average."""
    if len(history) < period:
        return 1.0
    avg_vol = float(np.mean(volumes(history, period)))
    if avg_vol == 0:
        return 1.0
    return history[-1].volume / avg_vol


# ─── Trend ───────────────────────────────────────────

def roc(history: List[Candle], period: int = 12) -> float:
    """Rate of Change (percentage)."""
    if len(history) <= period:
        return 0.0
    old = history[-period - 1].close
    if old == 0:
        return 0.0
    return ((history[-1].close - old) / old) * 100


def adx(history: List[Candle], period: int = 14) -> float:
    """Average Directional Index — trend strength (0-100).

    Simplified implementation. > 25 = trending, < 20 = ranging.
    """
    if len(history) < period + 1:
        return 0.0
    plus_dm = []
    minus_dm = []
    tr_vals = []
    for i in range(-period, 0):
        c = history[i]
        p = history[i - 1]
        up = c.high - p.high
        down = p.low - c.low
        plus_dm.append(max(up, 0) if up > down else 0)
        minus_dm.append(max(down, 0) if down > up else 0)
        tr = max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        tr_vals.append(tr)

    atr_val = np.mean(tr_vals)
    if atr_val == 0:
        return 0.0
    plus_di = 100 * np.mean(plus_dm) / atr_val
    minus_di = 100 * np.mean(minus_dm) / atr_val
    di_sum = plus_di + minus_di
    if di_sum == 0:
        return 0.0
    dx = 100 * abs(plus_di - minus_di) / di_sum
    return float(dx)


# ─── Convenience ─────────────────────────────────────

def z_score(history: List[Candle], period: int = 20) -> float:
    """Z-score of current price vs rolling mean."""
    if len(history) < period:
        return 0.0
    c = closes(history, period)
    mean = float(np.mean(c))
    std = float(np.std(c, ddof=1))
    if std == 0:
        return 0.0
    return (history[-1].close - mean) / std


def highest_high(history: List[Candle], period: int) -> float:
    """Highest high in the last N candles."""
    if len(history) < period:
        return max(c.high for c in history) if history else 0
    return float(max(c.high for c in history[-period:]))


def lowest_low(history: List[Candle], period: int) -> float:
    """Lowest low in the last N candles."""
    if len(history) < period:
        return min(c.low for c in history) if history else 0
    return float(min(c.low for c in history[-period:]))
