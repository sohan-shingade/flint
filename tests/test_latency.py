"""Tests for LatencyStage — venue-specific execution delays with seeded jitter."""
from __future__ import annotations


from flint.execution.latency import LatencyStage
from flint.models import Order, OrderType, Side


def _order(ts=1000, market="SOL-PERP") -> Order:
    return Order(market=market, side=Side.LONG, order_type=OrderType.MARKET,
                 size=10, order_id="o1", ts=ts)


class TestLatencyDelay:
    def test_drift_base_delay(self):
        stage = LatencyStage(base_latency_s=8.0, latency_jitter_s=0.0)
        order = _order(ts=1000)
        eligible_ts = stage.compute_eligible_ts(order)
        assert eligible_ts == 1008

    def test_zero_latency(self):
        stage = LatencyStage(base_latency_s=0.0, latency_jitter_s=0.0)
        order = _order(ts=1000)
        assert stage.compute_eligible_ts(order) == 1000

    def test_jitter_within_bounds(self):
        stage = LatencyStage(base_latency_s=8.0, latency_jitter_s=5.0, seed=42)
        results = [stage.compute_eligible_ts(_order(ts=1000)) for _ in range(100)]
        for r in results:
            assert 1003 <= r <= 1013  # 1000 + 8 +/- 5

    def test_seeded_jitter_is_deterministic(self):
        results_a = []
        results_b = []
        for _ in range(10):
            stage_a = LatencyStage(base_latency_s=8.0, latency_jitter_s=5.0, seed=42)
            stage_b = LatencyStage(base_latency_s=8.0, latency_jitter_s=5.0, seed=42)
            results_a.append(stage_a.compute_eligible_ts(_order(ts=1000)))
            results_b.append(stage_b.compute_eligible_ts(_order(ts=1000)))
        assert results_a == results_b

    def test_default_seed_is_deterministic(self):
        """Phase 1 T1.1.e — omitting seed must produce deterministic output.
        Two LatencyStage instances with no seed must compute identical jitter."""
        stage_a = LatencyStage(base_latency_s=8.0, latency_jitter_s=5.0)
        stage_b = LatencyStage(base_latency_s=8.0, latency_jitter_s=5.0)
        for _ in range(10):
            a = stage_a.compute_eligible_ts(_order(ts=1000))
            b = stage_b.compute_eligible_ts(_order(ts=1000))
            assert a == b, f"Default seed not deterministic: {a} vs {b}"

    def test_explicit_unseeded_varies(self):
        """seed=-1 opts into non-deterministic system-time seeding."""
        stage = LatencyStage(base_latency_s=8.0, latency_jitter_s=5.0, seed=-1)
        results = {stage.compute_eligible_ts(_order(ts=1000)) for _ in range(50)}
        assert len(results) > 1


class TestLatencyOrderQueue:
    def test_order_not_eligible_before_delay(self):
        stage = LatencyStage(base_latency_s=10.0, latency_jitter_s=0.0)
        order = _order(ts=1000)
        eligible_ts = stage.compute_eligible_ts(order)
        assert not stage.is_eligible(eligible_ts, current_ts=1005)

    def test_order_eligible_after_delay(self):
        stage = LatencyStage(base_latency_s=10.0, latency_jitter_s=0.0)
        order = _order(ts=1000)
        eligible_ts = stage.compute_eligible_ts(order)
        assert stage.is_eligible(eligible_ts, current_ts=1010)

    def test_order_eligible_exactly_at_delay(self):
        stage = LatencyStage(base_latency_s=10.0, latency_jitter_s=0.0)
        order = _order(ts=1000)
        eligible_ts = stage.compute_eligible_ts(order)
        assert stage.is_eligible(eligible_ts, current_ts=1010)


class TestLatencyDisabled:
    def test_disabled_means_immediate(self):
        stage = LatencyStage(enabled=False)
        order = _order(ts=1000)
        eligible_ts = stage.compute_eligible_ts(order)
        assert eligible_ts == 1000
