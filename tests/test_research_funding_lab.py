"""Slice 6.5 — cross-venue funding & basis lab (§10, D28) + the HL-native wedge demo.

Two halves:

1. **Lab analytics** — normalization/annualization is linear (settlements/day × 365, never
   compounded); the cross-venue benchmark and per-venue dislocation align observations
   **as-of** (no look-ahead, §8.2); basis comes from the recorded ``MarkSnapshot``.
2. **Acceptance (the wedge, §19.8)** — an HL-native funding-harvest backtest over
   hand-authored fixtures where funding **dominates** the cost attribution, proven with
   ``build_report``'s cost decomposition and the per-bar EQUITY curve (slice 7.3 fold).

D26: every rate, mark, and candle here is hand-authored — a flat price path and fixed
funding schedule are deliberate unit inputs, not generated market-like data.
"""

from __future__ import annotations

import pytest

from flint.adapters import InMemoryUserData
from flint.core.models import Candle, FundingRate, MarkSnapshot, Signal
from flint.engine import EngineConfig, build_tearsheet
from flint.engine.api import EngineFeed, EngineRunSpec
from flint.engine.portfolio import EventLog
from flint.engine.select import engine_for
from flint.ports import TenantContext
from flint.venues import HYPERLIQUID
from flint.research import (
    HOURS_PER_YEAR_365,
    annualize_hourly,
    annualized_rate,
    basis_series,
    build_report,
    cross_venue_dislocation,
    equity_series_from_events,
    mean_basis_bps,
    settlements_per_day,
    settlements_per_year,
    venue_funding_stats,
)

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
HOUR_S = 3600
HOUR_MS = 3600 * 1000
T0 = 1_700_000_000_000


def _fr(ts, rate, venue, *, interval_s=HOUR_S, basis="oracle", rate_type="predicted"):
    return FundingRate(
        market=MARKET,
        ts=ts,
        rate_hourly=rate,
        interval_s=interval_s,
        price_basis=basis,
        rate_type=rate_type,
        venue=venue,
    )


# --- annualization: linear, settlements/day × 365 (§10) ----------------------


def test_hours_per_year_is_365_day():
    assert HOURS_PER_YEAR_365 == 24 * 365 == 8760


def test_settlements_framing_matches_hourly_framing_hl_hourly():
    # HL settles hourly. 0.01%/h → 0.01% × 24 × 365 = 87.6% annualized, LINEAR.
    hl = _fr(T0, 0.0001, VENUE, interval_s=HOUR_S)
    assert annualize_hourly(hl.rate_hourly) == pytest.approx(0.876)
    assert annualized_rate(hl) == pytest.approx(0.876)  # settlements framing agrees
    assert settlements_per_day(HOUR_S) == 24
    assert settlements_per_year(HOUR_S) == 8760


def test_settlements_framing_matches_hourly_framing_binance_8h():
    # Binance settles every 8h; a native 0.01%/8h is rate_hourly = 0.01%/8 = 0.00125%/h.
    bi = _fr(T0, 0.0001 / 8, "binance", interval_s=8 * HOUR_S, basis="mark")
    assert settlements_per_day(8 * HOUR_S) == 3
    assert settlements_per_year(8 * HOUR_S) == 1095
    # Both framings annualize to the same linear number — the point of normalization.
    assert annualize_hourly(bi.rate_hourly) == pytest.approx(annualized_rate(bi))
    assert annualized_rate(bi) == pytest.approx(0.0001 / 8 * 8760)


def test_annualization_is_linear_not_compounded():
    # A compounded 0.01%/h over 8760h would be (1.0001**8760 - 1) ≈ 1.40 (140%);
    # the linear number is 0.876. We must be linear (§10).
    linear = annualize_hourly(0.0001)
    compounded = (1.0001**8760) - 1
    assert linear == pytest.approx(0.876)
    assert compounded > 1.3  # sanity: they are materially different
    assert linear != pytest.approx(compounded, rel=0.1)


# --- cross-venue benchmark + per-venue dislocation (§10) ---------------------


def test_cross_venue_dislocation_splits_around_benchmark():
    # HL richer than Binance at the same instant → symmetric dislocation about the mean.
    rates = [_fr(T0, 0.0001, VENUE), _fr(T0, 0.00002, "binance")]
    series = cross_venue_dislocation(rates)
    assert series.venues == ("binance", "hyperliquid")
    p = series.points[0]
    assert p.benchmark_hourly == pytest.approx(0.00006)
    assert p.dislocations[VENUE] == pytest.approx(0.00004)
    assert p.dislocations["binance"] == pytest.approx(-0.00004)
    ts, venue, d = series.widest()
    assert abs(d) == pytest.approx(0.00004)


