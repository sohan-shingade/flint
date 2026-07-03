"""UniverseResolver: point-in-time membership, exit behavior, gaps (slice 2.3).

All ranking histories are hand-authored (venue-neutral integers, not synthetic
market data — D26); the point of the suite is the no-look-ahead law, the exit
behaviors, the ranking-gap hold, and the event-log emission seam.
"""

from __future__ import annotations

import pytest

from flint.data import (
    UNIVERSE_SNAPSHOT,
    DynamicUniverse,
    ExitBehavior,
    InMemoryRankingData,
    Membership,
    StaticUniverse,
    UniverseResolver,
)


def _ranking(**series: list[tuple[int, float]]) -> InMemoryRankingData:
    rd = InMemoryRankingData()
    for market, points in series.items():
        rd.add(market, "volume", points)
    return rd


# --- static universes ------------------------------------------------------


def test_static_universe_is_constant_and_emits_snapshots():
    emitted: list[tuple[str, dict]] = []
    res = UniverseResolver(
        StaticUniverse(("SOL-PERP", "BTC-PERP")), emit=lambda k, p: emitted.append((k, p))
    )
    snaps = res.resolve([100, 200])
    assert all(s.active == ("SOL-PERP", "BTC-PERP") for s in snaps)
    assert all(not s.gap and not s.retained and not s.closing for s in snaps)
    # Every re-eval point writes a snapshot to the event log seam.
    assert [k for k, _ in emitted] == [UNIVERSE_SNAPSHOT, UNIVERSE_SNAPSHOT]
    assert emitted[0][1]["active"] == ["SOL-PERP", "BTC-PERP"]


# --- dynamic ranking is point-in-time (the core no-look-ahead law) ---------


def test_dynamic_ranks_top_n_using_only_data_before_t():
    # At t=150 only the points ts<150 count. AAA leads early; BBB overtakes later.
    rd = _ranking(
        AAA=[(100, 10.0), (200, 1.0)],
        BBB=[(100, 5.0), (200, 99.0)],
        CCC=[(100, 1.0)],
    )
    spec = DynamicUniverse.parse("top:2:volume", ["AAA", "BBB", "CCC"])
    res = UniverseResolver(spec, rd)

    early = res.resolve([150])[0]
    # Only ts<150 seen: AAA=10, BBB=5, CCC=1 -> top-2 = AAA, BBB (rank order).
    assert early.active == ("AAA", "BBB")

    late = res.resolve([250])[0]
    # Now ts<250 includes the 200 points: BBB=99, AAA=1, CCC=1 -> BBB, then AAA
    # (tie-break by name would only matter for AAA vs CCC; AAA=1==CCC=1 -> AAA).
    assert late.active == ("BBB", "AAA")


def test_ranking_is_deterministic_on_ties():
    rd = _ranking(ZZZ=[(0, 5.0)], AAA=[(0, 5.0)], MMM=[(0, 5.0)])
    spec = DynamicUniverse.parse("top:2:volume", ["ZZZ", "AAA", "MMM"])
    # Equal metric -> tie-break by market name ascending: AAA, MMM.
    assert UniverseResolver(spec, rd).resolve([10])[0].active == ("AAA", "MMM")


# --- exit behavior when a held market drops out ----------------------------


def _dropout_setup(exit_behavior: ExitBehavior) -> list[Membership]:
    # t=10: AAA & BBB lead. t=20: CCC surges, BBB collapses -> BBB drops out.
    rd = _ranking(
        AAA=[(0, 10.0), (15, 10.0)],
        BBB=[(0, 9.0), (15, 1.0)],
        CCC=[(0, 1.0), (15, 99.0)],
    )
    spec = DynamicUniverse.parse("top:2:volume", ["AAA", "BBB", "CCC"], exit_behavior=exit_behavior)
    return UniverseResolver(spec, rd).resolve([10, 20])


def test_hold_existing_keeps_dropped_market_for_management_only():
    snaps = _dropout_setup(ExitBehavior.HOLD_EXISTING)
    assert snaps[0].active == ("AAA", "BBB")
    # t=20: CCC surged to 99, BBB collapsed -> top-2 (rank order) = CCC, AAA;
    # BBB dropped -> retained (managed, no new entries).
    assert snaps[1].active == ("CCC", "AAA")
    assert snaps[1].retained == ("BBB",)
    assert snaps[1].closing == ()
    assert set(snaps[1].members) == {"AAA", "CCC", "BBB"}
    assert snaps[1].warnings == ()  # hold_existing is silent


