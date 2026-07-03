"""DataManager — 'declare a range + universe, the data appears' (§9, §2.11).

The engine is source-agnostic: it asks the DataManager for a universe × kinds ×
range and gets back a time-ordered feed per ``(venue, market, kind)`` plus a
**fidelity summary** (what came at full vs degraded fidelity). Under the hood the
manager walks the source chain per instrument×kind×range, fetching only the
**missing** sub-ranges from each tier and merging them (partial-range merge), and
writes every fetched range through to the local cache.

Two gates decide what happens at a coverage gap (§2.11):

* **Funding is hard — but only where the run can execute.** A perp's PnL is
  wrong without funding, so a funding gap on an **executable** venue (the run's
  ``venues=`` set — positions settle there) *rejects* the run: in ``strict`` mode
  with the exact missing ranges and the fix (``--from`` / ``--clip-to-coverage``);
  never silently degraded, never filled with synthetic data (D26). Funding read
  purely as a **signal** from a read-only lab venue (``signal_venues=`` — the
  cross-venue funding/basis lab's Binance/Bybit/OKX in v1, D28) instead follows
  the §8.2 soft contract: a gap yields ``None`` + a fidelity flag, never a
  rejection — "a wrong number there can't fill an order" (D28), and §8.2 prefers
  ``None`` over stale. This keeps the HL-native cross-venue-signal workflow (§3.2)
  usable even when a lab venue's rate history has holes.
* **Depth is soft.** Missing recorded depth drops the fill fidelity tier and is
  flagged in the summary, not rejected.

``clip_to_coverage`` runs the **whole run** over the multi-venue *intersection*
of every leg's funding coverage — one coherent window for all legs, so per-venue
Sharpes stay comparable (§13) — and records that the range was clipped.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
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
from .tiers import CANDLES_TIER, GRANULARITIES, TIER_BY_NAME, TIERS, Tier


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
    """The DataManager's output — feeds + effective range + fidelity (§9).

    ``streams`` (D5, additive) is the tick-scale read surface: a zero-argument
    callable per ``(venue, market, kind)`` returning a fresh bounded iterator
    of Arrow ``RecordBatch``es over the effective range. Every tick-scale kind
    (TRADES / QUOTES / BOOK_DELTA) gets a handle; **BOOK_DELTA is streams-only**
    — its ``tables`` entry is an empty placeholder, because materializing an L2
    day (tens of millions of flat rows) is exactly what the streaming surface
    exists to avoid. Threshold decision, documented: a row-count threshold
    would require materializing to count, so the cut is kind-level — BOOK_DELTA
    is unconditionally book-replay volume; TRADES/QUOTES stay table-friendly at
    HL volumes and keep their ``tables`` entries unchanged (handles are extra).
    """

    requested: TimeRange
    effective_range: TimeRange
    tables: dict[tuple[str, str, Kind], pa.Table] = field(default_factory=dict)
    fidelity: FidelitySummary = FidelitySummary()
    streams: dict[
        tuple[str, str, Kind], Callable[[], Iterator[pa.RecordBatch]]
    ] = field(default_factory=dict)
    #: The granularity tier the run resolved to (§B7): "candles" | "ticks" | "book".
    granularity: str = "candles"
    #: Human-readable provenance lines for the resolved tier — tearsheet honesty,
    #: e.g. "granularity: ticks" + per-leg "trades: tardis 2024-10-29 → …".
    granularity_detail: tuple[str, ...] = ()

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


class GranularityUnavailableError(Exception):
    """Raised when an *explicit* granularity tier has a coverage gap (§B7).

    Like :class:`FundingCoverageError`, this is expected data scarcity, not a
    fault: the services front door converts it into a structured §19.1
    ``rejected`` payload (code ``granularity_unavailable``) carrying the per-leg
    per-kind coverage and the machine-readable ``options`` out — run at bars,
    clip to coverage, vendor backfill (advertised even without a key), or
    record forward. It never surfaces as an HTTP error.
    """

    def __init__(
        self,
        requested_tier: str,
        coverage: dict[Leg, dict[Kind, RangeSet]],
        options: list[dict[str, object]],
        requested: TimeRange,
    ) -> None:
        self.requested_tier = requested_tier
        self.coverage = coverage
        self.options = options
        self.requested = requested
        super().__init__(self._format())

    def _format(self) -> str:
        lines = [
            f"Granularity {self.requested_tier!r} unavailable over the requested "
            "range — backtest rejected."
        ]
        for leg, per_kind in self.coverage.items():
            for kind, rs in per_kind.items():
                if rs.covers(self.requested):
                    continue
                bounds = rs.bounds()
                have = (
                    f"{_fmt_date(bounds.start_ms)} → {_fmt_date(bounds.end_ms)}"
                    if bounds is not None
                    else "none"
                )
                lines.append(f"  {leg.label()} {kind.value}: covered {have}.")
        actions = ", ".join(str(o.get("action")) for o in self.options)
        if actions:
            lines.append(f"Options: {actions}.")
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
            # Write-through targets the first source if it is a writable cache
            # (in-memory or the durable Parquet tier — both expose ``store``).
            from .store.durable_cache import DurableCacheSource

            self._cache = (
                sources[0]
                if sources
                and isinstance(sources[0], (InMemoryCacheSource, DurableCacheSource))
                else None
            )

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

    def funding_coverage(
        self, leg: Leg, want: TimeRange, *, rate_type: str = "final"
    ) -> RangeSet:
        """Union of one FUNDING *series*' coverage across the chain (§6.4).

        The hard gate consults ``rate_type="final"`` — only the settled series
        settles money. ``"predicted"`` coverage (the recorder's ctx fold, Tardis
        derivative_ticker) drives the fidelity flag, never the gate.
        """
        covered = RangeSet()
        for src in self._sources:
            covered = covered.union(
                src.available_funding(leg.venue, leg.market, want, rate_type=rate_type)
            )
        return covered

    def coverage_detail(
        self, leg: Leg, kind: Kind, want: TimeRange
    ) -> tuple[tuple[TimeRange, str], ...]:
        """Coverage as (range, provenance) pieces, chain-priority merged (§B7).

        Each source reports its coverage with a provenance label (ledger
        entries carry their asserting ingester — tardis/recorder/hl_rest);
        earlier tiers claim ranges first, so an overlap reads as the tier that
        would actually serve it. Pieces are non-overlapping, sorted by start.
        """
        claimed = RangeSet()
        out: list[tuple[TimeRange, str]] = []
        for src in self._sources:
            for piece, label in src.provenance(leg.venue, leg.market, kind, want):
                fresh = RangeSet((piece,)).subtract(claimed)
                for sub in fresh.ranges:
                    out.append((sub, label))
                claimed = claimed.union(fresh)
        return tuple(sorted(out, key=lambda p: p[0].start_ms))

    def tier_coverage(
        self, legs: Sequence[Leg], tier: Tier, want: TimeRange
    ) -> dict[Leg, dict[Kind, RangeSet]]:
        """Per-leg per-required-kind coverage for one tier — queries only, no fetch.

        FUNDING coverage means the **final/settled** series (§6.4): predicted
        captures never make a tier (or the gate) look covered.
        """
        per_leg: dict[Leg, dict[Kind, RangeSet]] = {}
        for leg in legs:
            per_kind: dict[Kind, RangeSet] = {}
            for kind in tier.required:
                if kind is Kind.FUNDING:
                    per_kind[kind] = self.funding_coverage(leg, want)
                else:
                    per_kind[kind] = self.coverage(leg, kind, want)
            per_leg[leg] = per_kind
        return per_leg

    def prepare(
        self,
        universe: Sequence[str],
        venues: Sequence[str],
        kinds: Sequence[Kind],
        requested: TimeRange,
        *,
        mode: CoverageMode = CoverageMode.STRICT,
        signal_venues: Sequence[str] = (),
        granularity: str = "candles",
    ) -> PreparedData:
        """Resolve every leg × kind over ``requested`` and apply the gates.

        ``venues`` are the **executable** venues (the run's ``venues=`` — positions
        settle there), so their legs are the only ones the funding hard gate can
        reject over (§2.11, D28). ``signal_venues`` are read-only lab venues (the
        cross-venue funding/basis lab's Binance/Bybit/OKX in v1): their legs are
        resolved for the same ``kinds`` but are **never** funding-gated — a gap
        degrades per the §8.2 soft contract (``None`` + a fidelity flag), it never
        rejects the run. A venue named in both sets is treated as executable (the
        stronger contract wins).

        ``granularity`` picks the tier of market data the run consumes (§B7):
        ``"candles"`` (default — the pre-tier behavior, byte-identical),
        ``"ticks"`` / ``"book"`` (explicit: a coverage gap in the tier's kinds
        raises :class:`GranularityUnavailableError`, resolved **before** the
        funding gate), or ``"auto"`` (highest tier whose required kinds fully
        cover the range for every executable leg — coverage queries only,
        falling back to the candles floor). Tiers above candles extend the
        fetched kinds with the tier's market-data kinds.
        """
        if granularity not in GRANULARITIES:
            raise ValueError(
                f"unknown granularity {granularity!r}; expected one of "
                f"{list(GRANULARITIES)}"
            )
        exec_legs = [Leg(venue=v, market=m) for v in venues for m in universe]
        exec_venues = set(venues)
        signal_legs = [
            Leg(venue=v, market=m)
            for v in signal_venues
            if v not in exec_venues
            for m in universe
        ]

        # 0. Granularity resolution — BEFORE the funding gate (§B7): an explicit
        #    tier with a gap is its own structured rejection; auto never rejects
        #    (the candles floor + the funding gate keep their own contracts).
        tier = self._resolve_tier(exec_legs, requested, granularity)
        kinds = tuple(dict.fromkeys(kinds))
        if tier.name != CANDLES_TIER.name:
            kinds = tuple(
                dict.fromkeys((*kinds, *tier.market_data_kinds))
            )

        # 1. Funding hard gate up front, over the requested range — EXECUTABLE
        #    legs only (§2.11, D28), and only the FINAL/settled series counts:
        #    that is what settles money (§6.4). Predicted-rate captures (the
        #    recorder's ctx fold, Tardis derivative_ticker) drive the fidelity
        #    flag below, never this gate. Signal-only lab legs degrade, never gate.
        #    The gate protects *runs*: a request that does not consume FUNDING at
        #    all (``pull_data`` warming trades/quotes) is a fetch, not a run —
        #    nothing settles, so there is nothing to gate or clip against.
        if Kind.FUNDING in kinds:
            funding_avail = {
                leg: self.funding_coverage(leg, requested) for leg in exec_legs
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
                effective = self._clip_range(exec_legs, funding_avail, requested)
        else:
            effective = requested

        # 2. Resolve every leg × kind over the effective range + build fidelity.
        #    Executable legs first; then signal legs, flagged so their funding
        #    gaps read as the §8.2 soft contract, not an executable-venue gap.
        tables: dict[tuple[str, str, Kind], pa.Table] = {}
        streams: dict[
            tuple[str, str, Kind], Callable[[], Iterator[pa.RecordBatch]]
        ] = {}
        entries: list[FidelityEntry] = []
        for leg in exec_legs:
            for kind in kinds:
                table, covered = self._prepare_kind(
                    leg, kind, effective, streams
                )
                tables[(leg.venue, leg.market, kind)] = table
                entries.append(self._fidelity(leg, kind, effective, covered))
        for leg in signal_legs:
            for kind in kinds:
                table, covered = self._prepare_kind(
                    leg, kind, effective, streams
                )
                tables[(leg.venue, leg.market, kind)] = table
                entries.append(
                    self._fidelity(leg, kind, effective, covered, signal=True)
                )

        # 3. Funding fidelity sub-entry (§B4): report the *predicted* series
        #    separately from the settled one the gate enforced — a tick-lane
        #    strategy sees the true recorded rate path only where predicted
        #    coverage exists; elsewhere the engine bridges from settled, and
        #    the tearsheet must say so.
        if Kind.FUNDING in kinds:
            for leg in exec_legs:
                entries.append(self._funding_predicted_entry(leg, effective))

        return PreparedData(
            requested=requested,
            effective_range=effective,
            tables=tables,
            fidelity=FidelitySummary(tuple(entries)),
            streams=streams,
            granularity=tier.name,
            granularity_detail=self._granularity_detail(tier, exec_legs, effective),
        )

    # --- internals ---------------------------------------------------------

    def _resolve_tier(
        self, exec_legs: list[Leg], requested: TimeRange, granularity: str
    ) -> Tier:
        """Resolve ``granularity`` to a tier — coverage queries only (§B7).

        ``"candles"`` is the floor and never granularity-rejects (candle gaps
        keep their established flagged behavior; funding gaps stay the funding
        gate's). ``"auto"`` walks the ladder top-down and takes the highest
        fully covered tier, else the floor. An explicit ``"ticks"``/``"book"``
        with a gap raises the structured rejection with the ways out.
        """
        if granularity == CANDLES_TIER.name:
            return CANDLES_TIER
        if granularity == "auto":
            for tier in reversed(TIERS):
                if tier.name == CANDLES_TIER.name:
                    continue
                if _tier_fully_covered(
                    self.tier_coverage(exec_legs, tier, requested), requested
                ):
                    return tier
            return CANDLES_TIER
        tier = TIER_BY_NAME[granularity]
        coverage = self.tier_coverage(exec_legs, tier, requested)
        if _tier_fully_covered(coverage, requested):
            return tier
        raise GranularityUnavailableError(
            granularity,
            coverage,
            self._tier_options(exec_legs, tier, coverage, requested),
            requested,
        )

    def _tier_options(
        self,
        exec_legs: list[Leg],
        tier: Tier,
        coverage: dict[Leg, dict[Kind, RangeSet]],
        requested: TimeRange,
    ) -> list[dict[str, object]]:
        """The machine-readable ways out of a granularity gap (§B7, §19.1)."""
        options: list[dict[str, object]] = []

        # run_bars — only offered when the candles tier actually covers.
        candles_cov = self.tier_coverage(exec_legs, CANDLES_TIER, requested)
        if _tier_fully_covered(candles_cov, requested):
            options.append({"action": "run_bars", "granularity": CANDLES_TIER.name})

        # clip_to_coverage — largest contiguous window where every required
        # kind of the requested tier covers every leg (same contiguity rule as
        # the funding-gate clip: an internal gap must never be spanned).
        common = RangeSet((requested,))
        for per_kind in coverage.values():
            for rs in per_kind.values():
                common = common.intersect(rs)
        if not common.is_empty:
            best = max(common.ranges, key=lambda r: r.duration_ms)
            options.append(
                {
                    "action": "clip_to_coverage",
                    "effective_range": {
                        "start_ms": best.start_ms,
                        "end_ms": best.end_ms,
                    },
                }
            )

        # vendor_backfill — advertised even without a key (D23 gates coverage,
        # not knowledge): per missing (leg, kind), the vendor window from
        # exchange metadata when a vendor tier is wired; a static advert
        # otherwise, so the user still learns the fix exists.
        available: dict[str, dict[str, object]] = {}
        for leg, per_kind in coverage.items():
            for kind, rs in per_kind.items():
                if rs.covers(requested) or kind is Kind.FUNDING:
                    continue
                advert = self._backfill_advert(leg, kind)
                if advert is not None:
                    available.setdefault(leg.label(), {})[kind.value] = advert[
                        "available"
                    ]
        options.append(
            {
                "action": "vendor_backfill",
                "vendor": "tardis",
                "requires_secret": "TARDIS_API_KEY",
                "available": available or None,
            }
        )

        options.append(
            {
                "action": "record_forward",
                "hint": (
                    "flint data record --venue "
                    + ",".join(sorted({leg.venue for leg in exec_legs}))
                    + " --market "
                    + ",".join(sorted({leg.market for leg in exec_legs}))
                ),
            }
        )
        return options

    def _backfill_advert(self, leg: Leg, kind: Kind) -> dict[str, object] | None:
        for src in self._sources:
            advert = getattr(src, "backfill_advert", None)
            if advert is None:
                continue
            got = advert(leg.venue, leg.market, kind)
            if got is not None:
                return got
        return None

    def _funding_predicted_entry(self, leg: Leg, effective: TimeRange) -> FidelityEntry:
        """The §B4 funding sub-entry: true predicted series vs settled-bridge."""
        predicted = self.funding_coverage(leg, effective, rate_type="predicted")
        if predicted.covers(effective):
            labels = sorted(
                {
                    src.name
                    for src in self._sources
                    if not src.available_funding(
                        leg.venue, leg.market, effective, rate_type="predicted"
                    ).is_empty
                }
            )
            note = f"predicted series: true ({'|'.join(labels)})"
        else:
            note = "predicted series: derived from settled — bridge"
        return FidelityEntry(
            leg=leg, kind=Kind.FUNDING, full=True, source="funding", note=note
        )

    def _granularity_detail(
        self, tier: Tier, exec_legs: list[Leg], effective: TimeRange
    ) -> tuple[str, ...]:
        """Tearsheet lines: the resolved tier + who serves its tick-scale kinds."""
        lines = [f"granularity: {tier.name}"]
        if tier.name == CANDLES_TIER.name:
            return tuple(lines)
        for leg in exec_legs:
            for kind in tier.market_data_kinds:
                pieces = self.coverage_detail(leg, kind, effective)
                if not pieces:
                    continue
                spans = ", ".join(
                    f"{label} {_fmt_date(r.start_ms)} → {_fmt_date(r.end_ms)}"
                    for r, label in pieces
                )
                lines.append(f"  {leg.label()} {kind.value}: {spans}")
        return tuple(lines)

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

    def _prepare_kind(
        self,
        leg: Leg,
        kind: Kind,
        want: TimeRange,
        streams: dict[tuple[str, str, Kind], Callable[[], Iterator[pa.RecordBatch]]],
    ) -> tuple[pa.Table, RangeSet]:
        """Resolve one ``(leg, kind)`` — table for most kinds, streams for tick (D5).

        Non-tick kinds are exactly the pre-D5 ``_resolve`` path. Tick-scale
        kinds additionally get a ``streams`` handle; BOOK_DELTA gets *only* the
        handle (its ``tables`` entry stays an empty placeholder — never
        materialized whole, see ``PreparedData``). Coverage for BOOK_DELTA is
        computed from ``available()`` alone, so fidelity stays honest without a
        fetch. Write-through of vendor-served streamed rows is the ingester's
        job (the Tardis fetcher's day-level sink / the recorder), not the
        stream's: a stream may be consumed zero or many times, so it must not
        be the thing that lands coverage.
        """
        key = (leg.venue, leg.market, kind)
        if kind is Kind.BOOK_DELTA:
            covered = self.coverage(leg, kind, want)
            streams[key] = self._stream_handle(leg, kind, want)
            return pa.table({}), covered
        table, covered = self._resolve(leg, kind, want)
        if kind.is_tick_scale:
            streams[key] = self._stream_handle(leg, kind, want)
        return table, covered

    def _stream_handle(
        self, leg: Leg, kind: Kind, want: TimeRange
    ) -> Callable[[], Iterator[pa.RecordBatch]]:
        """A restartable batch-stream over ``want``, planned lazily per call.

        Each call re-walks the source chain (first source that declares a
        sub-range serves it — the same priority rule ``_resolve`` applies) and
        yields ``fetch_batches`` output span by span in ascending start order.
        Planning at call time means a stream opened after a backfill sees the
        new coverage.
        """

        def _iterate() -> Iterator[pa.RecordBatch]:
            missing = RangeSet((want,))
            plan: list[tuple[DataSource, TimeRange]] = []
            for src in self._sources:
                if missing.is_empty:
                    break
                servable = src.available(
                    leg.venue, leg.market, kind, want
                ).intersect(missing)
                plan.extend((src, span) for span in servable.ranges)
                missing = missing.subtract(servable)
            plan.sort(key=lambda entry: entry[1].start_ms)
            for src, span in plan:
                yield from src.fetch_batches(leg.venue, leg.market, kind, span)

        return _iterate

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
        leg: Leg,
        kind: Kind,
        want: TimeRange,
        covered: RangeSet,
        *,
        signal: bool = False,
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
        if signal and kind is Kind.FUNDING:
            # Read-only lab venue: a funding gap is the §8.2 soft contract (None +
            # flag at the ctx accessor), never a rejection (D28). The executable
            # venue's funding is what the hard gate protects.
            return FidelityEntry(
                leg=leg,
                kind=kind,
                full=False,
                source="none",
                note="signal-only venue — funding gap returns None + flag (§8.2), not a rejection",
            )
        # A non-degradable, non-funding kind (candles/OI) with a gap: flagged so
        # the caller sees it, but not a hard reject (funding is gated separately).
        return FidelityEntry(
            leg=leg, kind=kind, full=False, source="none",
            note="partial coverage for this range",
        )


def _tier_fully_covered(
    coverage: dict[Leg, dict[Kind, RangeSet]], requested: TimeRange
) -> bool:
    """True when every required kind of every leg covers the whole range."""
    return all(
        rs.covers(requested)
        for per_kind in coverage.values()
        for rs in per_kind.values()
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
