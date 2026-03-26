"""Tests for ImpactStage — orderbook walk, sqrt model, bps fallback."""
from __future__ import annotations

import math

import pytest

from flint.execution.impact import ImpactStage, ImpactResult
from flint.models import (
    Candle, Order, OrderType, OrderbookLevel, OrderbookSnapshot, Side,
)


def _c(ts=1000, close=100.0, volume=1000.0, market="SOL-PERP") -> Candle:
    return Candle(ts=ts, open=close, high=close + 1, low=close - 1,
                  close=close, volume=volume, market=market, resolution_s=3600)


def _order(side=Side.LONG, size=10.0) -> Order:
    return Order(market="SOL-PERP", side=side, order_type=OrderType.MARKET,
                 size=size, order_id="o1")


def _book(bids=None, asks=None, market="SOL-PERP") -> OrderbookSnapshot:
    return OrderbookSnapshot(
        market=market, ts=1000,
        bids=tuple(bids or []),
        asks=tuple(asks or []),
    )


class TestOrderbookWalk:
    def test_single_level_full_fill(self):
        stage = ImpactStage()
        book = _book(asks=[OrderbookLevel(100.05, 20)])
        result = stage.compute(_order(Side.LONG, 10), _c(), book)
        assert result.tier == "orderbook"
        assert result.fill_price == pytest.approx(100.05)
        assert result.available_size == 10.0

    def test_multi_level_vwap(self):
        stage = ImpactStage()
        book = _book(asks=[OrderbookLevel(100.05, 5), OrderbookLevel(100.10, 8)])
        result = stage.compute(_order(Side.LONG, 10), _c(), book)
        expected_price = (5 * 100.05 + 5 * 100.10) / 10
        assert result.fill_price == pytest.approx(expected_price)
        assert result.available_size == 10.0
        assert result.tier == "orderbook"

    def test_partial_liquidity(self):
        stage = ImpactStage()
        book = _book(asks=[OrderbookLevel(100.05, 3)])
        result = stage.compute(_order(Side.LONG, 10), _c(), book)
        assert result.available_size == 3.0
        assert result.fill_price == pytest.approx(100.05)

    def test_sell_walks_bids(self):
        stage = ImpactStage()
        book = _book(bids=[OrderbookLevel(99.95, 20)])
        result = stage.compute(_order(Side.SHORT, 10), _c(), book)
        assert result.fill_price == pytest.approx(99.95)

    def test_empty_book_falls_through(self):
        stage = ImpactStage()
        book = _book(asks=[], bids=[])
        result = stage.compute(_order(Side.LONG, 10), _c(volume=1000), book)
        assert result.tier in ("sqrt", "fallback")

    def test_no_book_uses_sqrt(self):
        stage = ImpactStage()
        result = stage.compute(_order(Side.LONG, 10), _c(volume=1000), None)
        assert result.tier == "sqrt"


class TestSqrtModel:
    def test_impact_scales_sublinearly(self):
        stage = ImpactStage(impact_coefficient=0.1)
        small = stage.compute(_order(Side.LONG, 10), _c(volume=1000), None)
        large = stage.compute(_order(Side.LONG, 100), _c(volume=1000), None)
        assert large.impact_bps > small.impact_bps
        assert large.impact_bps < small.impact_bps * 10  # sub-linear

    def test_buy_pushes_price_up(self):
        stage = ImpactStage(impact_coefficient=0.1)
        result = stage.compute(_order(Side.LONG, 100), _c(close=100.0, volume=1000), None)
        assert result.fill_price > 100.0

    def test_sell_pushes_price_down(self):
        stage = ImpactStage(impact_coefficient=0.1)
        result = stage.compute(_order(Side.SHORT, 100), _c(close=100.0, volume=1000), None)
        assert result.fill_price < 100.0

    def test_custom_coefficient(self):
        low_k = ImpactStage(impact_coefficient=0.02)
        high_k = ImpactStage(impact_coefficient=0.1)
        r_low = low_k.compute(_order(Side.LONG, 100), _c(volume=1000), None)
        r_high = high_k.compute(_order(Side.LONG, 100), _c(volume=1000), None)
        assert r_high.impact_bps > r_low.impact_bps

    def test_available_size_equals_order_size(self):
        stage = ImpactStage()
        result = stage.compute(_order(Side.LONG, 50), _c(volume=1000), None)
        assert result.available_size == 50.0


class TestFallback:
    def test_zero_volume_uses_fallback(self):
        stage = ImpactStage(fallback_bps=10.0)
        result = stage.compute(_order(Side.LONG, 10), _c(volume=0), None)
        assert result.tier == "fallback"
        assert result.impact_bps == pytest.approx(10.0)

    def test_fallback_buy_price(self):
        stage = ImpactStage(fallback_bps=10.0)
        result = stage.compute(_order(Side.LONG, 10), _c(close=100.0, volume=0), None)
        assert result.fill_price == pytest.approx(100.1)

    def test_fallback_sell_price(self):
        stage = ImpactStage(fallback_bps=10.0)
        result = stage.compute(_order(Side.SHORT, 10), _c(close=100.0, volume=0), None)
        assert result.fill_price == pytest.approx(99.9)


class TestImpactResult:
    def test_impact_bps_from_orderbook(self):
        stage = ImpactStage()
        book = _book(asks=[OrderbookLevel(100.50, 20)])
        result = stage.compute(_order(Side.LONG, 10), _c(close=100.0), book)
        expected_bps = (100.50 - 100.0) / 100.0 * 10_000
        assert result.impact_bps == pytest.approx(expected_bps)
