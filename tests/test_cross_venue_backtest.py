"""Tests for cross-venue backtest support."""
import pytest
from flint.models import Candle, FundingRate, Side, Signal
from flint.backtest.engine import BacktestEngine, _parse_venue_market
from flint.strategy.base import Strategy


class SimpleArbStrategy(Strategy):
    """Test strategy that places orders on specific venues."""
    @property
    def name(self):
        return "test_arb"

    def reset(self):
        self._entered = False

    def on_candle(self, candle, history, ctx=None):
        if ctx is None or self._entered:
            return Signal.HOLD
        if len(history) >= 3:
            ctx.market_order(candle.market, Side.LONG, 1.0, venue="drift")
            ctx.market_order(candle.market, Side.SHORT, 1.0, venue="hyperliquid")
            self._entered = True
        return Signal.HOLD


class TestParseVenueMarket:
    def test_with_venue_prefix(self):
        venue, market = _parse_venue_market("drift:SOL-PERP")
        assert venue == "drift"
        assert market == "SOL-PERP"

    def test_without_prefix(self):
        venue, market = _parse_venue_market("SOL-PERP")
        assert venue == "default"
        assert market == "SOL-PERP"

    def test_hyperliquid_prefix(self):
        venue, market = _parse_venue_market("hyperliquid:BTC-PERP")
        assert venue == "hyperliquid"
        assert market == "BTC-PERP"


class TestCrossVenueBacktest:
    def _make_candles(self, market, venue, count=10, base_price=150.0):
        return [
            Candle(ts=1000 + i * 60, open=base_price + i, high=base_price + i + 1,
                   low=base_price + i - 1, close=base_price + i + 0.5,
                   volume=100.0, market=market, resolution_s=60, venue=venue)
            for i in range(count)
        ]

    def test_venue_market_keys_accepted(self):
        strategy = SimpleArbStrategy()
        engine = BacktestEngine(strategy=strategy, initial_capital=10000.0)
        candles = {
            "drift:SOL-PERP": self._make_candles("SOL-PERP", "drift"),
            "hyperliquid:SOL-PERP": self._make_candles("SOL-PERP", "hyperliquid"),
        }
        result = engine.run(candles)
        assert result.total_trades >= 0

    def test_backward_compatible_plain_keys(self):
        strategy = SimpleArbStrategy()
        engine = BacktestEngine(strategy=strategy, initial_capital=10000.0)
        candles = {"SOL-PERP": self._make_candles("SOL-PERP", "default")}
        result = engine.run(candles)
        assert result.total_trades >= 0

    def test_per_venue_pnl_in_result(self):
        strategy = SimpleArbStrategy()
        engine = BacktestEngine(strategy=strategy, initial_capital=10000.0)
        candles = {
            "drift:SOL-PERP": self._make_candles("SOL-PERP", "drift"),
            "hyperliquid:SOL-PERP": self._make_candles("SOL-PERP", "hyperliquid"),
        }
        result = engine.run(candles)
        assert hasattr(result, 'per_venue_pnl')
        assert isinstance(result.per_venue_pnl, dict)