def test_force_close_removes_and_closes_dropped_market():
    snaps = _dropout_setup(ExitBehavior.FORCE_CLOSE)
    assert snaps[1].active == ("CCC", "AAA")
    assert snaps[1].closing == ("BBB",)
    assert snaps[1].retained == ()
    assert "BBB" not in snaps[1].members  # force-closed and gone


def test_warn_keeps_dropped_market_but_flags_it():
    snaps = _dropout_setup(ExitBehavior.WARN)
    assert snaps[1].retained == ("BBB",)
    assert snaps[1].closing == ()
    assert any("BBB" in w and "warn" in w for w in snaps[1].warnings)


def test_retained_market_returns_to_active_when_it_re_ranks():
    rd = _ranking(
        AAA=[(0, 10.0), (15, 10.0), (25, 10.0)],
        BBB=[(0, 9.0), (15, 1.0), (25, 50.0)],  # drops at 20, surges by 30
        CCC=[(0, 1.0), (15, 99.0), (25, 2.0)],
    )
    spec = DynamicUniverse.parse("top:2:volume", ["AAA", "BBB", "CCC"])
    snaps = UniverseResolver(spec, rd).resolve([10, 20, 30])
    assert snaps[1].retained == ("BBB",)  # dropped at t=20
    assert snaps[2].active == ("BBB", "AAA")  # BBB re-ranks into top-2 at t=30
    assert "BBB" not in snaps[2].retained  # ...and is no longer just "held"
    # CCC (active at t=20) now drops out in turn and is retained instead.
    assert snaps[2].retained == ("CCC",)


# --- ranking-data gap holds previous membership (never re-ranks partial) ---


def test_gap_at_first_eval_yields_flagged_empty_membership():
    rd = _ranking(AAA=[(500, 10.0)])  # no data before t=100
    spec = DynamicUniverse.parse("top:2:volume", ["AAA", "BBB"])
    snap = UniverseResolver(spec, rd).resolve([100])[0]
    assert snap.gap and snap.members == ()
    assert any("gap" in w for w in snap.warnings)


def test_gap_at_later_eval_holds_previous_membership():
    # t=10 ranks fine; t=20 has no fresh data (still fine, uses ts<20), but t=5
    # before any data is a gap. Construct: data only at ts=8.
    rd = _ranking(AAA=[(8, 10.0)], BBB=[(8, 9.0)])
    spec = DynamicUniverse.parse("top:2:volume", ["AAA", "BBB"])
    res = UniverseResolver(spec, rd)
    snaps = res.resolve([5, 10])  # t=5: nothing<5 -> gap; t=10: sees the ts=8 points
    assert snaps[0].gap and snaps[0].members == ()
    assert not snaps[1].gap and snaps[1].active == ("AAA", "BBB")


def test_gap_preserves_active_and_flags_when_feed_drops_out():
    rd = InMemoryRankingData()
    # AAA/BBB have an early point, then the feed goes silent — a later re-eval
    # still sees the early points (ts<t), so force a true gap with a candidate
    # pool that only has data at one instant, evaluated before it.
    rd.add("AAA", "volume", [(100, 5.0)])
    rd.add("BBB", "volume", [(100, 4.0)])
    spec = DynamicUniverse.parse("top:2:volume", ["AAA", "BBB"])
    res = UniverseResolver(spec, rd)
    snaps = res.resolve([50, 200])  # 50 -> gap (nothing<50); 200 -> ranks
    assert snaps[0].gap
    assert snaps[1].active == ("AAA", "BBB")


# --- all_members (the set to funding-gate up front) + parsing --------------


def test_all_members_unions_active_retained_and_closing():
    snaps = _dropout_setup(ExitBehavior.FORCE_CLOSE)
    res = UniverseResolver(DynamicUniverse.parse("top:2:volume", ["AAA", "BBB", "CCC"]), _ranking())
    # AAA, BBB (t=10) + AAA, CCC active + BBB closing (t=20).
    assert res.all_members(snaps) == frozenset({"AAA", "BBB", "CCC"})


def test_parse_rejects_bad_rules_and_dynamic_needs_ranking():
    with pytest.raises(ValueError):
        DynamicUniverse.parse("top:volume", ["AAA"])  # missing N
    with pytest.raises(ValueError):
        DynamicUniverse.parse("bottom:5:volume", ["AAA"])  # not top:
    with pytest.raises(ValueError):
        DynamicUniverse.parse("top:0:volume", ["AAA"])  # N must be positive
    with pytest.raises(ValueError):
        UniverseResolver(DynamicUniverse.parse("top:2:volume", ["AAA"]))  # no ranking
