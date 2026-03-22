"""Pairwise and rolling correlation analysis for market candle series.

Computes Pearson correlation matrices and rolling correlation windows
using log returns derived from candle close prices.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

from ..models import Candle


def _returns(candles: List[Candle]) -> List[float]:
    """Compute log returns from a list of candles (using close prices).

    Returns a list of length ``len(candles) - 1``.  Each element is
    ``ln(close[i+1] / close[i])``.
    """
    rets: List[float] = []
    for i in range(1, len(candles)):
        prev = candles[i - 1].close
        cur = candles[i].close
        if prev > 0 and cur > 0:
            rets.append(math.log(cur / prev))
        else:
            rets.append(0.0)
    return rets


def _pearson(x: List[float], y: List[float]) -> float:
    """Pearson correlation coefficient between two equal-length float lists.

    Returns 0.0 when the inputs have zero variance or differ in length.
    """
    n = min(len(x), len(y))
    if n < 2:
        return 0.0

    mean_x = sum(x[:n]) / n
    mean_y = sum(y[:n]) / n

    cov = 0.0
    var_x = 0.0
    var_y = 0.0
    for i in range(n):
        dx = x[i] - mean_x
        dy = y[i] - mean_y
        cov += dx * dy
        var_x += dx * dx
        var_y += dy * dy

    denom = math.sqrt(var_x * var_y)
    if denom == 0.0:
        return 0.0
    return cov / denom


def compute_correlation_matrix(
    candles_by_market: Dict[str, List[Candle]],
) -> Dict[str, Dict[str, float]]:
    """Compute a pairwise Pearson correlation matrix over log returns.

    Args:
        candles_by_market: ``{market_symbol: [Candle, ...]}`` — candles should
            be sorted by timestamp and share the same resolution.

    Returns:
        Nested dict ``{market_a: {market_b: correlation, ...}, ...}``.
        Diagonal entries are 1.0.
    """
    markets = sorted(candles_by_market.keys())
    returns_by_market: Dict[str, List[float]] = {
        m: _returns(candles_by_market[m]) for m in markets
    }

    matrix: Dict[str, Dict[str, float]] = {}
    for a in markets:
        matrix[a] = {}
        for b in markets:
            if a == b:
                matrix[a][b] = 1.0
            elif b in matrix and a in matrix[b]:
                # Symmetric — reuse previously computed value
                matrix[a][b] = matrix[b][a]
            else:
                matrix[a][b] = _pearson(returns_by_market[a], returns_by_market[b])

    return matrix


def compute_rolling_correlation(
    candles_a: List[Candle],
    candles_b: List[Candle],
    window: int = 20,
) -> List[Tuple[int, float]]:
    """Compute rolling Pearson correlation between two candle series.

    Both series must be sorted by timestamp and aligned (same timestamps).
    The function uses close-to-close log returns and a sliding window of
    ``window`` return observations.

    Args:
        candles_a: First candle series (sorted by ts).
        candles_b: Second candle series (sorted by ts).
        window: Number of return observations per window (default 20).

    Returns:
        List of ``(timestamp, correlation)`` tuples.  The timestamp is taken
        from the *end* of each window in ``candles_a``.
    """
    rets_a = _returns(candles_a)
    rets_b = _returns(candles_b)

    n = min(len(rets_a), len(rets_b))
    if n < window:
        return []

    result: List[Tuple[int, float]] = []
    for i in range(window, n + 1):
        chunk_a = rets_a[i - window : i]
        chunk_b = rets_b[i - window : i]
        corr = _pearson(chunk_a, chunk_b)
        # Timestamp from the candle at the end of this window
        # rets index i-1 corresponds to candles index i (since returns are offset by 1)
        ts = candles_a[i].ts
        result.append((ts, corr))

    return result
