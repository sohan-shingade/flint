"""The paper/live clock — driven by venue event time, never the wall clock (§6.7).

Clock skew between the local host and the venue would otherwise masquerade as
strategy drift, so the paper session's sense of "now" is the **latest venue-
reported event timestamp** (from the trade / book frames that carry one), and a
bar "closes" only when a venue event crosses its boundary — not when the host's
wall clock ticks past it.

Two clocks, both venue-derived, are in play and must not be conflated:

* ``PaperClock.now`` — the **session** clock: the latest venue event ts. It
  decides *when* a bar has closed, gates T+1 execution eligibility (an order
  becomes eligible at ``close_event_ts + latency``, the same latency model the
  backtest uses), and feeds the heartbeat (silence > 2× bar interval fires).
* The engine's per-bar ``ctx.now`` — the **decision** clock: bar *start*. When
  the engine processes a closed bar it exposes ``ctx.now = candle.ts`` (bar
  start) under the §8.2 closed-data-only contract. That is strictly ≤ the
  session clock, and is the engine's business, not this module's.

This class owns only the session clock. It advances forward-only: a late or
out-of-order frame never rewinds it (a rewind would reopen a closed bar).
"""

from __future__ import annotations

from flint.core.time import bar_start


class PaperClock:
    """Tracks the latest venue event timestamp and the bar it falls in (§6.7)."""

    def __init__(self, resolution_s: int) -> None:
        if resolution_s <= 0:
            raise ValueError(f"resolution_s must be positive, got {resolution_s}")
        self._resolution_s = resolution_s
        self._now: int | None = None

    @property
    def resolution_s(self) -> int:
        return self._resolution_s

    @property
    def now(self) -> int | None:
        """Latest venue event ts observed, or ``None`` before the first event."""
        return self._now

    def observe(self, event_ts: int) -> None:
        """Advance the session clock to a venue event ts (forward-only).

        A frame whose ts is behind the current clock is a reordered/duplicate
        observation — it is folded into whichever bar it belongs to by the
        aggregator, but it never moves the clock backward and so never reopens a
        bar this clock has already declared closed.
        """
        if self._now is None or event_ts > self._now:
            self._now = event_ts

    @property
    def current_bar_start(self) -> int | None:
        """Start ts of the bar the session clock currently sits in, or ``None``."""
        if self._now is None:
            return None
        return bar_start(self._now, self._resolution_s)
