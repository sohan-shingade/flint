"""Tests for PartialFillStage — IOC, FOK, GTC time-in-force semantics."""
from __future__ import annotations

import pytest

from flint.execution.impact import ImpactResult
from flint.execution.partial_fill import PartialFillStage, FillDecision
from flint.models import Order, OrderType, Side, TimeInForce


def _order(size=10.0, tif=TimeInForce.IOC) -> Order:
    return Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                 size=size, order_id="o1", time_in_force=tif)


def _impact(fill_price=100.5, available_size=10.0, impact_bps=5.0, tier="orderbook") -> ImpactResult:
    return ImpactResult(fill_price=fill_price, available_size=available_size,
                        impact_bps=impact_bps, tier=tier)


class TestIOC:
    def test_full_fill(self):
        stage = PartialFillStage()
        order = _order(size=10, tif=TimeInForce.IOC)
        impact = _impact(available_size=10)
        decision = stage.decide(order, impact)
        assert decision.fill_size == 10.0
        assert decision.is_partial is False
        assert decision.resting_order is None

    def test_partial_fill(self):
        stage = PartialFillStage()
        order = _order(size=10, tif=TimeInForce.IOC)
        impact = _impact(available_size=3)
        decision = stage.decide(order, impact)
        assert decision.fill_size == 3.0
        assert decision.is_partial is True
        assert decision.resting_order is None

    def test_zero_liquidity_no_fill(self):
        stage = PartialFillStage()
        order = _order(size=10, tif=TimeInForce.IOC)
        impact = _impact(available_size=0)
        decision = stage.decide(order, impact)
        assert decision.fill_size == 0.0


class TestFOK:
    def test_full_fill(self):
        stage = PartialFillStage()
        order = _order(size=10, tif=TimeInForce.FOK)
        impact = _impact(available_size=10)
        decision = stage.decide(order, impact)
        assert decision.fill_size == 10.0
        assert decision.is_partial is False

    def test_insufficient_liquidity_cancels(self):
        stage = PartialFillStage()
        order = _order(size=10, tif=TimeInForce.FOK)
        impact = _impact(available_size=9)
        decision = stage.decide(order, impact)
        assert decision.fill_size == 0.0
        assert decision.cancelled is True

    def test_exact_fill(self):
        stage = PartialFillStage()
        order = _order(size=10, tif=TimeInForce.FOK)
        impact = _impact(available_size=10)
        decision = stage.decide(order, impact)
        assert decision.fill_size == 10.0


class TestGTC:
    def test_full_fill_no_resting(self):
        stage = PartialFillStage()
        order = _order(size=10, tif=TimeInForce.GTC)
        impact = _impact(fill_price=100.5, available_size=10)
        decision = stage.decide(order, impact)
        assert decision.fill_size == 10.0
        assert decision.resting_order is None

    def test_partial_fill_creates_resting(self):
        stage = PartialFillStage()
        order = _order(size=10, tif=TimeInForce.GTC)
        impact = _impact(fill_price=100.5, available_size=3)
        decision = stage.decide(order, impact)
        assert decision.fill_size == 3.0
        assert decision.is_partial is True
        assert decision.resting_order is not None
        assert decision.resting_order.size == 7.0
        assert decision.resting_order.price == 100.5
        assert decision.resting_order.order_type == OrderType.LIMIT

    def test_zero_liquidity_all_resting(self):
        stage = PartialFillStage()
        order = _order(size=10, tif=TimeInForce.GTC)
        impact = _impact(fill_price=100.5, available_size=0)
        decision = stage.decide(order, impact)
        assert decision.fill_size == 0.0
        assert decision.resting_order is not None
        assert decision.resting_order.size == 10.0
