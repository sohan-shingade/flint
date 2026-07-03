"""Granularity tiers + funding-gate rate_type discrimination (§B7, §6.4).

Two things this file pins:

1. **The funding hard gate consults the FINAL/settled series only.** Predicted
   funding (the recorder's ctx fold, Tardis ``derivative_ticker``, a
   predicted-only write-through) is real, servable data — it drives the fidelity
   flag — but it must never make the gate think the run can settle money. A window
   covered by predicted-only funding is *rejected*.
2. **Granularity resolution** (auto picks the highest fully-covered tier; an
   explicit tier with a gap raises the structured rejection with the ways out).

All fixtures are hand-authored ts-keyed Arrow tables + asserted coverage ledgers
(unit inputs, never synthetic market data — D26). No source touches a network.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from flint.data import (
    DataManager,
    FundingCoverageError,
    GranularityUnavailableError,
    InMemoryCacheSource,
    Kind,
    Leg,
    RangeSet,
    TimeRange,
)
from flint.data.sources import DataSource
from flint.data.store import DurableCacheSource
from flint.data.store.coverage import FUNDING_PREDICTED_VARIANT

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
SOL = Leg(VENUE, MARKET)
FULL = TimeRange(0, 100)


def _funding_rows(ts: list[int], rate_type: str) -> pa.Table:
    n = len(ts)
    return pa.table(
        {
            "ts": ts,
            "rate_hourly": [0.001] * n,
            "rate_type": [rate_type] * n,
            "venue": [VENUE] * n,
            "market": [MARKET] * n,
        }
    )


def _candles(ts: list[int]) -> pa.Table:
    n = len(ts)
    return pa.table({"ts": ts, "close": [100.0] * n, "venue": [VENUE] * n})


# --- funding gate: rate_type discrimination (§6.4) --------------------------


def test_predicted_only_funding_is_rejected_by_the_hard_gate():
    # A market with ONLY predicted funding rows has no settled series, so nothing
    # can settle money over the window — the gate must reject (never silently pass
    # on the predicted rate path, which is what a tick strategy *sees*, not what
    # the account *pays*).
    cache = InMemoryCacheSource()
    cache.store(VENUE, MARKET, Kind.FUNDING, _funding_rows([0, 50, 99], "predicted"))
    dm = DataManager(sources=[cache])

    with pytest.raises(FundingCoverageError):
        dm.prepare([MARKET], [VENUE], [Kind.FUNDING], FULL)

    # The predicted series is still fully visible — it drives the fidelity flag,
    # not the gate. Both halves of the split are honest.
    assert dm.funding_coverage(SOL, FULL, rate_type="predicted").covers(FULL)
    assert dm.funding_coverage(SOL, FULL, rate_type="final").is_empty


def test_final_funding_passes_the_gate():
    cache = InMemoryCacheSource()
    cache.store(VENUE, MARKET, Kind.FUNDING, _funding_rows([0, 50, 99], "final"))
    dm = DataManager(sources=[cache])

    prepared = dm.prepare([MARKET], [VENUE], [Kind.FUNDING], FULL)
    assert not prepared.clipped
    assert prepared.fidelity.all_full


# --- the three predicted-coverage leak paths stay OUT of the gate -----------


def test_recorder_predicted_ledger_does_not_satisfy_the_gate(tmp_path):
    # Leak path 1: the recorder asserts predicted-funding coverage in the ledger
    # (predicted variant). available() serves those rows, but the gate reads the
    # FINAL series (variant "") — which the recorder never asserts — so it rejects.
    cache = DurableCacheSource(tmp_path)
    ledger = cache.coverage_ledger(VENUE, MARKET, Kind.FUNDING)
    ledger.assert_covered(FULL, "recorder", variant=FUNDING_PREDICTED_VARIANT)
    dm = DataManager(sources=[cache])

    assert dm.funding_coverage(SOL, FULL, rate_type="final").is_empty
    assert dm.funding_coverage(SOL, FULL, rate_type="predicted").covers(FULL)
    with pytest.raises(FundingCoverageError):
        dm.prepare([MARKET], [VENUE], [Kind.FUNDING], FULL)


def test_predicted_write_through_never_extends_final_coverage(tmp_path):
    # Leak path 2: a REST backfill asserts the final series over the first half;
    # then a predicted-only write-through (Tardis derivative_ticker landing in the
    # cache) covers the second half. The final series must stay [0,50) — the write
    # extends the predicted series only, never the gate-satisfying one.
    cache = DurableCacheSource(tmp_path)
    cache.coverage_ledger(VENUE, MARKET, Kind.FUNDING).assert_covered(
        TimeRange(0, 50), "hl_rest"
    )
    cache.store(VENUE, MARKET, Kind.FUNDING, _funding_rows([50, 60, 99], "predicted"))

    final = cache.available_funding(VENUE, MARKET, FULL, rate_type="final")
    predicted = cache.available_funding(VENUE, MARKET, FULL, rate_type="predicted")
    assert final.ranges == (TimeRange(0, 50),)
    assert not predicted.intersect(RangeSet((TimeRange(50, 100),))).is_empty


class _PredictedOnlySource(DataSource):
    """A Tardis-like source: FUNDING rows exist (``available`` covers), but they
    are the predicted series only — ``available_funding(final)`` is empty."""

    name = "tardis"

    def __init__(self, span: TimeRange) -> None:
        self._span = span

    def available(self, venue, market, kind, want):
        if kind is Kind.FUNDING:
            return RangeSet((self._span,)).intersect(RangeSet((want,)))
        return RangeSet()

    def fetch(self, venue, market, kind, span):
        return pa.table({})

    def available_funding(self, venue, market, want, *, rate_type="final"):
        if rate_type != "predicted":
            return RangeSet()
        return RangeSet((self._span,)).intersect(RangeSet((want,)))


def test_gate_consults_final_series_not_plain_availability():
    # Leak path 3: a vendor advertises FUNDING availability over the whole window,
    # but only the predicted series. Plain coverage() says "covered"; the gate must
    # consult the FINAL series — empty here — and reject regardless.
    dm = DataManager(sources=[_PredictedOnlySource(FULL)])
    assert dm.coverage(SOL, Kind.FUNDING, FULL).covers(FULL)
    with pytest.raises(FundingCoverageError):
        dm.prepare([MARKET], [VENUE], [Kind.FUNDING], FULL)


def test_pulling_trades_alone_does_not_trigger_the_funding_gate():
    # A request that does not consume FUNDING (warming trades) is a fetch, not a
    # run — nothing settles, so there is nothing to gate. It must not reject even
    # with no funding anywhere. (Deliberate D4 behavior change.)
    cache = InMemoryCacheSource()
    cache.store(VENUE, MARKET, Kind.TRADES, pa.table({"ts": [0, 50, 99]}))
    dm = DataManager(sources=[cache])
    prepared = dm.prepare([MARKET], [VENUE], [Kind.TRADES], FULL)
    assert prepared.effective_range == FULL


# --- tier resolution: auto + explicit rejection -----------------------------


def _two_tier_dm(tmp_path, *, trades_end: int) -> DataManager:
    """Candles + FINAL funding fully cover [0,100); trades cover [0, trades_end)."""
    inmem = InMemoryCacheSource()
    inmem.store(VENUE, MARKET, Kind.CANDLES, _candles([0, 50, 99]))
    inmem.store(VENUE, MARKET, Kind.FUNDING, _funding_rows([0, 50, 99], "final"))
    durable = DurableCacheSource(tmp_path)
    durable.coverage_ledger(VENUE, MARKET, Kind.TRADES).assert_covered(
        TimeRange(0, trades_end), "recorder"
    )
    return DataManager(sources=[inmem, durable])


def test_auto_resolves_to_the_highest_covered_tier(tmp_path):
    dm = _two_tier_dm(tmp_path, trades_end=100)  # trades fully covered
    prepared = dm.prepare([MARKET], [VENUE], [Kind.FUNDING], FULL, granularity="auto")
    # TRADES + FUNDING cover the range → ticks; QUOTES/BOOK_DELTA absent → not book.
    assert prepared.granularity == "ticks"


def test_auto_falls_back_to_candles_when_no_tick_data(tmp_path):
    dm = _two_tier_dm(tmp_path, trades_end=50)  # trades only half covered
    prepared = dm.prepare([MARKET], [VENUE], [Kind.FUNDING], FULL, granularity="auto")
    assert prepared.granularity == "candles"


def test_explicit_ticks_gap_raises_structured_rejection_with_options(tmp_path):
    dm = _two_tier_dm(tmp_path, trades_end=50)
    with pytest.raises(GranularityUnavailableError) as err:
        dm.prepare([MARKET], [VENUE], [Kind.FUNDING], FULL, granularity="ticks")

    exc = err.value
    assert exc.requested_tier == "ticks"
    # Per-leg per-kind coverage records the trades gap (covered only [0,50)).
    trades_cov = exc.coverage[SOL][Kind.TRADES]
    assert trades_cov.ranges == (TimeRange(0, 50),)

    actions = {o["action"]: o for o in exc.options}
    # run_bars is offered because the candles tier fully covers the range.
    assert actions["run_bars"]["granularity"] == "candles"
    # clip_to_coverage points at the largest fully-covered contiguous window.
    assert actions["clip_to_coverage"]["effective_range"] == {
        "start_ms": 0,
        "end_ms": 50,
    }
    # vendor_backfill is advertised even with no key wired (available: None).
    assert actions["vendor_backfill"]["requires_secret"] == "TARDIS_API_KEY"
    assert actions["vendor_backfill"]["available"] is None
    # record_forward carries a runnable CLI hint.
    assert "flint data record" in actions["record_forward"]["hint"]


def test_explicit_candles_never_granularity_rejects(tmp_path):
    # The candles floor keeps its established behavior — it never granularity-
    # rejects (candle/funding gaps stay their own gates' business).
    dm = _two_tier_dm(tmp_path, trades_end=50)
    prepared = dm.prepare(
        [MARKET], [VENUE], [Kind.FUNDING], FULL, granularity="candles"
    )
    assert prepared.granularity == "candles"


def test_unknown_granularity_is_a_value_error():
    dm = DataManager()
    with pytest.raises(ValueError, match="unknown granularity"):
        dm.prepare([MARKET], [VENUE], [Kind.FUNDING], FULL, granularity="nope")


# --- services.data_coverage: per-tier ladder + provenance detail (§B7) ------


def test_data_coverage_reports_per_tier_ladder_and_provenance(tmp_path):
    from flint.services.data import data_coverage

    dm = _two_tier_dm(tmp_path, trades_end=50)
    out = data_coverage(data=dm, market=MARKET, venue=VENUE, start_ms=0, end_ms=100)

    # One RangeSet per tier: candles fully covers; ticks only the trades half;
    # book needs quotes/book_delta (absent) so it is empty.
    assert out["tiers"]["candles"] == [{"start_ms": 0, "end_ms": 100}]
    assert out["tiers"]["ticks"] == [{"start_ms": 0, "end_ms": 50}]
    assert out["tiers"]["book"] == []
    # Provenance detail attributes the trades coverage to the recorder ledger.
    assert out["detail"]["trades"] == [
        {"start_ms": 0, "end_ms": 50, "source": "recorder"}
    ]
