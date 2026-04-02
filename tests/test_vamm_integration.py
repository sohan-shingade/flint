"""Tests for vAMM integration with ImpactStage and FillPipeline."""
import pytest
from flint.models import Candle, Order, OrderType, Side
from flint.execution.impact import ImpactStage
from flint.execution.vamm import VammCurve


def _make_order(market="SOL-PERP", side=Side.LONG, size=10.0):
    return Order(market=market, side=side, order_type=OrderType.MARKET,
                 size=size, order_id="test-1", ts=1000)


def _make_candle(market="SOL-PERP", close=150.0, volume=10000.0):
    return Candle(ts=1000, open=150.0, high=155.0, low=148.0, close=close,
                  volume=volume, market=market, resolution_s=60)


class TestImpactStageTier0:
    def test_vamm_used_when_configured(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        stage = ImpactStage(vamm_configs={"SOL-PERP": curve})
        result = stage.compute(_make_order(size=100.0), _make_candle(), book=None)
        assert result.tier == "vamm"
        assert result.fill_price > 150.0
        assert result.impact_bps > 0

    def test_fallback_when_no_vamm_for_market(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        stage = ImpactStage(vamm_configs={"SOL-PERP": curve})
        result = stage.compute(_make_order(market="BTC-PERP"), _make_candle(market="BTC-PERP", close=65000.0), book=None)
        assert result.tier == "sqrt"

    def test_no_vamm_configs_unchanged(self):
        stage = ImpactStage()
        result = stage.compute(_make_order(), _make_candle(), book=None)
        assert result.tier == "sqrt"

    def test_vamm_short(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        stage = ImpactStage(vamm_configs={"SOL-PERP": curve})
        result = stage.compute(_make_order(side=Side.SHORT, size=100.0), _make_candle(), book=None)
        assert result.tier == "vamm"
        assert result.fill_price < 150.0

    def test_vamm_recenters_at_candle_close(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=100.0)
        stage = ImpactStage(vamm_configs={"SOL-PERP": curve})
        result = stage.compute(_make_order(size=100.0), _make_candle(close=200.0), book=None)
        assert result.fill_price > 190.0


class TestFillPipelineVamm:
    def test_pipeline_with_vamm(self):
        from flint.execution.fill_models import FillPipeline
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        pipeline = FillPipeline(latency_enabled=False, vamm_configs={"SOL-PERP": curve})
        fill = pipeline.fill_market(_make_order(size=100.0), _make_candle())
        assert fill is not None
        assert fill.price > 150.0
        assert fill.impact_bps > 0

    def test_pipeline_without_vamm_unchanged(self):
        from flint.execution.fill_models import FillPipeline
        pipeline = FillPipeline(latency_enabled=False)
        fill = pipeline.fill_market(_make_order(size=10.0), _make_candle())
        assert fill is not None