def test_dislocation_alignment_is_as_of_no_lookahead():
    # Binance's only obs is LATE (T0+2h). At T0 and T0+1h it has no as-of rate, so it is
    # absent from the benchmark — its future rate must NOT leak backward (§8.2/D26).
    rates = [
        _fr(T0, 0.0001, VENUE),
        _fr(T0 + HOUR_MS, 0.0001, VENUE),
        _fr(T0 + 2 * HOUR_MS, 0.0001, VENUE),
        _fr(T0 + 2 * HOUR_MS, 0.00002, "binance"),
    ]
    series = cross_venue_dislocation(rates)
    early = series.points[0]  # T0
    assert "binance" not in early.venue_rates  # not yet observed → excluded, not zeroed
    assert early.benchmark_hourly == pytest.approx(0.0001)  # HL alone
    assert early.dislocations[VENUE] == pytest.approx(0.0)  # single venue → no gap
    late = series.points[-1]  # T0+2h, both present
    assert set(late.venue_rates) == {VENUE, "binance"}
    assert late.benchmark_hourly == pytest.approx(0.00006)


def test_dislocation_carries_last_rate_forward_asof():
    # HL publishes once at T0; Binance publishes later. At the Binance timestamp HL's
    # as-of rate is still its last published one (carried forward is legal, not lookahead).
    rates = [_fr(T0, 0.0001, VENUE), _fr(T0 + 3 * HOUR_MS, 0.00002, "binance")]
    series = cross_venue_dislocation(rates)
    last = series.points[-1]
    assert last.venue_rates[VENUE] == pytest.approx(0.0001)  # carried from T0
    assert last.venue_rates["binance"] == pytest.approx(0.00002)


def test_predicted_only_default_excludes_final_rows():
    # The lab surfaces the strategy-visible (predicted) rate by default (§6.4); a stray
    # 'final' row must not enter the benchmark.
    rates = [
        _fr(T0, 0.0001, VENUE, rate_type="predicted"),
        _fr(T0, 0.5, VENUE, rate_type="final"),  # absurd final rate — must be ignored
    ]
    series = cross_venue_dislocation(rates)
    assert series.points[0].venue_rates[VENUE] == pytest.approx(0.0001)


def test_mixed_markets_rejected():
    a = _fr(T0, 0.0001, VENUE)
    b = FundingRate(market="BTC-PERP", ts=T0, rate_hourly=0.0001, interval_s=HOUR_S,
                    price_basis="oracle", rate_type="predicted", venue=VENUE)
    with pytest.raises(ValueError):
        cross_venue_dislocation([a, b])


def test_venue_funding_stats_reports_per_venue_annualized_carry():
    rates = [
        _fr(T0, 0.0001, VENUE),
        _fr(T0 + HOUR_MS, 0.0002, VENUE),
        _fr(T0, 0.00002, "binance", interval_s=8 * HOUR_S, basis="mark"),
    ]
    stats = {s.venue: s for s in venue_funding_stats(rates)}
    assert stats[VENUE].n == 2
    assert stats[VENUE].mean_hourly == pytest.approx(0.00015)
    assert stats[VENUE].annualized == pytest.approx(0.00015 * 8760)
    assert stats["binance"].settlements_per_year == 1095


# --- basis tracking (perp mark vs index, §10) --------------------------------


def _mark(ts, mark, index, venue=VENUE):
    return MarkSnapshot(market=MARKET, ts=ts, mark_price=mark, index_price=index,
                        venue=venue)


def test_basis_series_uses_model_definition_and_orders_by_ts():
    marks = [_mark(T0 + HOUR_MS, 100.5, 100.0), _mark(T0, 101.0, 100.0)]
    pts = basis_series(marks)
    assert [p.ts for p in pts] == [T0, T0 + HOUR_MS]  # sorted
    assert pts[0].basis_bps == pytest.approx(100.0)  # (101-100)/100*1e4
    assert pts[1].basis_bps == pytest.approx(50.0)
    assert mean_basis_bps(marks) == pytest.approx(75.0)


def test_basis_skips_undefined_index_never_fabricates():
    # A non-positive index has undefined basis — the row is skipped, not zeroed (D26).
    pts = basis_series([_mark(T0, 100.0, 0.0), _mark(T0 + HOUR_MS, 100.5, 100.0)])
    assert len(pts) == 1
    assert pts[0].ts == T0 + HOUR_MS


# --- ACCEPTANCE: HL funding-harvest → funding-dominated cost attribution (§19.8) ---

