"""Tests for the composable fill pipeline."""
from __future__ import annotations

import pytest

from flint.models import (
    Candle, Fill, Order, OrderType, Side, TimeInForce,
)


def _c(ts: int, close: float, high: float = 0, low: float = 0,
       open_: float = 0, volume: float = 100.0, market: str = "SOL-PERP") -> Candle:
    h = high or close + 1
    l = low or close - 1
    o = open_ or close
    return Candle(ts=ts, open=o, high=h, low=l, close=close,
                  volume=volume, market=market, resolution_s=3600)


class TestTimeInForce:
    def test_enum_values(self):
        assert TimeInForce.IOC.value == "ioc"
        assert TimeInForce.FOK.value == "fok"
        assert TimeInForce.GTC.value == "gtc"

    def test_order_default_tif_is_ioc(self):
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET, size=10)
        assert order.time_in_force == TimeInForce.IOC

    def test_order_accepts_tif(self):
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=10, time_in_force=TimeInForce.GTC)
        assert order.time_in_force == TimeInForce.GTC

    def test_fill_has_impact_fields(self):
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=100.0, size=10,
                    fee=0.05, ts=1000, is_partial=True, latency_ms=8000, impact_bps=15.0)
        assert fill.is_partial is True
        assert fill.latency_ms == 8000
        assert fill.impact_bps == 15.0

    def test_fill_defaults_backward_compat(self):
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=100.0, size=10,
                    fee=0.05, ts=1000)
        assert fill.is_partial is False
        assert fill.latency_ms == 0.0
        assert fill.impact_bps == 0.0


from flint.execution.fill_models import FillModel, FillPipeline, SlippageFill
from flint.execution.impact import ImpactStage
from flint.execution.latency import LatencyStage
from flint.execution.partial_fill import PartialFillStage
from flint.models import OrderbookLevel, OrderbookSnapshot, TimeInForce


class TestFillPipelineIsAFillModel:
    def test_subclass(self):
        assert issubclass(FillPipeline, FillModel)

    def test_default_construction(self):
        pipeline = FillPipeline()
        assert pipeline is not None


class TestPipelineMarketFill:
    def test_fill_with_orderbook(self):
        pipeline = FillPipeline(latency_enabled=False)
        book = OrderbookSnapshot(
            market="SOL-PERP", ts=1000,
            bids=(), asks=(OrderbookLevel(100.5, 20),),
        )
        pipeline.set_orderbook(book)
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=10, order_id="o1", ts=1000)
        candle = _c(1000, 100.0)
        fill = pipeline.fill_market(order, candle)
        assert fill is not None
        assert fill.price == pytest.approx(100.5)
        assert fill.impact_bps > 0

    def test_fill_without_orderbook_uses_sqrt(self):
        pipeline = FillPipeline(impact_coefficient=0.1, latency_enabled=False)
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=10, order_id="o1", ts=1000)
        candle = _c(1000, 100.0, volume=1000)
        fill = pipeline.fill_market(order, candle)
        assert fill is not None
        assert fill.price > 100.0

    def test_fill_no_volume_uses_fallback(self):
        pipeline = FillPipeline(fallback_bps=10.0, latency_enabled=False)
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=10, order_id="o1", ts=1000)
        candle = _c(1000, 100.0, volume=0)
        fill = pipeline.fill_market(order, candle)
        assert fill is not None
        assert fill.price == pytest.approx(100.1)

    def test_fok_rejects_partial(self):
        pipeline = FillPipeline(latency_enabled=False)
        book = OrderbookSnapshot(
            market="SOL-PERP", ts=1000,
            bids=(), asks=(OrderbookLevel(100.5, 3),),
        )
        pipeline.set_orderbook(book)
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=10, order_id="o1", ts=1000, time_in_force=TimeInForce.FOK)
        candle = _c(1000, 100.0)
        fill = pipeline.fill_market(order, candle)
        assert fill is None

    def test_ioc_partial_fill(self):
        pipeline = FillPipeline(latency_enabled=False)
        book = OrderbookSnapshot(
            market="SOL-PERP", ts=1000,
            bids=(), asks=(OrderbookLevel(100.5, 3),),
        )
        pipeline.set_orderbook(book)
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=10, order_id="o1", ts=1000, time_in_force=TimeInForce.IOC)
        candle = _c(1000, 100.0)
        fill = pipeline.fill_market(order, candle)
        assert fill is not None
        assert fill.size == 3.0
        assert fill.is_partial is True


class TestPipelineLimitFill:
    def test_limit_buy_triggers(self):
        pipeline = FillPipeline()
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.LIMIT,
                      size=10, price=99.0, order_id="o1")
        candle = _c(1000, 100.0, low=98.0)
        fill = pipeline.fill_limit(order, candle)
        assert fill is not None
        assert fill.price == 99.0

    def test_limit_buy_no_trigger(self):
        pipeline = FillPipeline()
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.LIMIT,
                      size=10, price=99.0, order_id="o1")
        candle = _c(1000, 100.0, low=99.5)
        fill = pipeline.fill_limit(order, candle)
        assert fill is None


class TestPipelineLatency:
    def test_latency_delays_fill(self):
        pipeline = FillPipeline(base_latency_s=10.0, latency_jitter_s=0.0)
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=10, order_id="o1", ts=1000)
        candle_early = _c(1005, 100.0, volume=1000)
        fill_early = pipeline.fill_market(order, candle_early)
        assert fill_early is None

        candle_ready = _c(1010, 101.0, volume=1000)
        fill_ready = pipeline.fill_market(order, candle_ready)
        assert fill_ready is not None

    def test_latency_disabled(self):
        pipeline = FillPipeline(latency_enabled=False)
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=10, order_id="o1", ts=1000)
        candle = _c(1000, 100.0, volume=1000)
        fill = pipeline.fill_market(order, candle)
        assert fill is not None


class TestPipelineGTCResting:
    def test_gtc_returns_resting_orders(self):
        pipeline = FillPipeline(latency_enabled=False)
        book = OrderbookSnapshot(
            market="SOL-PERP", ts=1000,
            bids=(), asks=(OrderbookLevel(100.5, 3),),
        )
        pipeline.set_orderbook(book)
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=10, order_id="o1", ts=1000, time_in_force=TimeInForce.GTC)
        candle = _c(1000, 100.0)
        fill = pipeline.fill_market(order, candle)
        assert fill is not None
        assert fill.size == 3.0
        assert len(pipeline.pending_resting_orders) == 1
        resting = pipeline.pending_resting_orders[0]
        assert resting.size == 7.0
        assert resting.order_type == OrderType.LIMIT
