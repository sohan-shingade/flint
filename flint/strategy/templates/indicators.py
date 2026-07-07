"""Pure-Python indicators for the built-in templates — no heavy deps (§8.4).

Templates run in-process (trusted, not sandboxed), so these stay dependency-free
and deterministic: plain-``float`` math over the ``history`` list the engine hands
``on_candle`` (closed bars oldest→newest, the current just-closed bar last). Every
window is **bounded and trailing** — an indicator only ever reads the tail it is
given, so there is no look-ahead by construction. ``None`` on insufficient history
(the template treats that as "no opinion yet", never a fabricated value).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flint.core.models import Candle


def closes(history: Sequence["Candle"]) -> list[float]:
    """The close series of ``history`` (oldest→newest)."""
    return [c.close for c in history]


def sma(values: Sequence[float], n: int) -> float | None:
    """Simple moving average of the last ``n`` values, or ``None`` if too few."""
    if n <= 0 or len(values) < n:
        return None
    window = values[-n:]
    return sum(window) / n


def ema(values: Sequence[float], n: int) -> float | None:
    """Exponential moving average (SMA-seeded) of ``values``, or ``None`` if too few.

    Seeded on the first ``n`` values' SMA, then smoothed forward with the standard
    ``2/(n+1)`` weight — a deterministic, causal pass over the given series.
    """
    if n <= 0 or len(values) < n:
        return None
    k = 2.0 / (n + 1)
    seed = sum(values[:n]) / n
    e = seed
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


def rsi(values: Sequence[float], n: int) -> float | None:
    """Wilder's RSI over the last ``n`` deltas, or ``None`` if too few values.

    Uses a simple average of gains/losses over the trailing window (bounded). All
    gains → 100; all losses → 0; the usual ``100 - 100/(1+rs)`` otherwise.
    """
    if n <= 0 or len(values) < n + 1:
        return None
    window = values[-(n + 1):]
    gains = 0.0
    losses = 0.0
    for prev, cur in zip(window, window[1:]):
        delta = cur - prev
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    if losses == 0:
        return 100.0
    if gains == 0:
        return 0.0
    rs = (gains / n) / (losses / n)
    return 100.0 - 100.0 / (1.0 + rs)


def stdev(values: Sequence[float], n: int) -> float | None:
    """Population standard deviation of the last ``n`` values, or ``None`` if too few."""
    if n <= 0 or len(values) < n:
        return None
    window = values[-n:]
    mean = sum(window) / n
    var = sum((v - mean) ** 2 for v in window) / n
    return var ** 0.5


def atr(history: Sequence["Candle"], n: int) -> float | None:
    """Average True Range over the last ``n`` bars, or ``None`` if too few.

    Simple average of the true ranges (needs ``n + 1`` bars for the first
    previous-close), matching this module's bounded-trailing-window convention.
    """
    if n <= 0 or len(history) < n + 1:
        return None
    window = history[-(n + 1):]
    trs = [
        max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
        for prev, cur in zip(window, window[1:])
    ]
    return sum(trs) / n


def vwap(history: Sequence["Candle"], n: int) -> float | None:
    """Volume-weighted average of typical price over the last ``n`` bars.

    ``None`` if too few bars or the window traded no volume (no opinion — never a
    fabricated level).
    """
    if n <= 0 or len(history) < n:
        return None
    window = history[-n:]
    volume = sum(c.volume for c in window)
    if volume <= 0:
        return None
    priced = sum(((c.high + c.low + c.close) / 3.0) * c.volume for c in window)
    return priced / volume


def _ema_series(values: Sequence[float], n: int) -> list[float]:
    """The running EMA at each step from the ``n``-th value on (SMA-seeded)."""
    k = 2.0 / (n + 1)
    e = sum(values[:n]) / n
    series = [e]
    for v in values[n:]:
        e = v * k + e * (1 - k)
        series.append(e)
    return series


def macd(
    values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[float, float] | None:
    """``(macd_line, signal_line)`` at the last value, or ``None`` if too few.

    MACD line is ``ema(fast) - ema(slow)``; the signal line is the ``signal``-EMA
    of the MACD line's own series, so it needs ``slow + signal - 1`` values before
    it has an opinion.
    """
    if fast <= 0 or slow <= fast or signal <= 0:
        return None
    if len(values) < slow + signal - 1:
        return None
    fast_series = _ema_series(values, fast)
    slow_series = _ema_series(values, slow)
    # Align the two series on their common (most recent) tail.
    line = [
        f - s
        for f, s in zip(fast_series[len(fast_series) - len(slow_series):], slow_series)
    ]
    signal_series = _ema_series(line, signal)
    return line[-1], signal_series[-1]


def highest(values: Sequence[float], n: int) -> float | None:
    """Highest of the last ``n`` values, or ``None`` if too few."""
    if n <= 0 or len(values) < n:
        return None
    return max(values[-n:])


def lowest(values: Sequence[float], n: int) -> float | None:
    """Lowest of the last ``n`` values, or ``None`` if too few."""
    if n <= 0 or len(values) < n:
        return None
    return min(values[-n:])
