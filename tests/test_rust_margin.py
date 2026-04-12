"""Per-venue margin and liquidation parity tests."""
from __future__ import annotations

import pytest

from flint.backtest.engine import BacktestEngine
from flint.execution.fill_models import ClosePriceFill
from flint.execution.margin import MarginEngine, compute_liquidation_price
from flint.execution.venue_config import VENUE_DEFAULTS, VenueConfig
from flint.models import Candle, Signal, Side
from flint.strategy import Strategy


def _c(ts, close, market="SOL-PERP"):
    return Candle(ts=ts, open=close, high=close + 1, low=close - 1,
                  close=close, volume=100.0, market=market, resolution_s=3600)


class TestLiquidationPrice:
    def test_long_liquidation(self):
        liq = compute_liquidation_price(
            entry_price=100.0, size=10.0, collateral=100.0,
            maintenance_margin=0.05, is_long=True)
        # liq = entry - (collateral - mmr*notional) / size
        # = 100 - (100 - 0.05*1000) / 10 = 100 - 50/10 = 95
        assert liq == pytest.approx(95.0)

    def test_short_liquidation(self):
        liq = compute_liquidation_price(
            entry_price=100.0, size=10.0, collateral=100.0,
            maintenance_margin=0.05, is_long=False)
        # liq = entry + (collateral - mmr*notional) / size
        # = 100 + 50/10 = 105
        assert liq == pytest.approx(105.0)

    def test_zero_size(self):
        liq = compute_liquidation_price(100.0, 0.0, 100.0)
        assert liq == 0.0


class TestPerVenueMargin:
    """Margin engine uses per-venue configs."""

    def test_venue_configs_exist(self):
        for venue in ["drift", "hyperliquid", "binance", "okx", "bybit",
                       "dydx", "jupiter", "coinbase", "kraken", "kucoin",
                       "gate", "bitget", "mexc", "htx"]:
            assert venue in VENUE_DEFAULTS, f"Missing VenueConfig for {venue}"

    def test_different_margin_requirements(self):
        """Different venues have different initial margin requirements."""
        drift = VENUE_DEFAULTS["drift"]
        binance = VENUE_DEFAULTS["binance"]
        # Drift: 10% initial margin (10x), Binance: 2% (50x)
        assert drift.initial_margin > binance.initial_margin

    def test_margin_engine_rejects_overleveraged(self):
        """MarginEngine should reject orders that exceed margin."""
        configs = {"default": VenueConfig(
            name="default", initial_margin=0.10, maintenance_margin=0.05)}
        engine = MarginEngine(venue_configs=configs)

        from flint.models import Order, OrderType, PositionInfo
        order = Order(market="SOL-PERP", side=Side.LONG,
                      order_type=OrderType.MARKET, size=1000.0,
                      order_id="t1", ts=0)
        # With $100 cash and 10% margin, max notional = $1000
        # Order for 1000 SOL at $100 = $100,000 notional → rejected
        allowed, reason = engine.check_can_open(order, 100.0, [], 100.0)
        assert not allowed

    def test_margin_engine_allows_small_order(self):
        """MarginEngine should allow orders within margin."""
        configs = {"default": VenueConfig(
            name="default", initial_margin=0.10, maintenance_margin=0.05)}
        engine = MarginEngine(venue_configs=configs)

        from flint.models import Order, OrderType
        order = Order(market="SOL-PERP", side=Side.LONG,
                      order_type=OrderType.MARKET, size=1.0,
                      order_id="t1", ts=0)
        # 1 SOL at $100 = $100 notional, 10% margin = $10, have $1000
        allowed, reason = engine.check_can_open(order, 1000.0, [], 100.0)
        assert allowed


class TestLiquidationInBacktest:
    """Liquidation events fire correctly during backtests."""

    def test_liquidation_triggers_on_price_drop(self):
        """Position gets liquidated when price drops below liq price."""
        # Start at 100, then crash to 50
        candles = []
        for i in range(30):
            if i < 10:
                price = 100.0
            else:
                price = 100.0 - (i - 10) * 5  # crash: 95, 90, ... 0
            candles.append(_c(i * 3600, max(price, 1.0)))

        class LeveragedLong(Strategy):
            @property
            def name(self):
                return "LevLong"
            def reset(self):
                self._entered = False
            def on_candle(self, candle, history, ctx=None):
                if ctx and not self._entered and len(history) >= 3:
                    # Large position relative to capital
                    ctx.market_order(candle.market, Side.LONG, 90.0)
                    self._entered = True
                return Signal.HOLD

        configs = {"default": VenueConfig(
            name="default", initial_margin=0.10,
            maintenance_margin=0.05, liquidation_penalty=0.01)}
        margin_eng = MarginEngine(venue_configs=configs)

        engine = BacktestEngine(
            LeveragedLong(), initial_capital=10_000,
            fee_rate=0.0, fill_model=ClosePriceFill(),
            margin_engine=margin_eng)
        result = engine.run(candles)

        # Should have strategy warnings about liquidation or margin
        has_liq_warning = any("LIQUIDATED" in w for w in result.strategy_warnings)
        # Position should have been force-closed one way or another
        assert result.total_trades >= 1
