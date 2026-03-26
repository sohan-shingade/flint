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
