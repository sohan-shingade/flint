"""Cross-venue funding & basis lab (§10, D6/D28) — the wedge, as analytics.

No incumbent normalizes funding across venues well; this module is that differentiator.
It takes funding observations from many venues, expresses them on one comparable hourly
scale, builds a cross-venue benchmark and each venue's dislocation from it, and tracks
basis (perp mark vs index). The UI renders these as a markets×venues heatmap and the
strategy ``ctx`` exposes the same numbers — so a user can both *see* a dislocation and
(on Hyperliquid) *trade* it.

**Read-only-except-HL (D28/§10).** In v1 Hyperliquid is the only executable venue;
Binance/Bybit/OKX funding is ingested **read-only** so a strategy can see every venue's
rate and trade the HL side of a dislocation. Taking a full cross-venue position waits on
a second executable adapter (v1.x). Nothing here places an order — it is pure analytics
over already-loaded observations, so the funding hard gate (§6.4, executable venues only)
does not apply to these read-only lab legs.

**Normalization convention (§10, Phase-2 ``FUNDING_SCHEMA``).** ``FundingRate.rate_hourly``
is already normalized to a 1-hour scale **linearly** (native rate / interval_hours) at
ingest, so venues with different native intervals (HL hourly, Binance 8h) compare like
with like without this module re-deriving anything. Annualization is likewise **linear**,
never compounded: a rate settled ``settlements/day`` times annualizes as
``per-settlement × settlements/day × 365`` (§10/§11.1), which — because ``rate_hourly`` is
interval-normalized — is identically ``rate_hourly × 24 × 365``.

D26: this module computes over supplied observations only; it fabricates no rate, and a
venue with no observation yet at a timestamp is simply absent from that timestamp's
benchmark (never zero-filled). It depends only downward on ``core.models`` (§4).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from flint.core.models import FundingRate, MarkSnapshot

# 365-day year (crypto trades weekends), in hours — the linear annualization base (§10).
HOURS_PER_YEAR_365 = 24 * 365  # 8760

# Strategy-visible funding is the PREDICTED rate (§6.4); the lab surfaces decision-time
# signal, so it defaults to predicted. Final rates drive payment, not the signal.
PREDICTED = "predicted"


# --- annualization (linear, settlements/day × 365 — never compounded, §10) ----


def settlements_per_day(interval_s: int) -> float:
    """How many funding settlements fall in a day at a venue's native interval (§10)."""
    if interval_s <= 0:
        raise ValueError("interval_s must be > 0")
    return 86_400 / interval_s


def settlements_per_year(interval_s: int) -> float:
    """Settlements per day × 365 (§10) — the linear annualization multiplier count."""
    return settlements_per_day(interval_s) * 365


def annualize_hourly(rate_hourly: float) -> float:
    """Annualize an already-hourly-normalized rate **linearly** (× 24 × 365, §10).

    Linear, never compounded: funding is a stream of discrete cash payments, not a
    reinvested return, so compounding would overstate carry. Because ``rate_hourly`` is
    interval-normalized at ingest, this is venue-agnostic."""
    return rate_hourly * HOURS_PER_YEAR_365


def annualized_rate(rate: FundingRate) -> float:
    """Annualize one funding row via its native settlement structure (§10).

    Frames annualization the way the design states it — per-settlement payment fraction
    (``rate_hourly × interval_hours``) times ``settlements/day × 365`` — which is
    identically :func:`annualize_hourly`\\ (rate_hourly) because ``rate_hourly`` is
    interval-normalized. Kept as a separate entry point so the settlements framing is
    explicit and pinned by a golden."""
    interval_hours = rate.interval_s / 3600
    per_settlement = rate.rate_hourly * interval_hours
    return per_settlement * settlements_per_year(rate.interval_s)


# --- per-venue carry summary (feeds the heatmap, §10) ------------------------


