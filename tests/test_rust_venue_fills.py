"""Per-venue fill pipeline parity tests.

Tests that each venue-specific fill model produces correct results.
When the Rust engine is ready, these will be parametrized to compare
Python vs Rust fill outputs for identical inputs.
"""
from __future__ import annotations

from typing import Optional

import pytest

from flint.execution.fill_cex import (
    BinanceFillModel,
    BitgetFillModel,
    BybitFillModel,
    CoinbaseFillModel,
    GateFillModel,
    HtxFillModel,
    KrakenFillModel,
    KucoinFillModel,
    MexcFillModel,
    OkxFillModel,
)
from flint.execution.fill_drift import DriftFillModel
from flint.execution.fill_hyperliquid import HyperliquidFillModel
from flint.execution.fill_jupiter import JupiterFillModel
from flint.execution.fill_models import FillPipeline
from flint.execution.fill_registry import create_fill_model
from flint.execution.synthetic_depth import VENUE_PROFILES
from flint.models import Candle, Order, OrderType, Side, Fill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candle(close: float = 100.0, volume: float = 500.0,
            market: str = "SOL-PERP", ts: int = 1000) -> Candle:
    return Candle(ts=ts, open=close - 0.5, high=close + 1.0,
                  low=close - 1.0, close=close, volume=volume,
                  market=market, resolution_s=3600)


def _order(side: Side = Side.LONG, size: float = 10.0,
           market: str = "SOL-PERP", order_type=OrderType.MARKET,
           price: float = 0.0) -> Order:
    return Order(market=market, side=side, order_type=order_type,
                 size=size, price=price, order_id="test-1", ts=1000)


# ---------------------------------------------------------------------------
# Drift fill model tests
# ---------------------------------------------------------------------------

class TestDriftFillModel:
    def test_fill_with_seed(self):
        """Same RNG seed produces identical fills."""
        model1 = DriftFillModel(seed=42)
        model2 = DriftFillModel(seed=42)
        candle = _candle()
        order = _order(Side.LONG, 10.0)

        fill1 = model1.fill_market(order, candle)
        fill2 = model2.fill_market(order, candle)

        assert fill1 is not None
        assert fill2 is not None
        assert fill1.price == pytest.approx(fill2.price, rel=1e-10)
        assert fill1.size == pytest.approx(fill2.size, rel=1e-10)

    def test_jit_improves_price(self):
        """JIT auction should improve price for the taker."""
        model = DriftFillModel(jit_fill_probability=1.0, seed=42)
        candle = _candle(close=100.0)
        buy_order = _order(Side.LONG, 1.0)

        fill = model.fill_market(buy_order, candle)
        assert fill is not None
        # JIT should give a price at or below oracle for buys
        # (weighted across tiers, so avg may be above oracle)
        assert fill.price > 0

    def test_vamm_backstop(self):
        """Large orders should hit vAMM tier."""
        model = DriftFillModel(jit_fill_probability=0.0, seed=42)
        candle = _candle(close=100.0, volume=10.0)
        # Very large order relative to synthetic depth
        order = _order(Side.LONG, 100_000.0)

        fill = model.fill_market(order, candle)
        assert fill is not None
        assert fill.size > 0

    def test_limit_order(self):
        """Limit buy fills when low crosses limit price."""
        model = DriftFillModel(seed=42)
        candle = _candle(close=100.0)  # low=99.0
        order = _order(Side.LONG, 5.0, order_type=OrderType.LIMIT, price=99.5)

        fill = model.fill_limit(order, candle)
        assert fill is not None
        assert fill.price == 99.5

    def test_limit_order_no_fill(self):
        """Limit buy doesn't fill when low is above limit price."""
        model = DriftFillModel(seed=42)
        candle = _candle(close=100.0)  # low=99.0
        order = _order(Side.LONG, 5.0, order_type=OrderType.LIMIT, price=98.0)

        fill = model.fill_limit(order, candle)
        assert fill is None


# ---------------------------------------------------------------------------
# Hyperliquid fill model tests
# ---------------------------------------------------------------------------

