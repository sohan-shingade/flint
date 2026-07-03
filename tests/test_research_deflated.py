"""Slice 6.2 — Deflated Sharpe Ratio, pinned against hand-computed values (D22, §19.3 item 5).

Worked out by hand from ``returns = [+0.10, -0.10, +0.10]`` (D26):

    mean = 0.0333333, m2 = 0.0088889, m3 = -0.0005926, m4 = 0.0001185
    skew = m3 / m2^1.5 = -0.7071068
    kurt = m4 / m2^2   =  1.5            (non-excess)
    observed sharpe (ddof=1) = 0.2886751

The expected-maximum-Sharpe and the CDF steps are re-derived inline from the same
Bailey & López de Prado (2014) formula using stdlib ``NormalDist`` — an independent
reference the module must match — and the multiple-testing property (more trials ->
lower DSR) is asserted directly, which is the whole point of the deflation (D22).
"""

from __future__ import annotations

import math
from statistics import NormalDist

import pytest

from flint.research import (
    ParamSpace,
    WalkForwardConfig,
    deflated_sharpe,
    expected_max_sharpe,
    kurtosis,
    probabilistic_sharpe,
    run_walk_forward,
    skewness,
    trial_sharpes,
)
from flint.research.deflated import EULER_MASCHERONI

RETURNS = [0.10, -0.10, 0.10]
# ten trials, mean 0.2, sample variance = (10 * 0.1^2)/9 = 0.0111111
TRIALS = [0.1, 0.3] * 5


# --- 1. moment estimators, pinned ---------------------------------------------


def test_skewness_and_kurtosis_pinned():
    assert skewness(RETURNS) == pytest.approx(-0.7071068, abs=1e-6)
    assert kurtosis(RETURNS) == pytest.approx(1.5, abs=1e-9)  # non-excess
    assert kurtosis(RETURNS, excess=True) == pytest.approx(-1.5, abs=1e-9)


def test_degenerate_series_return_normal_defaults():
    assert skewness([0.02, 0.02]) == 0.0
    assert kurtosis([0.02, 0.02]) == 3.0


# --- 2. expected maximum Sharpe (SR0) matches the reference formula ------------


def test_expected_max_sharpe_matches_the_reference_expression():
    n = len(TRIALS)
    var = sum((x - 0.2) ** 2 for x in TRIALS) / (n - 1)
    nd = NormalDist()
    ref = math.sqrt(var) * (
        (1 - EULER_MASCHERONI) * nd.inv_cdf(1 - 1 / n)
        + EULER_MASCHERONI * nd.inv_cdf(1 - 1 / (n * math.e))
    )
    assert expected_max_sharpe(TRIALS) == pytest.approx(ref, abs=1e-12)
    assert expected_max_sharpe(TRIALS) == pytest.approx(0.1659772, abs=1e-6)


def test_expected_max_sharpe_needs_at_least_two_trials():
    with pytest.raises(ValueError):
        expected_max_sharpe([0.2])


# --- 3. PSR analytic anchor: observed == benchmark -> exactly 0.5 --------------


def test_psr_is_one_half_when_observed_equals_benchmark():
    assert probabilistic_sharpe(0.5, 0.5, 50, 0.0, 3.0) == pytest.approx(0.5, abs=1e-12)


def test_psr_rejects_pathological_moments():
    # a hugely negative kurtosis drives the estimator variance non-positive.
    with pytest.raises(ValueError):
        probabilistic_sharpe(2.0, 0.0, 100, 0.0, -50.0)


# --- 4. full DSR vs an inline reference, and the multiple-testing property -----


def test_deflated_sharpe_matches_an_independent_reference():
    d = deflated_sharpe(RETURNS, TRIALS)
    # rebuild the DSR from the documented formula with stdlib only.
    n = len(TRIALS)
    var = sum((x - 0.2) ** 2 for x in TRIALS) / (n - 1)
    nd = NormalDist()
    sr0 = math.sqrt(var) * (
        (1 - EULER_MASCHERONI) * nd.inv_cdf(1 - 1 / n)
        + EULER_MASCHERONI * nd.inv_cdf(1 - 1 / (n * math.e))
    )
    sr = 0.2886751
    skew, kurt, m = -0.7071068, 1.5, len(RETURNS)
    radicand = 1 - skew * sr + ((kurt - 1) / 4) * sr**2
    z = (sr - sr0) * math.sqrt(m - 1) / math.sqrt(radicand)
    assert d.dsr == pytest.approx(nd.cdf(z), abs=1e-6)
    assert d.observed_sharpe == pytest.approx(sr, abs=1e-6)
    assert d.expected_max_sharpe == pytest.approx(sr0, abs=1e-9)
    assert d.n_trials == 10 and d.n_returns == 3
    assert 0.0 <= d.dsr <= 1.0


def test_more_trials_deflate_the_same_sharpe_toward_and_below_a_half():
    # identical returns and identical trial-Sharpe variance; only the trial COUNT
    # grows. The same observed Sharpe must earn a strictly lower DSR (D22: 1.8 in
    # 10,000 trials is weaker evidence than 1.8 in 10).
    d10 = deflated_sharpe(RETURNS, TRIALS)  # 10 trials
    d1000 = deflated_sharpe(RETURNS, TRIALS * 100)  # 1000 trials, same variance
    assert d1000.expected_max_sharpe > d10.expected_max_sharpe
    assert d1000.dsr < d10.dsr
    assert d10.n_trials == 10 and d1000.n_trials == 1000


# --- 5. wiring 6.1 -> 6.2: trial sharpes pooled from a walk-forward result -----


def test_trial_sharpes_pool_every_windows_trials_from_a_walk_forward_result():
    def runner(params, start, end):
        return -((params["x"] - 3.0) ** 2)

    cfg = WalkForwardConfig(n_windows=2, n_trials=8, purge_bars=1, label_horizon_bars=1)
    result = run_walk_forward(30, [ParamSpace("x", 0.0, 10.0)], runner, cfg)
    pooled = trial_sharpes(result)
    assert len(pooled) == result.total_trials == 16
    # a DSR can be computed straight off the walk-forward output (the 6.1 -> 6.2 seam).
    d = deflated_sharpe(RETURNS, pooled)
    assert 0.0 <= d.dsr <= 1.0
    assert d.n_trials == 16