@dataclass(frozen=True)
class VenueFundingStat:
    """One venue's normalized funding carry for a market over a window (§10).

    ``mean_hourly`` is the mean of the observed hourly-normalized rates; ``annualized``
    is its linear annualization. A positive rate means longs pay shorts (§6.4), so a
    positive carry favors the short side (a funding harvester)."""

    venue: str
    market: str
    n: int
    mean_hourly: float
    annualized: float
    interval_s: int
    settlements_per_year: float


def _filter(rates: Sequence[FundingRate], rate_type: str | None) -> list[FundingRate]:
    if rate_type is None:
        return list(rates)
    return [r for r in rates if r.rate_type == rate_type]


def _single_market(rates: Sequence[FundingRate]) -> str:
    markets = {r.market for r in rates}
    if len(markets) > 1:
        raise ValueError(
            f"lab operates per-market; got mixed markets {sorted(markets)} — "
            "call once per market"
        )
    return next(iter(markets)) if markets else ""


def venue_funding_stats(
    rates: Sequence[FundingRate], *, rate_type: str | None = PREDICTED
) -> list[VenueFundingStat]:
    """Per-venue normalized funding carry for a single market (§10).

    Groups the observations by venue and reports each venue's mean hourly rate and its
    linear annualization — the per-cell numbers behind the markets×venues heatmap.
    Returns venues in sorted order for stable rendering."""
    market = _single_market(rates)
    rows = _filter(rates, rate_type)
    by_venue: dict[str, list[FundingRate]] = {}
    for r in rows:
        by_venue.setdefault(r.venue, []).append(r)

    out: list[VenueFundingStat] = []
    for venue in sorted(by_venue):
        obs = by_venue[venue]
        mean_hourly = sum(o.rate_hourly for o in obs) / len(obs)
        interval_s = obs[-1].interval_s  # native cadence (stable per venue)
        out.append(
            VenueFundingStat(
                venue=venue,
                market=market,
                n=len(obs),
                mean_hourly=mean_hourly,
                annualized=annualize_hourly(mean_hourly),
                interval_s=interval_s,
                settlements_per_year=settlements_per_year(interval_s),
            )
        )
    return out


# --- cross-venue benchmark + per-venue dislocation (§10) ---------------------


@dataclass(frozen=True)
class DislocationPoint:
    """At one timestamp: the cross-venue benchmark and each venue's deviation from it.

    ``venue_rates`` holds each venue's **as-of** hourly rate (the last one published at
    or before ``ts`` — no look-ahead, §8.2); a venue with no observation yet is absent,
    never zero-filled (D26). ``benchmark_hourly`` is the mean over the present venues;
    ``dislocations[v] = venue_rates[v] - benchmark_hourly``."""

    ts: int
    benchmark_hourly: float
    venue_rates: Mapping[str, float]
    dislocations: Mapping[str, float]


@dataclass(frozen=True)
class DislocationSeries:
    """The dislocation time series for one market across venues (§10)."""

    market: str
    venues: tuple[str, ...]
    points: tuple[DislocationPoint, ...]

    def venue_series(self, venue: str) -> list[tuple[int, float]]:
        """``(ts, dislocation)`` pairs for one venue, only where it had an as-of rate."""
        return [
            (p.ts, p.dislocations[venue])
            for p in self.points
            if venue in p.dislocations
        ]

    def widest(self) -> tuple[int, str, float] | None:
        """The single largest-magnitude dislocation as ``(ts, venue, signed value)``.

        This is the arb-finder's headline: where one venue's funding deviates most from
        the cross-venue benchmark. ``None`` when there is nothing to compare."""
        best: tuple[int, str, float] | None = None
        for p in self.points:
            for venue, d in p.dislocations.items():
                if best is None or abs(d) > abs(best[2]):
                    best = (p.ts, venue, d)
        return best

    def describe(self) -> str:
        lines = [f"dislocation lab: {self.market} across {', '.join(self.venues)}"]
        w = self.widest()
        if w is None:
            lines.append("  (no observations)")
        else:
            ts, venue, d = w
            lines.append(
                f"  widest: {venue} {d * 1e4:+.3f} bps/h vs benchmark at ts {ts}"
            )
        return "\n".join(lines)