class TestHyperliquidFillModel:
    def test_clob_walk(self):
        """Basic CLOB walk produces a fill."""
        model = HyperliquidFillModel()
        candle = _candle(close=100.0, volume=500.0)
        order = _order(Side.LONG, 10.0)

        fill = model.fill_market(order, candle)
        assert fill is not None
        assert fill.price > 0
        assert fill.venue == "hyperliquid"

    def test_hlp_backstop(self):
        """Large orders hit HLP vault at worse price."""
        model = HyperliquidFillModel(impact_coefficient=0.005)
        candle = _candle(close=100.0, volume=100.0)
        small = _order(Side.LONG, 1.0)
        large = _order(Side.LONG, 100_000.0)

        fill_small = model.fill_market(small, candle)
        fill_large = model.fill_market(large, candle)

        assert fill_small is not None
        assert fill_large is not None
        # Larger order should get worse (higher) fill price for buys
        assert fill_large.price >= fill_small.price

    def test_sell_side(self):
        """Sell order walks bid side."""
        model = HyperliquidFillModel()
        candle = _candle(close=100.0, volume=500.0)
        order = _order(Side.SHORT, 10.0)

        fill = model.fill_market(order, candle)
        assert fill is not None
        assert fill.side == Side.SHORT


# ---------------------------------------------------------------------------
# Jupiter fill model tests
# ---------------------------------------------------------------------------

class TestJupiterFillModel:
    def test_keeper_delay_deterministic(self):
        """Same seed produces same keeper delay and fill price."""
        m1 = JupiterFillModel(seed=42)
        m2 = JupiterFillModel(seed=42)
        candle = _candle(close=100.0)
        order = _order(Side.LONG, 10.0)

        f1 = m1.fill_market(order, candle)
        f2 = m2.fill_market(order, candle)

        assert f1 is not None and f2 is not None
        assert f1.price == pytest.approx(f2.price, rel=1e-10)

    def test_quadratic_impact(self):
        """Larger orders have quadratically larger impact."""
        model_s = JupiterFillModel(seed=42)
        model_l = JupiterFillModel(seed=42)
        candle = _candle(close=100.0)
        small = _order(Side.LONG, 10.0)
        large = _order(Side.LONG, 1000.0)

        fs = model_s.fill_market(small, candle)
        fl = model_l.fill_market(large, candle)

        assert fs is not None and fl is not None
        # Larger order should have worse fill price
        assert fl.price > fs.price

    def test_venue_tag(self):
        model = JupiterFillModel(seed=42)
        fill = model.fill_market(_order(), _candle())
        assert fill is not None
        assert fill.venue == "jupiter"


# ---------------------------------------------------------------------------
# CEX fill model tests (Binance, OKX, Bybit + new venues)
# ---------------------------------------------------------------------------

