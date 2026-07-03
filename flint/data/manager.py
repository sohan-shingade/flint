"""DataManager — 'declare a range + universe, the data appears' (§9, §2.11).

The engine is source-agnostic: it asks the DataManager for a universe × kinds ×
range and gets back a time-ordered feed per ``(venue, market, kind)`` plus a
**fidelity summary** (what came at full vs degraded fidelity). Under the hood the
manager walks the source chain per instrument×kind×range, fetching only the
**missing** sub-ranges from each tier and merging them (partial-range merge), and
writes every fetched range through to the local cache.

Two gates decide what happens at a coverage gap (§2.11):

* **Funding is hard.** A perp's PnL is wrong without funding, so a funding gap
  *rejects* the run — in ``strict`` mode with the exact missing ranges and the
  fix (``--from`` / ``--clip-to-coverage``); never silently degraded, never
  filled with synthetic data (D26).
* **Depth is soft.** Missing recorded depth drops the fill fidelity tier and is
  flagged in the summary, not rejected.

``clip_to_coverage`` runs the **whole run** over the multi-venue *intersection*
of every leg's funding coverage — one coherent window for all legs, so per-venue
Sharpes stay comparable (§13) — and records that the range was clipped.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

import pyarrow as pa

from .ranges import Kind, RangeSet, TimeRange
from .sources import (
    DataSource,
    FlintDataAPIClient,
    FreeVenueProvider,
    InMemoryCacheSource,
)


class CoverageMode(Enum):
    """How the DataManager treats a coverage gap (§2.11)."""

    STRICT = "strict"  # default: any gap in a hard-required kind rejects
    CLIP_TO_COVERAGE = "clip_to_coverage"  # run the covered intersection instead


@dataclass(frozen=True, slots=True)
class Leg:
    """One ``(venue, market)`` a signal may target — a funding-gate unit (§6.3)."""

    venue: str
    market: str

    def label(self) -> str:
        return f"{self.market}@{self.venue}"


@dataclass(frozen=True)
class FidelityEntry:
    """The fidelity a single ``(leg, kind)`` resolved at (§9 fidelity summary)."""

    leg: Leg
    kind: Kind
    full: bool
    source: str  # provenance: which tier served it (or "none")
    note: str = ""


@dataclass(frozen=True)
class FidelitySummary:
    """What fidelity every ``(leg, kind)`` got — the tearsheet's honesty record."""

    entries: tuple[FidelityEntry, ...] = ()

    @property
    def all_full(self) -> bool:
        return all(e.full for e in self.entries)

    def degraded(self) -> tuple[FidelityEntry, ...]:
        return tuple(e for e in self.entries if not e.full)

    def lines(self) -> list[str]:
        """Human-readable provenance lines, e.g. for the tearsheet."""
        out = []
        for e in self.entries:
            tier = "full" if e.full else "DEGRADED"
            suffix = f" — {e.note}" if e.note else ""
            out.append(f"{e.leg.label()} {e.kind}: {tier} ({e.source}){suffix}")
        return out


@dataclass(frozen=True)
class PreparedData:
    """The DataManager's output — feeds + effective range + fidelity (§9)."""

    requested: TimeRange
    effective_range: TimeRange
    tables: dict[tuple[str, str, Kind], pa.Table] = field(default_factory=dict)
    fidelity: FidelitySummary = FidelitySummary()

    @property
    def clipped(self) -> bool:
        return (
            self.effective_range.start_ms != self.requested.start_ms
            or self.effective_range.end_ms != self.requested.end_ms
        )


class FundingCoverageError(Exception):
    """Raised in strict mode when funding coverage has a gap (§2.11 hard gate).

    Carries the per-leg gaps so a caller/agent can act on them; ``str`` is the
    user-facing rejection with the available range and the fix.
    """

    def __init__(self, gaps: dict[Leg, RangeSet], available: dict[Leg, RangeSet], requested: TimeRange) -> None:
        self.gaps = gaps
        self.available = available
        self.requested = requested
        super().__init__(self._format())

    def _format(self) -> str:
        lines = ["Funding data missing — backtest rejected (funding is a hard requirement)."]
        for leg, avail in self.available.items():
            if leg not in self.gaps:
                continue
            lines.append(f"  {_availability_phrase(leg, avail, self.requested)}")
        first = next(iter(self.gaps))
        avail = self.available[first]
        bounds = avail.bounds()
        if bounds is not None:
            from_date = _fmt_date(bounds.start_ms)
            lines.append(
                f"Re-run with --from {from_date}, or pass --clip-to-coverage."
            )
        else:
            lines.append("Re-run over a covered range, or pass --clip-to-coverage.")
        return "\n".join(lines)


