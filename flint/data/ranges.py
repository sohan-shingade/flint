"""Half-open time ranges + kinds — the algebra the DataManager resolves over (§9).

Every data request is a ``(venue, market, kind, range)``. A ``TimeRange`` is a
half-open ``[start_ms, end_ms)`` window (matching ``core.time`` and the storage
ports: a bar timestamped exactly ``end_ms`` is *outside* the range). A
``RangeSet`` is a normalised union of such windows — the shape coverage takes
when a source has data in fragments, and the type the source chain does its
partial-range merge and funding-gate arithmetic on.

No timestamps are ever invented here (D26): the algebra only intersects,
subtracts, and unions ranges the caller supplies.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class Kind(StrEnum):
    """A kind of market data the DataManager resolves independently (§9)."""

    CANDLES = "candles"
    FUNDING = "funding"
    OI = "oi"
    DEPTH = "depth"

    @property
    def is_hard_required(self) -> bool:
        """True if a gap in this kind *rejects* the run rather than degrading it.

        Funding is a perp's PnL, not an optional input — missing funding rejects
        the covered period (§2.11 hard gate). Borrow (Jupiter) gates the same way
        but is deferred with its venue (D28), so funding is the only v1 case.
        """
        return self is Kind.FUNDING

    @property
    def is_degradable(self) -> bool:
        """True if a gap drops fidelity (flagged) instead of rejecting (§6.3).

        Recorded depth is the degradable input: without it, fills fall back to a
        spread/impact model at a lower, clearly-labelled tier.
        """
        return self is Kind.DEPTH


@dataclass(frozen=True, slots=True)
class TimeRange:
    """A half-open ``[start_ms, end_ms)`` window in unix milliseconds."""

    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if self.end_ms < self.start_ms:
            raise ValueError(f"end {self.end_ms} precedes start {self.start_ms}")

    @property
    def is_empty(self) -> bool:
        return self.start_ms >= self.end_ms

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    def overlaps(self, other: TimeRange) -> bool:
        return self.start_ms < other.end_ms and other.start_ms < self.end_ms

    def intersect(self, other: TimeRange) -> TimeRange | None:
        start = max(self.start_ms, other.start_ms)
        end = min(self.end_ms, other.end_ms)
        return TimeRange(start, end) if start < end else None


class RangeSet:
    """A normalised union of half-open ranges — sorted, non-overlapping, merged.

    Construction always normalises: overlapping *and* adjacent ranges collapse
    (``[a, b) ∪ [b, c) == [a, c)`` because the boundary is half-open), and empty
    ranges are dropped. Instances are immutable; every operation returns a new
    ``RangeSet``.
    """

    __slots__ = ("_ranges",)

    def __init__(self, ranges: Iterable[TimeRange] = ()) -> None:
        self._ranges: tuple[TimeRange, ...] = self._normalise(ranges)

    @staticmethod
    def _normalise(ranges: Iterable[TimeRange]) -> tuple[TimeRange, ...]:
        ordered = sorted(
            (r for r in ranges if not r.is_empty), key=lambda r: r.start_ms
        )
        merged: list[TimeRange] = []
        for r in ordered:
            if merged and r.start_ms <= merged[-1].end_ms:
                # Overlapping or adjacent — extend the last range's end.
                last = merged[-1]
                merged[-1] = TimeRange(last.start_ms, max(last.end_ms, r.end_ms))
            else:
                merged.append(r)
        return tuple(merged)

    @property
    def ranges(self) -> tuple[TimeRange, ...]:
        return self._ranges

    @property
    def is_empty(self) -> bool:
        return not self._ranges

    @property
    def total_ms(self) -> int:
        return sum(r.duration_ms for r in self._ranges)

    def bounds(self) -> TimeRange | None:
        """The single ``[min_start, max_end)`` envelope, or None if empty."""
        if not self._ranges:
            return None
        return TimeRange(self._ranges[0].start_ms, self._ranges[-1].end_ms)

    def union(self, other: RangeSet) -> RangeSet:
        return RangeSet((*self._ranges, *other._ranges))

    def intersect(self, other: RangeSet) -> RangeSet:
        out: list[TimeRange] = []
        for a in self._ranges:
            for b in other._ranges:
                piece = a.intersect(b)
                if piece is not None:
                    out.append(piece)
        return RangeSet(out)

    def subtract(self, other: RangeSet) -> RangeSet:
        """Ranges in ``self`` not covered by ``other`` — the gaps (§2.11 gate)."""
        out: list[TimeRange] = []
        for a in self._ranges:
            cursor = a.start_ms
            # `other` is sorted; walk its pieces that touch `a`, punching holes.
            for b in other._ranges:
                if b.end_ms <= cursor or b.start_ms >= a.end_ms:
                    continue
                if b.start_ms > cursor:
                    out.append(TimeRange(cursor, b.start_ms))
                cursor = max(cursor, b.end_ms)
                if cursor >= a.end_ms:
                    break
            if cursor < a.end_ms:
                out.append(TimeRange(cursor, a.end_ms))
        return RangeSet(out)

    def covers(self, span: TimeRange) -> bool:
        return RangeSet((span,)).subtract(self).is_empty

    def __eq__(self, other: object) -> bool:
        return isinstance(other, RangeSet) and self._ranges == other._ranges

    def __repr__(self) -> str:
        inner = ", ".join(f"[{r.start_ms},{r.end_ms})" for r in self._ranges)
        return f"RangeSet({inner})"
