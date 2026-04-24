"""Tests for PortfolioRiskEngine — Phase 6 T6.2."""
from __future__ import annotations

import pytest

from flint.risk.portfolio import (
    OrderLite,
    PortfolioRiskEngine,
    PositionLite,
    RiskLimits,
)


def _order(market="SOL-PERP", venue="drift", side="long", size=10.0, price=150.0):
    return OrderLite(market=market, venue=venue, side=side,
                     size=size, price=price)


def _pos(market, venue, size, price=150.0):
    return PositionLite(market=market, venue=venue, size=size,
                        entry_price=price)


def _loose_concentration(**overrides) -> RiskLimits:
    """RiskLimits with per-venue / per-market caps disabled so isolated
    tests only exercise the limit they care about."""
    base = dict(max_per_venue_pct=1.0, max_per_market_pct=1.0)
    base.update(overrides)
    return RiskLimits(**base)


class TestGrossExposure:
    def test_passes_under_cap(self):
        engine = PortfolioRiskEngine(_loose_concentration(max_gross_exposure=2.0))
        r = engine.check_order(_order(size=10), equity=10_000.0, positions=[])
        assert r.approved, r.reason

    def test_rejects_over_cap(self):
        engine = PortfolioRiskEngine(_loose_concentration(max_gross_exposure=0.5))
        # size=10 * price=150 = $1500, equity $1000 → 1.5x, cap 0.5x
        r = engine.check_order(_order(size=10), equity=1_000.0, positions=[])
        assert not r.approved
        assert "gross exposure" in r.reason


class TestNetExposure:
    def test_hedged_book_passes(self):
        """Long $1500 + existing short $1000 → net $500 / equity $1000 = 0.5x."""
        engine = PortfolioRiskEngine(_loose_concentration(
            max_gross_exposure=10.0, max_net_exposure=1.0,
        ))
        existing = [_pos("BTC-PERP", "drift", size=-6.67, price=150.0)]  # ~$1000 short
        r = engine.check_order(
            _order(market="SOL-PERP", size=10, price=150),
            equity=1_000.0, positions=existing,
        )
        assert r.approved, r.reason

    def test_directional_rejects(self):
        """Big long with no offsetting short: net = gross > cap."""
        engine = PortfolioRiskEngine(_loose_concentration(
            max_gross_exposure=10.0, max_net_exposure=0.5,
        ))
        r = engine.check_order(_order(size=10), equity=1_000.0, positions=[])
        assert not r.approved
        assert "net exposure" in r.reason


class TestConcentration:
    def test_single_venue_rejected(self):
        """Existing SOL-PERP on drift + new BTC-PERP on drift = 100% drift,
        cap 70%."""
        engine = PortfolioRiskEngine(RiskLimits(
            max_gross_exposure=100, max_net_exposure=100,
            max_per_venue_pct=0.70,
        ))
        existing = [_pos("SOL-PERP", "drift", size=5, price=150)]
        r = engine.check_order(
            _order(market="BTC-PERP", venue="drift", size=10, price=150),
            equity=10_000, positions=existing,
        )
        assert not r.approved
        assert "drift" in r.reason
        assert "venue" in r.reason.lower()

    def test_split_venue_passes(self):
        engine = PortfolioRiskEngine(RiskLimits(
            max_gross_exposure=100, max_net_exposure=100,
            max_per_venue_pct=0.70,
            max_per_market_pct=1.0,  # don't gate on market for this test
        ))
        existing = [_pos("SOL-PERP", "drift", size=5, price=150)]
        r = engine.check_order(
            _order(market="BTC-PERP", venue="hyperliquid", size=10, price=150),
            equity=10_000, positions=existing,
        )
        assert r.approved, r.reason

    def test_single_market_rejected(self):
        engine = PortfolioRiskEngine(RiskLimits(
            max_gross_exposure=100, max_net_exposure=100,
            max_per_venue_pct=1.0, max_per_market_pct=0.40,
        ))
        existing = [_pos("SOL-PERP", "drift", size=5, price=150)]
        r = engine.check_order(
            _order(market="SOL-PERP", venue="hyperliquid", size=10, price=150),
            equity=10_000, positions=existing,
        )
        assert not r.approved
        assert "SOL-PERP" in r.reason


class TestCorrelationClusters:
    def test_correlated_pair_rejected(self):
        """BTC and ETH correlated 0.9 → combined holding counted as one
        cluster; cluster cap 70%, actual 100% → reject."""
        corr = {("BTC-PERP", "ETH-PERP"): 0.9}
        engine = PortfolioRiskEngine(
            RiskLimits(
                max_gross_exposure=100, max_net_exposure=100,
                max_per_venue_pct=1.0, max_per_market_pct=1.0,
                max_correlation_cluster_pct=0.70,
            ),
            correlation_matrix=corr,
        )
        existing = [_pos("BTC-PERP", "drift", size=5, price=150)]
        r = engine.check_order(
            _order(market="ETH-PERP", size=5, price=150),
            equity=10_000, positions=existing,
        )
        assert not r.approved
        assert "cluster" in r.reason.lower()

    def test_uncorrelated_pair_passes(self):
        corr = {("BTC-PERP", "ETH-PERP"): 0.2}
        engine = PortfolioRiskEngine(
            RiskLimits(
                max_gross_exposure=100, max_net_exposure=100,
                max_per_venue_pct=1.0, max_per_market_pct=1.0,
                max_correlation_cluster_pct=0.70,
            ),
            correlation_matrix=corr,
        )
        existing = [_pos("BTC-PERP", "drift", size=5, price=150)]
        r = engine.check_order(
            _order(market="ETH-PERP", size=5, price=150),
            equity=10_000, positions=existing,
        )
        assert r.approved


class TestKillSwitch:
    def test_peak_tracking(self):
        engine = PortfolioRiskEngine(RiskLimits(max_drawdown_pct=0.25))
        assert not engine.check_kill_switch(10_000)
        assert not engine.check_kill_switch(12_000)  # new peak
        # 12k peak → 9k = 25% drawdown → trigger
        assert engine.check_kill_switch(9_000)

    def test_no_kill_below_threshold(self):
        engine = PortfolioRiskEngine(RiskLimits(max_drawdown_pct=0.25))
        engine.check_kill_switch(10_000)
        # 10k → 8k = 20% drawdown, cap 25% → no trigger
        assert not engine.check_kill_switch(8_000)


class TestVaR:
    def test_var_computed_after_sufficient_returns(self):
        engine = PortfolioRiskEngine()
        for r in [0.01, -0.02, 0.005, -0.015, 0.008, -0.03, 0.012,
                  -0.005, 0.02, -0.01, 0.015, -0.025]:
            engine.record_return(r)
        v = engine.var()
        # Worst 5% and 1% of 12 samples → var_95 close to -min, var_99 = -min
        assert v.var_95 > 0
        assert v.var_99 >= v.var_95

    def test_var_gate_blocks_order(self):
        engine = PortfolioRiskEngine(_loose_concentration(var_limit_95_1d=1.0))
        # Record highly negative returns so projected 95% VaR * equity > $1.
        for r in [-0.1, -0.08, -0.12, -0.05, -0.09, -0.11,
                  -0.07, -0.13, -0.06, -0.10, -0.08, -0.09]:
            engine.record_return(r)
        r = engine.check_order(
            _order(size=1, price=150), equity=10_000, positions=[],
        )
        assert not r.approved
        assert "VaR" in r.reason