class DataManager:
    """Resolves a backtest's data needs through the source chain (§9)."""

    def __init__(self, sources: Sequence[DataSource] | None = None) -> None:
        # Default chain: local cache -> Flint Data API (stub) -> free providers.
        # The free-venue tier is inert with no providers registered (no transport
        # configured on a bare manager); a caller wanting the HL fallback passes an
        # explicit chain with FreeVenueProvider([HyperliquidRestProvider(...)]).
        if sources is None:
            self._cache = InMemoryCacheSource()
            self._sources: tuple[DataSource, ...] = (
                self._cache,
                FlintDataAPIClient(),
                FreeVenueProvider(),
            )
        else:
            self._sources = tuple(sources)
            # Write-through targets the first source if it is a writable cache.
            self._cache = sources[0] if sources and isinstance(sources[0], InMemoryCacheSource) else None

    # --- public API --------------------------------------------------------

    def coverage(
        self, leg: Leg, kind: Kind, want: TimeRange
    ) -> RangeSet:
        """Union of what every source in the chain can serve for ``want``.

        This is the read side of ``flint data coverage`` — no fetch, no gate.
        """
        covered = RangeSet()
        for src in self._sources:
            covered = covered.union(src.available(leg.venue, leg.market, kind, want))
        return covered

    def prepare(
        self,
        universe: Sequence[str],
        venues: Sequence[str],
        kinds: Sequence[Kind],
        requested: TimeRange,
        *,
        mode: CoverageMode = CoverageMode.STRICT,
    ) -> PreparedData:
        """Resolve every leg × kind over ``requested`` and apply the gates.

        Legs are the product of ``venues × universe`` — every ``(venue, market)``
        a signal may target, so the funding gate checks them all (§3.2).
        """
        legs = [Leg(venue=v, market=m) for v in venues for m in universe]

        # 1. Funding hard gate up front, over the requested range (§2.11).
        funding_avail = {
            leg: self.coverage(leg, Kind.FUNDING, requested) for leg in legs
        }
        gaps = {
            leg: RangeSet((requested,)).subtract(avail)
            for leg, avail in funding_avail.items()
        }
        gaps = {leg: g for leg, g in gaps.items() if not g.is_empty}

        if mode is CoverageMode.STRICT:
            if gaps:
                raise FundingCoverageError(gaps, funding_avail, requested)
            effective = requested
        else:
            effective = self._clip_range(legs, funding_avail, requested)

        # 2. Resolve every leg × kind over the effective range + build fidelity.
        tables: dict[tuple[str, str, Kind], pa.Table] = {}
        entries: list[FidelityEntry] = []
        for leg in legs:
            for kind in kinds:
                table, covered = self._resolve(leg, kind, effective)
                tables[(leg.venue, leg.market, kind)] = table
                entries.append(self._fidelity(leg, kind, effective, covered))

        return PreparedData(
            requested=requested,
            effective_range=effective,
            tables=tables,
            fidelity=FidelitySummary(tuple(entries)),
        )

    # --- internals ---------------------------------------------------------

    def _clip_range(
        self, legs: list[Leg], funding_avail: dict[Leg, RangeSet], requested: TimeRange
    ) -> TimeRange:
        """The whole-run intersection of every leg's funding coverage (§13).

        The run window is one *contiguous* range fully covered by funding for
        every leg — the largest such window in the common coverage. Picking the
        envelope instead could span an internal gap and run over missing funding,
        which the hard gate forbids; the largest contiguous piece cannot.
        """
        covered = RangeSet((requested,))
        for leg in legs:
            covered = covered.intersect(funding_avail[leg])
        if covered.is_empty:
            # Nothing common across all legs — clip has nothing to run.
            raise FundingCoverageError(
                {leg: RangeSet((requested,)) for leg in legs}, funding_avail, requested
            )
        return max(covered.ranges, key=lambda r: r.duration_ms)

    def _resolve(
        self, leg: Leg, kind: Kind, want: TimeRange
    ) -> tuple[pa.Table, RangeSet]:
        """Walk the chain, fetching only missing sub-ranges, and merge them."""
        missing = RangeSet((want,))
        covered = RangeSet()
        fetched: list[pa.Table] = []
        for src in self._sources:
            if missing.is_empty:
                break
            servable = src.available(leg.venue, leg.market, kind, want).intersect(missing)
            for span in servable.ranges:
                table = src.fetch(leg.venue, leg.market, kind, span)
                if table.num_rows:
                    fetched.append(table)
                    # Write through to the local cache (skip if the cache is the
                    # source we just read from).
                    if self._cache is not None and src is not self._cache:
                        self._cache.store(leg.venue, leg.market, kind, table)
            covered = covered.union(servable)
            missing = missing.subtract(servable)

        merged = self._merge(fetched)
        return merged, covered

    @staticmethod
    def _merge(tables: list[pa.Table]) -> pa.Table:
        if not tables:
            return pa.table({})
        combined = pa.concat_tables(tables)
        if "ts" in combined.column_names:
            return combined.sort_by("ts")
        return combined

    @staticmethod
    def _fidelity(
        leg: Leg, kind: Kind, want: TimeRange, covered: RangeSet
    ) -> FidelityEntry:
        full = covered.covers(want)
        if full:
            return FidelityEntry(leg=leg, kind=kind, full=True, source="chain")
        if kind.is_degradable:
            return FidelityEntry(
                leg=leg,
                kind=kind,
                full=False,
                source="none",
                note="no recorded book for this range — using spread/impact model",
            )
        # A non-degradable, non-funding kind (candles/OI) with a gap: flagged so
        # the caller sees it, but not a hard reject (funding is gated separately).
        return FidelityEntry(
            leg=leg, kind=kind, full=False, source="none",
            note="partial coverage for this range",
        )


# --- formatting -------------------------------------------------------------


def _fmt_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _availability_phrase(leg: Leg, avail: RangeSet, requested: TimeRange) -> str:
    """e.g. ``Available range for SOL-PERP@hyperliquid: 2025-03-01 → present.``"""
    bounds = avail.bounds()
    if bounds is None:
        return f"Available range for {leg.label()}: none."
    start = _fmt_date(bounds.start_ms)
    end = "present" if bounds.end_ms >= requested.end_ms else _fmt_date(bounds.end_ms)
    return f"Available range for {leg.label()}: {start} → {end}."
