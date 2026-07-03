"""Time-base conversion between Flint and Nautilus (§A3, §5).

Two clocks meet here:

* **Units.** Flint timestamps are integer unix **milliseconds** UTC (§5); Nautilus
  timestamps are integer unix **nanoseconds**. ``ms_to_ns`` / ``ns_to_ms`` are the
  only conversions, and ``ns_to_ms`` floors — a nanosecond value that is not a whole
  millisecond loses sub-ms precision, which Flint never carries anyway.

* **Labeling.** A Flint :class:`~flint.core.models.Candle` is stamped at its bar
  **START** (``ts``); a Nautilus ``Bar`` is stamped at its bar **CLOSE**
  (``ts_event``). ``candle_start_ms_to_bar_close_ns`` and its inverse move between
  the two conventions given the bar width, so a recorded candle round-trips into a
  Nautilus bar and back without drifting a bar.

This module deliberately imports **nothing** from ``nautilus_trader`` — it is pure
integer arithmetic, so it is import-cheap and its tests run in an environment
without the ``nautilus`` extra.
"""

from __future__ import annotations

NS_PER_MS = 1_000_000
MS_PER_SECOND = 1_000


def ms_to_ns(ts_ms: int) -> int:
    """Convert integer unix-ms to integer unix-ns (exact)."""
    return int(ts_ms) * NS_PER_MS


def ns_to_ms(ts_ns: int) -> int:
    """Convert integer unix-ns to integer unix-ms, flooring sub-ms remainder."""
    return int(ts_ns) // NS_PER_MS


def candle_start_ms_to_bar_close_ns(start_ms: int, resolution_s: int) -> int:
    """Nautilus ``Bar.ts_event`` (bar CLOSE, ns) for a Flint candle START (ms).

    A Flint candle labelled at ``start_ms`` covers ``[start_ms, start_ms + width)``;
    Nautilus stamps the same bar at its close, so ``ts_event = (start + width)`` in ns.
    """
    if resolution_s <= 0:
        raise ValueError(f"resolution_s must be positive, got {resolution_s}")
    close_ms = int(start_ms) + resolution_s * MS_PER_SECOND
    return ms_to_ns(close_ms)


def bar_close_ns_to_candle_start_ms(close_ns: int, resolution_s: int) -> int:
    """Flint candle START (ms) for a Nautilus ``Bar.ts_event`` (bar CLOSE, ns).

    Exact inverse of :func:`candle_start_ms_to_bar_close_ns`: ``start = close - width``.
    This is also the label correction the aggregator shim applies to Nautilus's
    internal time bars — they are stamped at the bar CLOSE boundary (timer-driven),
    so subtracting the width recovers the epoch-aligned START (§A7). It is
    deliberately *not* a floor of the close ts: a close that lands exactly on the
    next bar's boundary would floor to the wrong (next) bar.
    """
    if resolution_s <= 0:
        raise ValueError(f"resolution_s must be positive, got {resolution_s}")
    return ns_to_ms(close_ns) - resolution_s * MS_PER_SECOND
