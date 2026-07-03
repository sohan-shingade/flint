"""Reconnect gap replay — the lake as a cold backfill, never the hot path (§6.7).

When the WebSocket drops, bars close on the venue between the last frame the
LiveFeed emitted and the first frame after reconnect. Those bars are **missed
state**, and the contract (§6.7) is precise about both failure directions:

* they are **never skipped** — a silent hole would desync positions / funding
  from reality; and
* they are **never forward-filled** — a fabricated flat bar is look-ahead-free
  but is still invented data (D26).

So on reconnect the LiveFeed fetches the missed candles (+ funding, marks, OI)
from the lake and **replays them through the same engine loop** as ``gap-replay``
bars. Whatever the lake genuinely holds is replayed; whatever it lacks is
reported as an unrecovered remainder with ``degraded_fidelity`` set — surfaced,
not papered over.

``GapSource`` is the port the LiveFeed depends on; the runner (slice 5.6) wires
the lake-backed adapter, and tests inject :class:`InMemoryGapSource` over
hand-authored recorded fragments. Keeping it a port is the ports-and-adapters
rule: the live feed reaches storage only through an interface, never directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from flint.core.models import Candle, FundingRate, MarkSnapshot, OrderbookSnapshot
from flint.engine.context import OpenInterestSnapshot


@dataclass(frozen=True)
class GapData:
    """What the lake could return for a reconnect window, keyed by nothing but ts.

    All lists are for the single ``(venue, market)`` the LiveFeed serves and are
    ascending by ts. Any list may be empty — an empty ``candles`` is exactly the
    "lake lacked the gap data" case the caller flags as degraded.
    """

    candles: tuple[Candle, ...] = ()
    funding: tuple[FundingRate, ...] = ()
    marks: tuple[MarkSnapshot, ...] = ()
    oi: tuple[OpenInterestSnapshot, ...] = ()
    books: tuple[OrderbookSnapshot, ...] = ()


class GapSource(Protocol):
    """Reads closed bars + funding for a reconnect window from the lake (§6.7)."""

    def fetch(
        self, venue: str, market: str, start_ms: int, end_ms: int, resolution_s: int
    ) -> GapData:
        """Return whatever the lake holds for bars with start in ``[start, end)``."""
        ...


@dataclass(frozen=True)
class GapRecovery:
    """The record of one reconnect: what was missed and how much came back (§6.7).

    ``bars_expected`` is how many bar starts fall in the gap window;
    ``bars_recovered`` is how many the lake actually returned. When they differ
    the session is ``degraded_fidelity`` — the monitor still shows "recovered
    from N-minute gap", but flagged, and no bar is invented to close the hole.
    """

    start_ms: int
    end_ms: int
    resolution_s: int
    bars_expected: int
    bars_recovered: int

    @property
    def gap_minutes(self) -> float:
        return (self.end_ms - self.start_ms) / 60_000

    @property
    def degraded_fidelity(self) -> bool:
        return self.bars_recovered < self.bars_expected

    @property
    def message(self) -> str:
        base = f"recovered from {self.gap_minutes:.0f}-minute gap"
        if self.degraded_fidelity:
            missing = self.bars_expected - self.bars_recovered
            return f"{base} (degraded: {missing} of {self.bars_expected} bars unavailable)"
        return base


@dataclass
class InMemoryGapSource:
    """A ``GapSource`` over pre-loaded fragments — for tests and the runner's seam.

    Holds ascending records per ``(venue, market)``; ``fetch`` returns the slice
    whose bar-start falls in the window. Records are hand-authored real-shape
    fragments (D26), never generated.
    """

    candles: dict[tuple[str, str], list[Candle]] = field(default_factory=dict)
    funding: dict[tuple[str, str], list[FundingRate]] = field(default_factory=dict)
    marks: dict[tuple[str, str], list[MarkSnapshot]] = field(default_factory=dict)
    oi: dict[tuple[str, str], list[OpenInterestSnapshot]] = field(default_factory=dict)
    books: dict[tuple[str, str], list[OrderbookSnapshot]] = field(default_factory=dict)

    def fetch(
        self, venue: str, market: str, start_ms: int, end_ms: int, resolution_s: int
    ) -> GapData:
        key = (venue, market)
        return GapData(
            candles=tuple(
                c for c in self.candles.get(key, []) if start_ms <= c.ts < end_ms
            ),
            funding=tuple(
                f for f in self.funding.get(key, []) if start_ms <= f.ts < end_ms
            ),
            marks=tuple(
                m for m in self.marks.get(key, []) if start_ms <= m.ts < end_ms
            ),
            oi=tuple(o for o in self.oi.get(key, []) if start_ms <= o.ts < end_ms),
            books=tuple(
                b for b in self.books.get(key, []) if start_ms <= b.ts < end_ms
            ),
        )
