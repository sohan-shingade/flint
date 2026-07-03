"""Slice 6.2 — §11.1 metric definitions, pinned against hand-computed values (§19.3 item 5).

Every number here is worked out by hand from a tiny hand-authored equity curve (D26):

    equity = [100, 110, 99, 108.9]  ->  returns = [+0.10, -0.10, +0.10]
    mean   = 0.10/3 = 0.0333333
    std    (ddof=1) = sqrt((0.066667^2 + 0.133333^2 + 0.066667^2)/2) = 0.1154701
    sharpe = 0.0333333 / 0.1154701          = 0.2886751
    downside dev (vs 0) = sqrt((0.10^2)/3)  = 0.0577350
    sortino = 0.0333333 / 0.0577350         = 0.5773503
    max drawdown = (110 - 99)/110           = 0.10
"""

from __future__ import annotations

import math

import pytest

from flint.engine.portfolio import FILL
from flint.engine.portfolio.events import Event
from flint.research import (
    MetricSummary,
    annualization_factor,
    bar_returns,
    bars_per_year,
    build_report,
    downside_deviation,
    max_drawdown,
    sharpe,
    sortino,
    summarize_equity,
    win_rate_from_events,
)

EQUITY = [100.0, 110.0, 99.0, 108.9]


# --- 1. primitives, pinned ----------------------------------------------------


def test_bar_returns_are_arithmetic_and_skip_nonpositive_prior():
    assert bar_returns(EQUITY) == pytest.approx([0.10, -0.10, 0.10])
    # a non-positive prior equity contributes no return for that step.
    assert bar_returns([0.0, 5.0, 6.0]) == pytest.approx([0.2])
    assert bar_returns([100.0]) == []


def test_sharpe_pinned():
    assert sharpe(bar_returns(EQUITY)) == pytest.approx(0.2886751, abs=1e-6)


def test_sharpe_is_zero_when_undefined():
    assert sharpe([0.05]) == 0.0  # < 2 returns
    assert sharpe([0.02, 0.02, 0.02]) == 0.0  # zero dispersion


def test_sortino_pinned():
    assert downside_deviation(bar_returns(EQUITY)) == pytest.approx(0.0577350, abs=1e-6)
    assert sortino(bar_returns(EQUITY)) == pytest.approx(0.5773503, abs=1e-6)


def test_sortino_all_upside_is_positive_infinity():
    assert sortino([0.01, 0.02, 0.03]) == math.inf  # no downside, positive mean
    assert sortino([0.0, 0.0, 0.0]) == 0.0  # no downside and no excess -> 0.0, not inf


def test_max_drawdown_pinned():
    assert max_drawdown(EQUITY) == pytest.approx(0.10, abs=1e-9)
    assert max_drawdown([100.0, 101.0, 102.0]) == 0.0  # monotonic up -> no drawdown


def test_annualization_is_365_day_root_bars_per_year():
    assert bars_per_year(86_400) == pytest.approx(365.0)  # daily bars
    assert bars_per_year(60) == pytest.approx(525_600.0)  # 1-minute bars
    assert annualization_factor(86_400) == pytest.approx(math.sqrt(365.0))
    with pytest.raises(ValueError):
        bars_per_year(0)


# --- 2. summary carries the factor and the effective range --------------------


def test_summary_annualizes_and_states_the_evaluated_range():
    s = summarize_equity(EQUITY, resolution_s=86_400, evaluated_start_ts=1000, evaluated_end_ts=4000)
    assert isinstance(s, MetricSummary)
    factor = math.sqrt(365.0)
    assert s.annualization_factor == pytest.approx(factor)
    assert s.annualized_sharpe == pytest.approx(0.2886751 * factor, abs=1e-5)
    assert s.annualized_sortino == pytest.approx(0.5773503 * factor, abs=1e-5)
    # the effective evaluated range rides along with the numbers (§11.1).
    assert (s.evaluated_start_ts, s.evaluated_end_ts) == (1000, 4000)


def test_summary_keeps_infinite_sortino_unscaled():
    s = summarize_equity([100.0, 101.0, 102.0], resolution_s=86_400)
    assert s.sortino == math.inf
    assert s.annualized_sortino == math.inf  # not multiplied into nan


# --- 3. win rate from the event log -------------------------------------------


def _fill(pnl: float, ts: int) -> Event:
    return Event(
        FILL,
        {"realized_pnl": pnl, "fee": 0.1, "price": 100.0, "size": 1.0, "fidelity_tier": "C"},
        ts=ts,
    )


def test_win_rate_counts_only_decided_closing_trades():
    events = [
        _fill(0.0, 1),  # an opening fill (no realized pnl) — not a decided trade
        _fill(5.0, 2),  # win
        _fill(-3.0, 3),  # loss
        _fill(2.0, 4),  # win
    ]
    assert win_rate_from_events(events) == pytest.approx(2 / 3)
    assert win_rate_from_events([_fill(0.0, 1)]) is None  # no decided trades


# --- 4. the trust report always shows raw Sharpe + DSR + trial count ----------


def test_report_without_optimization_reports_dsr_as_unavailable_not_omitted():
    report = build_report(EQUITY, resolution_s=86_400, evaluated_start_ts=1000, evaluated_end_ts=4000)
    text = report.describe()
    assert "Sharpe (raw): 0.2887" in text
    assert "Deflated Sharpe: n/a" in text  # honest, not hidden
    assert "trials: 0" in text
    assert "evaluated range [1000, 4000]" in text
    assert report.n_trials == 0


def test_report_folds_in_the_engine_cost_decomposition_and_win_rate():
    events = [_fill(5.0, 2), _fill(-3.0, 3)]
    report = build_report(EQUITY, resolution_s=86_400, events=events)
    assert report.cost is not None
    assert report.win_rate == pytest.approx(0.5)
    # range defaults from the cost decomposition when not passed explicitly.
    assert report.metrics.evaluated_start_ts == 2
    assert report.metrics.evaluated_end_ts == 3
    assert "cost: net" in report.describe()
