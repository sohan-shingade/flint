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
