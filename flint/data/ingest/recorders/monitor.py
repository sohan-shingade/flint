"""Recorder runtime monitors: WS sequence-gap detection + lag metric (§9.0).

Two observability primitives every WS recorder feeds, kept separate from the
table-oriented pre-write bars in ``ingest/quality.py``:

* ``SequenceTracker`` — WS **sequence-number monotonicity**. A stream carries a
  monotonic key (HL ``trades`` → the strictly-increasing global ``tid``; HL
  ``l2Book`` / ``activeAssetCtx`` → the non-decreasing event/receipt ``time``).
  Observing each key in order classifies it as ok / duplicate / regression /
  gap, so a dropped or replayed frame is *detected*, never silently persisted.
* ``LagMonitor`` — the exported **lag metric**: age of the newest record per
  ``(venue, market, kind)`` against an injected clock, with a staleness query
  for alerting. No wall-clock is read directly (the clock is injected), so tests
  are deterministic.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from ...ranges import Kind


class SeqMode(StrEnum):
    """How the next sequence value must relate to the last one."""

    CONTIGUOUS = "contiguous"  # exactly last + 1 (a true sequence number)
    STRICTLY_INCREASING = "strictly_increasing"  # > last (HL trade tids)
    NON_DECREASING = "non_decreasing"  # >= last (event timestamps may repeat)


@dataclass(frozen=True)
class SeqResult:
    """The classification of one observed sequence value."""

    status: str  # "ok" | "duplicate" | "regression" | "gap"
    missed: int = 0  # for a CONTIGUOUS gap: how many values were skipped


class SequenceTracker:
    """Classify a per-stream sequence of monotonic keys; count anomalies.

    ``observe`` is called once per frame with a stream key (e.g. ``(market,
    kind)``) and the frame's sequence value. The first value on a stream is
    always ``ok`` (nothing to compare against).
    """

    def __init__(self, mode: SeqMode = SeqMode.STRICTLY_INCREASING) -> None:
        self._mode = mode
        self._last: dict[object, int] = {}
        self.gaps = 0
        self.missed_total = 0
        self.regressions = 0
        self.duplicates = 0

    def observe(self, stream: object, seq: int) -> SeqResult:
        last = self._last.get(stream)
        if last is None:
            self._last[stream] = seq
            return SeqResult("ok")

        result = self._classify(last, seq)
        # A regression never advances the stream's high-water mark (a replayed or
        # out-of-order frame must not hide a later genuine gap); everything else
        # advances it.
        if result.status != "regression":
            self._last[stream] = seq
        self._tally(result)
        return result

    def _classify(self, last: int, seq: int) -> SeqResult:
        if seq == last:
            return SeqResult("duplicate")
        if seq < last:
            return SeqResult("regression")
        if self._mode is SeqMode.CONTIGUOUS and seq > last + 1:
            return SeqResult("gap", missed=seq - last - 1)
        return SeqResult("ok")

    def _tally(self, result: SeqResult) -> None:
        if result.status == "duplicate":
            self.duplicates += 1
        elif result.status == "regression":
            self.regressions += 1
        elif result.status == "gap":
            self.gaps += 1
            self.missed_total += result.missed


class LagMonitor:
    """Track the age of the newest record per ``(venue, market, kind)`` (§9.0)."""

    def __init__(self, clock: Callable[[], int]) -> None:
        self._clock = clock
        self._newest: dict[tuple[str, str, Kind], int] = {}

    def record(self, venue: str, market: str, kind: Kind, event_ts: int) -> None:
        """Note that a record with ``event_ts`` was captured for this stream."""
        key = (venue, market, kind)
        prev = self._newest.get(key)
        if prev is None or event_ts > prev:
            self._newest[key] = event_ts

    def lag_ms(self, venue: str, market: str, kind: Kind) -> int | None:
        """Now minus the newest event ts for this stream, or None if unseen."""
        newest = self._newest.get((venue, market, kind))
        return None if newest is None else self._clock() - newest

    def snapshot(self) -> dict[tuple[str, str, Kind], int]:
        """Current lag for every tracked stream — the exported metric."""
        now = self._clock()
        return {key: now - ts for key, ts in self._newest.items()}

    def stale(self, threshold_ms: int) -> list[tuple[str, str, Kind]]:
        """Streams whose newest record is older than ``threshold_ms`` (alerting)."""
        return [key for key, lag in self.snapshot().items() if lag > threshold_ms]
