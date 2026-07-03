"""Data services — coverage + on-demand pull, behind the DataManager (§9, §12).

Market data is tenant-agnostic (shared by everyone, §2.7), so these take no
``TenantContext``. Coverage is the read side of ``flint data coverage`` — what
ranges exist before you run, with no fetch and no gate. Pull warms the cache
through the same source chain a backtest uses (there is no required download
step, §12); it runs best-effort so a partial range reports what it actually got
rather than rejecting.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from flint.data import TIERS, CoverageMode, DataManager, Leg
from flint.data.ranges import Kind, TimeRange

# A wide default window for "what do you have?" queries with no explicit range.
_FAR_FUTURE_MS = 32_503_680_000_000  # ~year 3000
_DEFAULT_KINDS: tuple[Kind, ...] = (Kind.CANDLES, Kind.FUNDING, Kind.OI)

# Every kind any tier needs — the ladder's provenance detail is reported for all
# of them so a Tier-2/3 gap shows which stream is missing (§B7).
_TIER_KINDS: tuple[Kind, ...] = tuple(
    dict.fromkeys(k for t in TIERS for k in (*t.required, *t.flagged))
)


def data_coverage(
    *,
    data: DataManager,
    market: str,
    venue: str,
    kinds: Sequence[Kind] = _DEFAULT_KINDS,
    start_ms: int = 0,
    end_ms: int | None = None,
) -> dict[str, Any]:
    """Coverage for ``(venue, market)`` — per-kind bounds, provenance, tiers (§B7).

    No fetch, no gate (§9). Beyond the legacy per-kind ``coverage`` bounds, this
    returns ``detail`` (each kind's covered pieces with the provenance that would
    serve them — tardis/recorder/hl_rest/local_cache) and ``tiers`` (one RangeSet
    per granularity tier: the intersection of its required kinds, FUNDING read as
    the settled series). Together they drive the UI's per-tier coverage ladder and
    tell auto-resolution which tier a range can run at.
    """
    want = TimeRange(start_ms, end_ms if end_ms is not None else _FAR_FUTURE_MS)
    leg = Leg(venue=venue, market=market)

    query_kinds = tuple(dict.fromkeys((*kinds, *_TIER_KINDS)))
    coverage: dict[str, Any] = {}
    detail: dict[str, Any] = {}
    for kind in query_kinds:
        rs = data.coverage(leg, kind, want)
        bounds = rs.bounds()
        coverage[kind.value] = (
            {"start_ms": bounds.start_ms, "end_ms": bounds.end_ms}
            if bounds is not None
            else None
        )
        detail[kind.value] = [
            {"start_ms": r.start_ms, "end_ms": r.end_ms, "source": label}
            for r, label in data.coverage_detail(leg, kind, want)
        ]

    tiers: dict[str, Any] = {}
    for tier in TIERS:
        per_kind = data.tier_coverage([leg], tier, want)[leg]
        covered = None
        for rs in per_kind.values():
            covered = rs if covered is None else covered.intersect(rs)
        pieces = covered.ranges if covered is not None else ()
        tiers[tier.name] = [
            {"start_ms": r.start_ms, "end_ms": r.end_ms} for r in pieces
        ]

    return {
        "market": market,
        "venue": venue,
        "coverage": coverage,
        "detail": detail,
        "tiers": tiers,
    }


def pull_data(
    *,
    data: DataManager,
    market: str,
    venues: Sequence[str],
    kind: Kind,
    start_ms: int,
    end_ms: int,
) -> dict[str, Any]:
    """Warm the cache for ``(venues, market, kind)`` over the range (best-effort).

    Reuses the DataManager source chain (cache → Flint Data API → free providers)
    with write-through, so a subsequent backtest finds the data local. Best-effort
    mode never funding-gates — this is a fetch, not a run — so it reports the rows
    and coverage it obtained.
    """
    tr = TimeRange(start_ms, end_ms)
    prepared = data.prepare(
        [market], list(venues), [kind], tr, mode=CoverageMode.CLIP_TO_COVERAGE
    )
    rows = {
        f"{v}/{m}/{k.value}": table.num_rows
        for (v, m, k), table in prepared.tables.items()
    }
    return {
        "market": market,
        "venues": list(venues),
        "kind": kind.value,
        "requested_range": {"start_ms": tr.start_ms, "end_ms": tr.end_ms},
        "effective_range": {
            "start_ms": prepared.effective_range.start_ms,
            "end_ms": prepared.effective_range.end_ms,
        },
        "rows": rows,
        "fidelity": prepared.fidelity.lines(),
    }