N_BARS = 8
# 1%/h predicted & final, well under HL's 4%/h cap (§6.4). Sized so the funding
# harvested over the window genuinely dominates the venue's one-time entry market
# impact — under the legacy engine's impact-free NaiveFillModel even 0.1%/h cleared
# a near-zero entry cost, but the Nautilus bar lane prices a realistic ClobFill
# entry (~$307 slippage on the $10k short), so the thesis "funding-dominated, curve
# rises" is only true once the funding line clears that fixed cost.
HARVEST_RATE = 0.01


def _candles() -> list[Candle]:
    # Flat price: trading PnL is ~0 by construction, so any net PnL is funding carry.
    return [
        Candle(ts=T0 + i * HOUR_MS, open=100.0, high=100.0, low=100.0, close=100.0,
               volume=1000.0, market=MARKET, resolution_s=HOUR_S, venue=VENUE)
        for i in range(N_BARS)
    ]


class _FundingHarvester:
    """The engine-seam twin of strategy/templates/perp.FundingHarvestStrategy (which uses
    the strategy-surface on_candle(candle, history, ctx) wired by Phase 7): short once the
    PREDICTED funding rate is positive (shorts receive when longs pay), then hold to
    accrue. Records every rate it was shown to prove it only ever saw predicted (§6.4)."""

    def __init__(self, size_usd: float, threshold: float = 1e-5) -> None:
        self._size = size_usd
        self._threshold = threshold
        self._entered = False
        self.rates_seen: list[float] = []

    def on_candle(self, candle, ctx):
        fr = ctx.funding_rate(candle.market, candle.venue)
        if fr is not None:
            self.rates_seen.append(fr.rate_hourly)
        if not self._entered and fr is not None and fr.rate_hourly > self._threshold:
            self._entered = True
            return [Signal.short(candle.market, candle.venue, size_usd=self._size)]
        return []


def _run_harvest():
    pytest.importorskip("nautilus_trader")
    candles = _candles()
    # Predicted rate visible every bar; final rate settles once per bar (in-bar ts).
    predicted = [_fr(c.ts, HARVEST_RATE, VENUE, rate_type="predicted") for c in candles]
    final = [_fr(c.ts, HARVEST_RATE, VENUE, rate_type="final") for c in candles]
    marks = [_mark(c.ts, 100.0, 100.0) for c in candles]  # oracle price for settlement

    log = EventLog(InMemoryUserData(), TenantContext.local(), run_id="harvest")
    strat = _FundingHarvester(size_usd=10_000.0)
    spec = EngineRunSpec(
        config=EngineConfig(),
        venue_spec=HYPERLIQUID,
        initial_capital="100000",
        fund_venue=VENUE,
        mark_policy="close_derived",
    )
    engine_for("nautilus")().run(
        EngineFeed(
            candles=candles,
            marks={MARKET: marks},
            funding={MARKET: predicted + final},
        ),
        strat,
        event_log=log,
        spec=spec,
    )
    return log, strat


def test_funding_harvest_cost_attribution_is_funding_dominated():
    log, strat = _run_harvest()
    events = log.read()
    report = build_report(
        equity_series_from_events(events), resolution_s=HOUR_S, events=events
    )
    cost = report.cost

    # Funding actually settled, and the short received it (positive).
    assert cost.funding_settlements >= 1
    assert float(cost.funding) > 0

    lines = {
        "funding": abs(float(cost.funding)),
        "trading": abs(float(cost.trading_pnl)),
        "fees": abs(float(cost.fees)),
        "slippage": abs(float(cost.slippage_cost)),
    }
    # The wedge claim: funding is the dominant cost line, not trading PnL or fees.
    assert lines["funding"] == max(lines.values())
    assert lines["funding"] > lines["trading"]
    assert lines["funding"] > lines["fees"]
    # And the tearsheet actually surfaces the funding line to the user.
    assert "funding" in report.describe()


def test_funding_harvest_equity_curve_rises_from_the_7_3_fold():
    log, _ = _run_harvest()
    events = log.read()
    equity = equity_series_from_events(events)
    # One EQUITY snapshot per bar (slice 7.3), folded in order.
    assert len(equity) == N_BARS
    # Flat price + positive funding receipts → the curve ends above where it started.
    assert equity[-1] > equity[0]


def test_harvester_only_ever_saw_the_predicted_rate():
    # §6.4 predicted/final contract at the lab boundary: the strategy never sees 'final'.
    _, strat = _run_harvest()
    assert strat.rates_seen
    assert all(r == pytest.approx(HARVEST_RATE) for r in strat.rates_seen)


def test_cost_attribution_matches_build_tearsheet_directly():
    # build_report folds the same engine Tearsheet — cross-check the funding line.
    log, _ = _run_harvest()
    events = log.read()
    direct = build_tearsheet(events)
    via_report = build_report(
        equity_series_from_events(events), resolution_s=HOUR_S, events=events
    ).cost
    assert float(via_report.funding) == pytest.approx(float(direct.funding))
