"""Funding/basis lab service — the data behind the markets×venues heatmap (§10, §12).

Screen 2's heatmap wants *real funding carry* per (venue, market): the mean hourly
rate and its linear annualization, plus where one venue's funding most dislocates
from the cross-venue benchmark (the arb headline). This service is the front door
carry-forward (e) specifies: a ``TenantContext`` + ``DataManager`` query that feeds
the pure ``research.funding_lab`` functions.

The lab is **read-only** and **degrade-not-reject**: every requested venue is passed
to ``DataManager.prepare`` as a ``signal_venues`` leg, so the funding hard gate
(§6.4, which can only reject *executable*-venue runs) never fires here — a venue
with no funding in the window simply yields a ``null`` cell (the §8.2 soft contract),
never a rejection [6.5, 4260b7f ruling]. Market data is tenant-agnostic (§2.7), but
the query takes ``tenant`` to honor the services contract (every services function is
tenant-scoped) and to anchor future per-tenant lab quotas.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from flint.core.models.market import FundingRate
from flint.data import CoverageMode, DataManager
from flint.data.ranges import Kind, TimeRange
from flint.ports import TenantContext
from flint.research import cross_venue_dislocation, venue_funding_stats
from flint.research.funding_lab import PREDICTED

# "what carry does this market×venue show?" over an unbounded window if unspecified.
_FAR_FUTURE_MS = 32_503_680_000_000  # ~year 3000


def funding_lab(
    tenant: TenantContext,
    *,
    data: DataManager,
    markets: Sequence[str],
    venues: Sequence[str],
    start_ms: int = 0,
    end_ms: int | None = None,
    rate_type: str | None = PREDICTED,
) -> dict[str, Any]:
    """Per-(venue, market) funding carry + cross-venue dislocation for the heatmap (§10).

    Resolves funding for every ``markets × venues`` leg as a read-only lab leg (no
    funding gate), then folds it through ``research.funding_lab``:
      * ``cells[venue/market]`` = the venue's :class:`VenueFundingStat` for that market
        (mean hourly rate, linear annualization, obs count, native interval) — or
        ``null`` when that leg has no observations of ``rate_type`` in the window.
      * ``dislocation[market]`` = the widest signed dislocation from the cross-venue
        benchmark (the arb headline), or ``null`` when there is nothing to compare.
    ``rate_type`` defaults to the strategy-visible ``predicted`` rate (§6.4) — the
    decision-time carry a funding harvester sees; pass ``None`` to include all rows.
    """
    _ = tenant  # tenant-scoped by contract; market data itself is shared (§2.7)
    want = TimeRange(start_ms, end_ms if end_ms is not None else _FAR_FUTURE_MS)

    # All requested venues are lab (signal) legs → no executable leg → no funding
    # gate can fire; partial coverage degrades to fewer observations, never rejects.
    prepared = data.prepare(
        list(markets),
        [],  # no executable venues: this is a read-only lab view
        [Kind.FUNDING],
        want,
        mode=CoverageMode.STRICT,
        signal_venues=list(venues),
    )

    # Group the resolved funding rows by market (funding_lab operates per-market).
    by_market: dict[str, list[FundingRate]] = {m: [] for m in markets}
    for (venue, market, kind), table in prepared.tables.items():
        if kind is not Kind.FUNDING:
            continue
        rows = by_market.setdefault(market, [])
        for r in table.to_pylist():
            rows.append(
                FundingRate(
                    market=r["market"],
                    ts=r["ts"],
                    rate_hourly=r["rate_hourly"],
                    interval_s=r["interval_s"],
                    price_basis=r["price_basis"],
                    rate_type=r["rate_type"],
                    venue=r["venue"],
                )
            )

    cells: dict[str, dict[str, Any] | None] = {}
    dislocation: dict[str, dict[str, Any] | None] = {}
    for market in markets:
        rates = by_market.get(market, [])
        stats = {s.venue: s for s in venue_funding_stats(rates, rate_type=rate_type)}
        for venue in venues:
            s = stats.get(venue)
            cells[f"{venue}/{market}"] = (
                {
                    "venue": s.venue,
                    "market": s.market,
                    "n": s.n,
                    "mean_hourly": s.mean_hourly,
                    "annualized": s.annualized,
                    "interval_s": s.interval_s,
                    "settlements_per_year": s.settlements_per_year,
                }
                if s is not None
                else None
            )

        series = cross_venue_dislocation(rates, rate_type=rate_type)
        widest = series.widest()
        dislocation[market] = (
            {"ts": widest[0], "venue": widest[1], "dislocation_hourly": widest[2]}
            if widest is not None
            else None
        )

    return {
        "markets": list(markets),
        "venues": list(venues),
        "rate_type": rate_type,
        "requested_range": {"start_ms": want.start_ms, "end_ms": want.end_ms},
        "effective_range": {
            "start_ms": prepared.effective_range.start_ms,
            "end_ms": prepared.effective_range.end_ms,
        },
        "cells": cells,
        "dislocation": dislocation,
        "fidelity": prepared.fidelity.lines(),
    }
