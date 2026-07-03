"""Bar-time alignment and the no-look-ahead 'as of t' rule (§5, §8.2).

Timestamps are unix-**milliseconds** UTC ``int``. Bars are epoch-aligned: a bar
of width ``resolution_s`` starts at an integer multiple of ``resolution_s``
seconds from the unix epoch. The universal visibility rule (§8.2) is:

    strictly less than bar start, closed data only, None over stale, never synthetic.

These are the primitives every accessor's contract is built on.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable, Sequence
from typing import TypeVar

MS_PER_SECOND = 1000

T = TypeVar("T")


def bar_start(ts_ms: int, resolution_s: int) -> int:
    """Floor ``ts_ms`` to the START of its epoch-aligned bar (unix ms)."""
    if resolution_s <= 0:
        raise ValueError(f"resolution_s must be positive, got {resolution_s}")
    width_ms = resolution_s * MS_PER_SECOND
    return ts_ms - (ts_ms % width_ms)


def bar_end(bar_start_ms: int, resolution_s: int) -> int:
    """The exclusive END of the bar that starts at ``bar_start_ms`` (unix ms)."""
    if resolution_s <= 0:
        raise ValueError(f"resolution_s must be positive, got {resolution_s}")
    return bar_start_ms + resolution_s * MS_PER_SECOND


def is_bar_closed(bar_start_ms: int, resolution_s: int, as_of_ms: int) -> bool:
    """True iff the bar is fully closed as of ``as_of_ms``: bar_end <= as_of (§8.2).

    Strategies see only closed bars — a bar still in progress is invisible.
    """
    return bar_end(bar_start_ms, resolution_s) <= as_of_ms


def last_before(
    items: Sequence[T],
    t_ms: int,
    key: Callable[[T], int] | None = None,
) -> T | None:
    """The last item with timestamp STRICTLY < ``t_ms``, or None (§8.2).

    This is the concrete meaning of 'as of t'. ``items`` must be sorted ascending
    by timestamp. ``key`` extracts each item's ts (default: the item is the ts).
    Strict ``<`` is the whole point — a value published exactly at the bar start
    is not yet knowable at the bar start.
    """
    if not items:
        return None
    idx = bisect_left(items, t_ms) if key is None else bisect_left(items, t_ms, key=key)
    return items[idx - 1] if idx > 0 else None