def _asof(sorted_obs: Sequence[tuple[int, float]], ts: int) -> float | None:
    """Last rate published at or before ``ts`` (no look-ahead); ``None`` if none yet."""
    found: float | None = None
    for obs_ts, rate in sorted_obs:
        if obs_ts <= ts:
            found = rate
        else:
            break
    return found


def cross_venue_dislocation(
    rates: Sequence[FundingRate], *, rate_type: str | None = PREDICTED
) -> DislocationSeries:
    """Cross-venue benchmark and per-venue dislocation for one market (§10).

    On the union of observation timestamps, each venue contributes its **as-of** hourly
    rate (last published ≤ t, §8.2 — carrying a still-in-effect rate forward is not
    look-ahead; inventing a future one would be). The benchmark at each timestamp is the
    mean over venues that have an as-of rate, and dislocation is each venue's signed gap
    from it. A venue absent so far is left out of that timestamp entirely (D26)."""
    market = _single_market(rates)
    rows = _filter(rates, rate_type)
    venues = tuple(sorted({r.venue for r in rows}))

    by_venue: dict[str, list[tuple[int, float]]] = {v: [] for v in venues}
    for r in rows:
        by_venue[r.venue].append((r.ts, r.rate_hourly))
    for v in venues:
        by_venue[v].sort(key=lambda pair: pair[0])

    grid = sorted({r.ts for r in rows})
    points: list[DislocationPoint] = []
    for ts in grid:
        present: dict[str, float] = {}
        for v in venues:
            rate = _asof(by_venue[v], ts)
            if rate is not None:
                present[v] = rate
        if not present:
            continue
        benchmark = sum(present.values()) / len(present)
        dislocations = {v: rate - benchmark for v, rate in present.items()}
        points.append(
            DislocationPoint(
                ts=ts,
                benchmark_hourly=benchmark,
                venue_rates=present,
                dislocations=dislocations,
            )
        )
    return DislocationSeries(market=market, venues=venues, points=tuple(points))


# --- basis tracking (perp mark vs index, §10) --------------------------------


@dataclass(frozen=True)
class BasisPoint:
    """Perp premium/discount vs index at one timestamp (§10, §5.MarkSnapshot).

    ``basis_bps`` is ``(mark - index) / index × 10_000`` — positive when the perp trades
    rich to spot. Sourced from the recorded ``MarkSnapshot`` (the model owns the formula),
    never re-derived here."""

    ts: int
    venue: str
    market: str
    mark_price: float
    index_price: float
    basis_bps: float


def basis_series(marks: Sequence[MarkSnapshot]) -> list[BasisPoint]:
    """Basis time series from recorded mark snapshots, ascending by ts (§10).

    Uses ``MarkSnapshot.basis_bps`` (the model's own definition) so the perp-vs-index
    convention lives in one place. Snapshots with a non-positive index are skipped
    (their basis is undefined — never faked to 0)."""
    out: list[BasisPoint] = []
    for m in sorted(marks, key=lambda s: s.ts):
        if m.index_price <= 0:
            continue
        out.append(
            BasisPoint(
                ts=m.ts,
                venue=m.venue,
                market=m.market,
                mark_price=m.mark_price,
                index_price=m.index_price,
                basis_bps=m.basis_bps,
            )
        )
    return out


def mean_basis_bps(marks: Sequence[MarkSnapshot]) -> float:
    """Mean basis in bps over the recorded snapshots (undefined-index rows skipped)."""
    pts = basis_series(marks)
    return sum(p.basis_bps for p in pts) / len(pts) if pts else 0.0


__all__ = [
    "HOURS_PER_YEAR_365",
    "PREDICTED",
    "settlements_per_day",
    "settlements_per_year",
    "annualize_hourly",
    "annualized_rate",
    "VenueFundingStat",
    "venue_funding_stats",
    "DislocationPoint",
    "DislocationSeries",
    "cross_venue_dislocation",
    "BasisPoint",
    "basis_series",
    "mean_basis_bps",
]