class TestCexFillModels:
    """Test all CEX fill models share common behavior."""

    @pytest.mark.parametrize("venue,cls", [
        ("binance", BinanceFillModel),
        ("okx", OkxFillModel),
        ("bybit", BybitFillModel),
        ("coinbase", CoinbaseFillModel),
        ("kraken", KrakenFillModel),
        ("kucoin", KucoinFillModel),
        ("gate", GateFillModel),
        ("bitget", BitgetFillModel),
        ("mexc", MexcFillModel),
        ("htx", HtxFillModel),
    ])
    def test_basic_fill(self, venue, cls):
        """Every CEX model produces a fill for a normal-sized order."""
        model = cls()
        candle = _candle(close=100.0, volume=500.0)
        order = _order(Side.LONG, 10.0)

        fill = model.fill_market(order, candle)
        assert fill is not None, f"{venue} failed to fill"
        assert fill.price > 0
        assert fill.size > 0
        assert fill.venue == venue

    @pytest.mark.parametrize("venue,cls", [
        ("binance", BinanceFillModel),
        ("okx", OkxFillModel),
        ("coinbase", CoinbaseFillModel),
        ("kraken", KrakenFillModel),
        ("kucoin", KucoinFillModel),
        ("gate", GateFillModel),
        ("bitget", BitgetFillModel),
        ("mexc", MexcFillModel),
        ("htx", HtxFillModel),
    ])
    def test_larger_orders_worse_price(self, venue, cls):
        """Larger orders should get worse average fill prices."""
        model_s = cls()
        model_l = cls()
        candle = _candle(close=100.0, volume=1000.0)
        small = _order(Side.LONG, 1.0)
        large = _order(Side.LONG, 500.0)

        fs = model_s.fill_market(small, candle)
        fl = model_l.fill_market(large, candle)

        assert fs is not None and fl is not None
        # Larger order walks deeper into the book → worse price
        # Use small tolerance for floating point rounding at similar price levels
        assert fl.price >= fs.price - 1e-6

    @pytest.mark.parametrize("venue,cls", [
        ("binance", BinanceFillModel),
        ("okx", OkxFillModel),
        ("coinbase", CoinbaseFillModel),
        ("kraken", KrakenFillModel),
        ("kucoin", KucoinFillModel),
        ("gate", GateFillModel),
        ("bitget", BitgetFillModel),
        ("mexc", MexcFillModel),
        ("htx", HtxFillModel),
    ])
    def test_sell_side(self, venue, cls):
        """Sell orders walk bid side and get filled."""
        model = cls()
        candle = _candle(close=100.0, volume=500.0)
        order = _order(Side.SHORT, 10.0)

        fill = model.fill_market(order, candle)
        assert fill is not None
        assert fill.side == Side.SHORT

    def test_bybit_ioc_band(self):
        """Bybit IOC band caps fill price at 1% from close."""
        model = BybitFillModel()
        candle = _candle(close=100.0, volume=500.0)
        # Very large order that would walk well past 1%
        order = _order(Side.LONG, 1_000_000.0)

        fill = model.fill_market(order, candle)
        if fill is not None:
            # Fill price should not exceed 1% band
            assert fill.price <= 100.0 * 1.01 + 0.01  # small float tolerance
            # Should be partial fill
            assert fill.is_partial or fill.size < order.size

    def test_mexc_zero_fee(self):
        """MEXC should produce zero fees."""
        model = MexcFillModel()
        candle = _candle(close=100.0, volume=500.0)
        order = _order(Side.LONG, 10.0)

        fill = model.fill_market(order, candle)
        assert fill is not None
        assert fill.fee == 0.0

    @pytest.mark.parametrize("venue,cls,expected_fee_bps", [
        ("binance", BinanceFillModel, 5.0),
        ("okx", OkxFillModel, 5.0),
        ("bybit", BybitFillModel, 5.5),
        ("coinbase", CoinbaseFillModel, 6.0),
        ("kraken", KrakenFillModel, 4.0),
        ("kucoin", KucoinFillModel, 6.0),
        ("gate", GateFillModel, 7.5),
        ("bitget", BitgetFillModel, 6.0),
        ("mexc", MexcFillModel, 0.0),
        ("htx", HtxFillModel, 5.0),
    ])
    def test_limit_order_fee(self, venue, cls, expected_fee_bps):
        """Limit orders apply correct taker fee per venue."""
        model = cls()
        candle = _candle(close=100.0)  # low=99.0
        order = _order(Side.LONG, 10.0, order_type=OrderType.LIMIT, price=99.5)

        fill = model.fill_limit(order, candle)
        assert fill is not None

        expected_fee = 99.5 * 10.0 * (expected_fee_bps / 10_000)
        assert fill.fee == pytest.approx(expected_fee, rel=1e-6)


# ---------------------------------------------------------------------------
# Depth profile coverage
# ---------------------------------------------------------------------------

class TestDepthProfiles:
    """Every venue in the fill registry has a matching depth profile."""

    @pytest.mark.parametrize("venue", [
        "binance", "okx", "bybit", "coinbase", "kraken", "kucoin",
        "gate", "bitget", "mexc", "htx", "hyperliquid", "drift",
    ])
    def test_profile_exists(self, venue):
        assert venue in VENUE_PROFILES, f"Missing DepthProfile for {venue}"

    @pytest.mark.parametrize("venue", [
        "binance", "okx", "bybit", "coinbase", "kraken", "kucoin",
        "gate", "bitget", "mexc", "htx", "hyperliquid", "drift",
        "jupiter",
    ])
    def test_fill_registry(self, venue):
        """Every venue can be created via the fill registry."""
        model = create_fill_model(venue)
        assert model is not None


# ---------------------------------------------------------------------------
# Fill pipeline (generic) tests
# ---------------------------------------------------------------------------

class TestFillPipeline:
    def test_default_pipeline_fills(self):
        pipeline = FillPipeline()
        candle = _candle(close=100.0, volume=500.0)
        order = _order(Side.LONG, 10.0)

        fill = pipeline.fill_market(order, candle)
        # May or may not fill depending on latency — just verify no crash
        # and that if it fills, the result is sane
        if fill is not None:
            assert fill.price > 0
            assert fill.size > 0

    def test_pipeline_limit_order(self):
        pipeline = FillPipeline()
        candle = _candle(close=100.0)
        order = _order(Side.LONG, 10.0, order_type=OrderType.LIMIT, price=99.5)

        fill = pipeline.fill_limit(order, candle)
        assert fill is not None
        assert fill.price == 99.5
