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

from flint.data import CoverageMode, DataManager, Leg
from flint.data.ranges import Kind, TimeRange

# A wide default window for "what do you have?" queries with no explicit range.
_FAR_FUTURE_MS = 32_503_680_000_000  # ~year 3000
_DEFAULT_KINDS: tuple[Kind, ...] = (Kind.CANDLES, Kind.FUNDING, Kind.OI)


def data_coverage(
    *,
    data: DataManager,
    market: str,
    venue: str,
    kinds: Sequence[Kind] = _DEFAULT_KINDS,
    start_ms: int = 0,
    end_ms: int | None = None,
) -> dict[str, Any]:
    """Per-kind covered ranges for ``(venue, market)`` — no fetch, no gate (§9)."""
    want = TimeRange(start_ms, end_ms if end_ms is not None else _FAR_FUTURE_MS)
    leg = Leg(venue=venue, market=market)
    coverage: dict[str, Any] = {}
    for kind in kinds:
        rs = data.coverage(leg, kind, want)
        bounds = rs.bounds()
        coverage[kind.value] = (
            {"start_ms": bounds.start_ms, "end_ms": bounds.end_ms}
            if bounds is not None
            else None
        )
    return {"market": market, "venue": venue, "coverage": coverage}


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
