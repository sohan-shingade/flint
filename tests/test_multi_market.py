"""Tests for multi-market backtesting."""
from __future__ import annotations
from typing import List, Optional, Dict

from flint.backtest.engine import BacktestEngine
from flint.execution.context import ExecutionContext
from flint.models import Candle, Signal, Side
from flint.strategy.base import Strategy


def _c(ts, close, market="SOL-PERP"):
    return Candle(ts=ts, open=close, high=close+1, low=close-1,
                  close=close, volume=100, market=market, resolution_s=3600)


class CrossMarketStrategy(Strategy):
    """Buy SOL when BTC is rising."""

    @property
    def name(self): return "CrossMarket"
    def reset(self): pass

    def on_candle(self, candle, history, ctx=None):
        if ctx is None or len(history) < 5:
            return Signal.HOLD
        btc = ctx.get_candles("BTC-PERP", 5)
        if len(btc) >= 2 and btc[-1].close > btc[-2].close:
            if not ctx.positions:
                return Signal.BUY
        elif ctx.positions:
            return Signal.SELL
        return Signal.HOLD


class TestMultiMarket:
    def test_dict_input(self):
        sol = [_c(i*3600, 100+i, "SOL-PERP") for i in range(50)]
        btc = [_c(i*3600, 40000+i*100, "BTC-PERP") for i in range(50)]
        engine = BacktestEngine(CrossMarketStrategy(), fee_rate=0.0)
        result = engine.run({"SOL-PERP": sol, "BTC-PERP": btc})
        assert len(result.equity_curve) > 0

    def test_extra_markets_param(self):
        sol = [_c(i*3600, 100+i, "SOL-PERP") for i in range(50)]
        btc = [_c(i*3600, 40000+i*100, "BTC-PERP") for i in range(50)]
        engine = BacktestEngine(CrossMarketStrategy(), fee_rate=0.0)
        result = engine.run(sol, extra_markets={"BTC-PERP": btc})
        assert len(result.equity_curve) == 50

    def test_get_candles_returns_data(self):
        sol = [_c(i*3600, 100+i, "SOL-PERP") for i in range(50)]
        btc = [_c(i*3600, 40000+i*100, "BTC-PERP") for i in range(50)]
        engine = BacktestEngine(CrossMarketStrategy(), fee_rate=0.0)
        result = engine.run(sol, extra_markets={"BTC-PERP": btc})
        # CrossMarketStrategy uses ctx.get_candles("BTC-PERP", 5)
        # and BTC is always rising, so it should generate trades
        assert result.total_trades > 0

    def test_markets_property(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(10000)
        assert ctx.markets == []
        ctx.set_market_histories({"SOL-PERP": [], "BTC-PERP": []})
        assert sorted(ctx.markets) == ["BTC-PERP", "SOL-PERP"]

    def test_single_market_still_works(self):
        from flint.strategy import MACrossoverStrategy
        candles = [_c(i*3600, 100 + (i%20 - 10)*0.5) for i in range(60)]
        engine = BacktestEngine(MACrossoverStrategy(5, 10), fee_rate=0.0)
        result = engine.run(candles)
        assert len(result.equity_curve) == 60
