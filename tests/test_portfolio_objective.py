"""Tests for portfolio_objective — Phase 6 T6.3."""
from __future__ import annotations

import pytest

from flint.optimization.portfolio_objective import (
    pairwise_correlations,
    score,
)


class TestPairwiseCorrelations:
    def test_identical_curves_high_corr(self):
        c = [100 + i * 0.1 for i in range(100)]
        corr = pairwise_correlations([c, c])
        assert corr == pytest.approx(1.0, abs=1e-6)

    def test_anti_correlated_returns_negative_corr(self):
        """Per-bar returns must swing opposite to produce negative correlation.
        Monotonic price curves have monotonic returns → positive correlation
        even if one line goes up and the other down. Construct alternating
        up/down curves so the RETURNS are anti-correlated."""
        c1 = []
        c2 = []
        p1, p2 = 100.0, 100.0
        for i in range(50):
            step = 1.0 if i % 2 == 0 else -1.0
            p1 += step
            p2 -= step
            c1.append(p1)
            c2.append(p2)
        corr = pairwise_correlations([c1, c2])
        assert corr < -0.9

    def test_single_curve_returns_zero(self):
        corr = pairwise_correlations([[100, 101, 102]])
        assert corr == 0.0

    def test_empty_returns_zero(self):
        assert pairwise_correlations([]) == 0.0

    def test_flat_curve_skipped(self):
        """Zero-variance curve contributes no pair — average stays clean."""
        flat = [100] * 50
        moving = [100 + i * 0.5 for i in range(50)]
        corr = pairwise_correlations([flat, moving])
        # Only one comparable pair, but flat curve has zero std → skipped.
        # Implementation returns 0.0 when no valid pairs.
        assert corr == 0.0


class TestScore:
    def test_no_penalty_under_floor(self):
        """Low correlations (< floor) don't degrade Sharpe."""
        c1 = [100, 101, 100.5, 101.2, 100.8]
        c2 = [100, 100.3, 101, 100.1, 101.5]
        s = score(portfolio_sharpe=2.0, equity_curves=[c1, c2],
                  penalty_lambda=0.5, correlation_floor=0.8)
        # Even if corr > 0.3 but < 0.8, penalty=0
        assert s == pytest.approx(2.0, abs=1e-6)

    def test_high_correlation_degrades(self):
        """Highly correlated curves lose score proportional to excess."""
        c1 = [100 + i * 0.1 for i in range(50)]
        c2 = [100 + i * 0.1 for i in range(50)]  # identical → corr 1.0
        s = score(portfolio_sharpe=2.0, equity_curves=[c1, c2],
                  penalty_lambda=0.5, correlation_floor=0.3)
        # excess = 1.0 - 0.3 = 0.7 → penalty = 0.35 → 2.0 - 0.35 = 1.65
        assert s == pytest.approx(1.65, abs=1e-6)

    def test_lambda_zero_disables_penalty(self):
        c1 = [100 + i for i in range(50)]
        c2 = [100 + i for i in range(50)]
        s = score(portfolio_sharpe=2.0, equity_curves=[c1, c2],
                  penalty_lambda=0.0)
        assert s == pytest.approx(2.0, abs=1e-6)

    def test_negative_sharpe_penalized_further_by_correlation(self):
        c1 = [100 + i for i in range(50)]
        c2 = [100 + i for i in range(50)]
        s = score(portfolio_sharpe=-0.5, equity_curves=[c1, c2],
                  penalty_lambda=1.0, correlation_floor=0.0)
        # Correlation 1.0, no floor → penalty = 1.0 → -0.5 - 1.0 = -1.5
        assert s == pytest.approx(-1.5, abs=1e-6)
