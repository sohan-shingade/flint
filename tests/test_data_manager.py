"""DataManager: source chain, partial-range merge, fidelity, funding gate (2.2).

All fixtures are hand-authored ts-keyed Arrow tables (unit inputs, never
synthetic market data — D26). No source touches a network; the 'API' and
'provider' tiers are just pre-loaded in-memory sources injected into the chain.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pyarrow as pa
import pytest

from flint.data import (
    CoverageMode,
    DataManager,
    FundingCoverageError,
    InMemoryCacheSource,
    Kind,
    Leg,
    TimeRange,
)


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


def _rows(ts_values: list[int], value_col: str = "close") -> pa.Table:
    return pa.table({"ts": ts_values, value_col: [float(i) for i in range(len(ts_values))]})


def _loaded(entries: dict[tuple[str, str, Kind], pa.Table]) -> InMemoryCacheSource:
    src = InMemoryCacheSource()
    for (venue, market, kind), table in entries.items():
        src.store(venue, market, kind, table)
    return src


SOL = Leg("hyperliquid", "SOL-PERP")
BTC = Leg("hyperliquid", "BTC-PERP")


# --- source chain: partial-range merge + write-through ---------------------


def test_partial_range_merge_fetches_only_missing_and_writes_through():
    # Cache holds [0,51) (rows at 0,50); an 'API' tier holds [50,100) (50,99).
    cache = _loaded({(SOL.venue, SOL.market, Kind.FUNDING): _rows([0, 50], "rate")})
    api = _loaded({(SOL.venue, SOL.market, Kind.FUNDING): _rows([50, 99], "rate")})
    dm = DataManager(sources=[cache, api])

    prepared = dm.prepare(
        [SOL.market], [SOL.venue], [Kind.FUNDING], TimeRange(0, 100)
    )
    merged = prepared.tables[(SOL.venue, SOL.market, Kind.FUNDING)]
    # Cache served [0,51) (ts 0,50); API filled only the missing [51,100) (ts 99).
    assert merged.column("ts").to_pylist() == [0, 50, 99]
    # The API-fetched row was written through to the local cache tier.
    assert cache.fetch(SOL.venue, SOL.market, Kind.FUNDING, TimeRange(51, 100)).column(
        "ts"
    ).to_pylist() == [99]


def test_coverage_is_the_union_across_the_chain_without_fetching():
    cache = _loaded({(SOL.venue, SOL.market, Kind.CANDLES): _rows([0, 9])})
    api = _loaded({(SOL.venue, SOL.market, Kind.CANDLES): _rows([20, 29])})
    dm = DataManager(sources=[cache, api])
    cov = dm.coverage(SOL, Kind.CANDLES, TimeRange(0, 30))
    # [0,10) from cache (max 9 -> +1) unioned with [20,30) from api.
    assert cov.ranges == (TimeRange(0, 10), TimeRange(20, 30))


# --- funding hard gate (strict) --------------------------------------------


def test_strict_gate_rejects_with_available_range_and_fix():
    requested = TimeRange(_ms(2025, 1, 1), _ms(2025, 6, 1))
    # Funding only from March 1 (a row at end-1 so coverage reaches the end).
    cache = _loaded(
        {
            (SOL.venue, SOL.market, Kind.FUNDING): _rows(
                [_ms(2025, 3, 1), _ms(2025, 6, 1) - 1], "rate"
            )
        }
    )
    dm = DataManager(sources=[cache])
    with pytest.raises(FundingCoverageError) as err:
        dm.prepare([SOL.market], [SOL.venue], [Kind.FUNDING], requested)

    msg = str(err.value)
    assert "Available range for SOL-PERP@hyperliquid: 2025-03-01 → present." in msg
    assert "Re-run with --from 2025-03-01, or pass --clip-to-coverage." in msg
    # The structured gaps expose the exact missing window (Jan 1 -> Mar 1).
    gap = err.value.gaps[SOL]
    assert gap.bounds() == TimeRange(_ms(2025, 1, 1), _ms(2025, 3, 1))


def test_default_chain_stub_api_declares_nothing_and_gate_rejects():
    # Bare DataManager: empty local cache + stub API -> any funding request is a gap.
    dm = DataManager()
    with pytest.raises(FundingCoverageError):
        dm.prepare([SOL.market], [SOL.venue], [Kind.FUNDING], TimeRange(0, 100))


def test_fully_covered_funding_passes_the_gate():
    cache = _loaded({(SOL.venue, SOL.market, Kind.FUNDING): _rows([0, 99], "rate")})
    dm = DataManager(sources=[cache])
    prepared = dm.prepare([SOL.market], [SOL.venue], [Kind.FUNDING], TimeRange(0, 100))
    assert not prepared.clipped
    assert prepared.fidelity.all_full


# --- clip-to-coverage: whole-run multi-venue intersection ------------------


def test_clip_uses_the_intersection_of_every_legs_funding():
    requested = TimeRange(_ms(2025, 1, 1), _ms(2025, 8, 1))
    # SOL funding [Feb, Aug); BTC funding [Mar, Jul). Intersection: [Mar, Jul).
    cache = _loaded(
        {
            (SOL.venue, SOL.market, Kind.FUNDING): _rows(
                [_ms(2025, 2, 1), _ms(2025, 8, 1) - 1], "rate"
            ),
            (BTC.venue, BTC.market, Kind.FUNDING): _rows(
                [_ms(2025, 3, 1), _ms(2025, 7, 1) - 1], "rate"
            ),
        }
    )
    dm = DataManager(sources=[cache])

    # Strict rejects; clip runs the common window for BOTH legs (whole-run).
    with pytest.raises(FundingCoverageError):
        dm.prepare([SOL.market, BTC.market], [SOL.venue], [Kind.FUNDING], requested)

    prepared = dm.prepare(
        [SOL.market, BTC.market], [SOL.venue], [Kind.FUNDING], requested,
        mode=CoverageMode.CLIP_TO_COVERAGE,
    )
    assert prepared.clipped
    assert prepared.effective_range == TimeRange(_ms(2025, 3, 1), _ms(2025, 7, 1))


def test_clip_with_no_common_window_still_rejects():
    requested = TimeRange(0, 100)
    cache = _loaded(
        {
            (SOL.venue, SOL.market, Kind.FUNDING): _rows([0, 40], "rate"),  # [0,41)
            (BTC.venue, BTC.market, Kind.FUNDING): _rows([60, 99], "rate"),  # [60,100)
        }
    )
    dm = DataManager(sources=[cache])
    with pytest.raises(FundingCoverageError):
        dm.prepare(
            [SOL.market, BTC.market], [SOL.venue], [Kind.FUNDING], requested,
            mode=CoverageMode.CLIP_TO_COVERAGE,
        )


# --- signal-only lab venues: funding degrades, never gates (D28, §8.2) ------

BINANCE_SOL = Leg("binance", "SOL-PERP")


def test_signal_venue_funding_gap_degrades_instead_of_rejecting():
    # Executable HL funding is fully covered; the read-only lab venue (binance)
    # has a funding gap. The run must NOT reject — the lab gap degrades (§8.2).
    cache = _loaded(
        {
            (SOL.venue, SOL.market, Kind.FUNDING): _rows([0, 99], "rate"),  # full
            (BINANCE_SOL.venue, BINANCE_SOL.market, Kind.FUNDING): _rows(
                [0, 50], "rate"
            ),  # [0,51) — gap [51,100)
        }
    )
    dm = DataManager(sources=[cache])

    prepared = dm.prepare(
        [SOL.market], [SOL.venue], [Kind.FUNDING], TimeRange(0, 100),
        signal_venues=[BINANCE_SOL.venue],
    )

    assert not prepared.fidelity.all_full
    degraded = prepared.fidelity.degraded()
    assert len(degraded) == 1
    assert degraded[0].leg == BINANCE_SOL and degraded[0].kind is Kind.FUNDING
    assert "signal-only venue" in degraded[0].note and "§8.2" in degraded[0].note
    # The executable leg's funding is untouched — still full.
    assert all(e.full for e in prepared.fidelity.entries if e.leg == SOL)
    # The lab table carries exactly the rows that exist — nothing fabricated (D26).
    assert prepared.tables[
        (BINANCE_SOL.venue, BINANCE_SOL.market, Kind.FUNDING)
    ].column("ts").to_pylist() == [0, 50]


def test_signal_venue_fully_covered_is_full_fidelity():
    cache = _loaded(
        {
            (SOL.venue, SOL.market, Kind.FUNDING): _rows([0, 99], "rate"),
            (BINANCE_SOL.venue, BINANCE_SOL.market, Kind.FUNDING): _rows([0, 99], "rate"),
        }
    )
    dm = DataManager(sources=[cache])
    prepared = dm.prepare(
        [SOL.market], [SOL.venue], [Kind.FUNDING], TimeRange(0, 100),
        signal_venues=[BINANCE_SOL.venue],
    )
    assert prepared.fidelity.all_full
    assert (
        BINANCE_SOL.venue,
        BINANCE_SOL.market,
        Kind.FUNDING,
    ) in prepared.tables


def test_venue_in_both_sets_is_treated_as_executable_and_gates():
    # A venue named in both venues= and signal_venues= keeps the STRONGER
    # (executable) contract, so its funding gap still rejects.
    cache = _loaded(
        {(SOL.venue, SOL.market, Kind.FUNDING): _rows([0, 50], "rate")}  # gap [51,100)
    )
    dm = DataManager(sources=[cache])
    with pytest.raises(FundingCoverageError):
        dm.prepare(
            [SOL.market], [SOL.venue], [Kind.FUNDING], TimeRange(0, 100),
            signal_venues=[SOL.venue],
        )


def test_clip_ignores_signal_venue_coverage():
    # Clip is driven by the executable legs only: HL funding [Feb, Aug) clips to
    # [Feb, Aug) even though the signal venue's funding is the narrower [Mar, Jul).
    requested = TimeRange(_ms(2025, 1, 1), _ms(2025, 8, 1))
    cache = _loaded(
        {
            (SOL.venue, SOL.market, Kind.FUNDING): _rows(
                [_ms(2025, 2, 1), _ms(2025, 8, 1) - 1], "rate"
            ),
            (BINANCE_SOL.venue, BINANCE_SOL.market, Kind.FUNDING): _rows(
                [_ms(2025, 3, 1), _ms(2025, 7, 1) - 1], "rate"
            ),
        }
    )
    dm = DataManager(sources=[cache])
    prepared = dm.prepare(
        [SOL.market], [SOL.venue], [Kind.FUNDING], requested,
        mode=CoverageMode.CLIP_TO_COVERAGE,
        signal_venues=[BINANCE_SOL.venue],
    )
    # Clipped to the executable venue's window, not the narrower signal window.
    assert prepared.effective_range == TimeRange(_ms(2025, 2, 1), _ms(2025, 8, 1))
    degraded = prepared.fidelity.degraded()
    assert [e.leg for e in degraded] == [BINANCE_SOL]


# --- fidelity summary: depth degrades, it does not reject ------------------


def test_missing_depth_degrades_with_flag_but_run_proceeds():
    # Funding + candles fully covered; depth absent everywhere.
    cache = _loaded(
        {
            (SOL.venue, SOL.market, Kind.FUNDING): _rows([0, 99], "rate"),
            (SOL.venue, SOL.market, Kind.CANDLES): _rows([0, 99]),
        }
    )
    dm = DataManager(sources=[cache])
    prepared = dm.prepare(
        [SOL.market], [SOL.venue],
        [Kind.FUNDING, Kind.CANDLES, Kind.DEPTH], TimeRange(0, 100),
    )
    assert not prepared.fidelity.all_full
    degraded = prepared.fidelity.degraded()
    assert len(degraded) == 1 and degraded[0].kind is Kind.DEPTH
    assert "spread/impact model" in degraded[0].note
    assert any("DEGRADED" in line for line in prepared.fidelity.lines())
    # Depth table is empty (nothing fabricated), but the run still produced feeds.
    assert prepared.tables[(SOL.venue, SOL.market, Kind.DEPTH)].num_rows == 0
    assert prepared.tables[(SOL.venue, SOL.market, Kind.CANDLES)].num_rows == 2
