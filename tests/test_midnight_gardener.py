"""Tests for The Midnight Gardener strategy."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "strategies", "user"))

_strategy_path = os.path.join(os.path.dirname(__file__), "..", "strategies", "user", "midnight_gardener.py")
if not os.path.exists(_strategy_path):
    pytest.skip("midnight_gardener.py not found (user strategy, gitignored)", allow_module_level=True)

from midnight_gardener import MidnightGardener
from flint.models import Candle, FundingRate
from flint.backtest.engine import BacktestEngine
from flint.execution.fill_models import ClosePriceFill, FillPipeline, SlippageFill
from flint.execution.fee_models import ZeroFeeModel
from flint.execution.margin import MarginEngine


def _candles(start_ts, count, base_price=100.0, trend=0.0, volatility=2.0):
    """Generate synthetic hourly candles with optional trend and volatility."""
    import math
    candles = []
    price = base_price
    for i in range(count):
        # Sine wave volatility + linear trend
        noise = volatility * math.sin(i * 0.3) + volatility * 0.5 * math.sin(i * 0.7)
        price = base_price + trend * i + noise
        candles.append(Candle(
            ts=start_ts + i * 3600,
            open=price - 0.5,
            high=price + 1.0,
            low=price - 1.0,
            close=price,
            volume=10000.0,
            market="SOL-PERP",
            resolution_s=3600,
            venue="hyperliquid",
        ))
    return candles


def _btc_candles(start_ts, count, base_price=90000.0, trend=0.0):
    """Generate BTC candles correlated with SOL."""
    import math
    candles = []
    for i in range(count):
        noise = 500 * math.sin(i * 0.3)
        price = base_price + trend * i + noise
        candles.append(Candle(
            ts=start_ts + i * 3600,
            open=price - 50,
            high=price + 100,
            low=price - 100,
            close=price,
            volume=500.0,
            market="BTC-PERP",
            resolution_s=3600,
            venue="hyperliquid",
        ))
    return candles


class TestMidnightGardenerUnit:
    """Unit tests for strategy logic."""

    def test_init_defaults(self):
        s = MidnightGardener()
        assert s.name == "MidnightGardener"
        assert s.rsi_period == 10
        assert s.bear_fade_lo == 54.0
        assert s.bear_fade_hi == 66.0

    def test_reset_clears_state(self):
        s = MidnightGardener()
        s._residuals = [1.0, 2.0]
        s._entry_price = 100.0
        s._bars_since = 0
        s.reset()
        assert s._residuals == []
        assert s._entry_price == 0.0
        assert s._bars_since == 999

    def test_parameters_returns_valid_ranges(self):
        params = MidnightGardener.parameters()
        assert "rsi_period" in params
        assert "bear_fade_lo" in params
        assert "stop_atr_mult" in params
        for name, spec in params.items():
            assert "type" in spec
            assert "low" in spec
            assert "high" in spec
            assert "default" in spec
            assert spec["low"] <= spec["default"] <= spec["high"]

    def test_warmup_returns_hold(self):
        """Strategy should return HOLD during warmup period."""
        s = MidnightGardener()
        sol = _candles(1000000, 50)  # fewer than warmup bars
        btc = _btc_candles(1000000, 50)

        engine = BacktestEngine(s, 10_000.0, 0.0005)
        result = engine.run({"SOL-PERP": sol, "BTC-PERP": btc})
        assert result.total_trades == 0  # not enough bars to trade

    def test_no_longs_in_bear_regime(self):
        """In a downtrend, strategy should not go long."""
        s = MidnightGardener()
        # Strong downtrend: fast EMA will be below slow EMA
        sol = _candles(1000000, 500, base_price=200.0, trend=-0.5, volatility=3.0)
        btc = _btc_candles(1000000, 500, trend=-100)

        engine = BacktestEngine(s, 10_000.0, 0.0005)
        result = engine.run({"SOL-PERP": sol, "BTC-PERP": btc})

        # Check that no long trades were taken
        # (strategy filters longs when bearish)
        if hasattr(result, 'trades') and result.trades:
            long_trades = [t for t in result.trades if t.get("side") == "LONG"]
            assert len(long_trades) == 0


class TestMidnightGardenerBacktest:
    """Integration tests with backtest engine."""

    def test_runs_without_error(self):
        """Strategy completes a full backtest without exceptions."""
        s = MidnightGardener()
        sol = _candles(1000000, 300, volatility=5.0)
        btc = _btc_candles(1000000, 300)

        engine = BacktestEngine(s, 10_000.0, 0.0005)
        result = engine.run({"SOL-PERP": sol, "BTC-PERP": btc})
        assert result is not None
        assert result.total_pnl is not None
        assert result.sharpe_ratio is not None

    def test_with_fill_pipeline(self):
        """Works with FillPipeline (realistic fills)."""
        s = MidnightGardener()
        sol = _candles(1000000, 300, volatility=5.0)
        btc = _btc_candles(1000000, 300)

        pipeline = FillPipeline(impact_coefficient=0.005, fallback_bps=10)
        engine = BacktestEngine(s, 10_000.0, 0.00035, fill_model=pipeline)
        result = engine.run({"SOL-PERP": sol, "BTC-PERP": btc})
        assert result is not None

    def test_with_margin_tracking(self):
        """Works with margin engine enabled."""
        s = MidnightGardener()
        sol = _candles(1000000, 300, volatility=5.0)
        btc = _btc_candles(1000000, 300)

        engine = BacktestEngine(
            s, 10_000.0, 0.00035,
            margin_engine=MarginEngine(),
        )
        result = engine.run({"SOL-PERP": sol, "BTC-PERP": btc})
        assert result is not None
        # Leverage should be low (strategy uses 30% max alloc)
        if result.margin_stats:
            assert result.margin_stats.max_leverage < 5.0

    def test_with_funding_rates(self):
        """Strategy works when funding data is available."""
        s = MidnightGardener()
        sol = _candles(1000000, 300, volatility=5.0)
        btc = _btc_candles(1000000, 300)

        funding = [
            FundingRate(market="SOL-PERP", ts=1000000 + i * 3600,
                        rate=0.0001, oracle_price=100.0, mark_price=100.0, source="")
            for i in range(300)
        ]
        engine = BacktestEngine(s, 10_000.0, 0.00035, funding_rates=funding)
        result = engine.run({"SOL-PERP": sol, "BTC-PERP": btc})
        assert result is not None

    def test_with_slippage(self):
        """Results degrade gracefully with slippage."""
        s1 = MidnightGardener()
        s2 = MidnightGardener()
        sol = _candles(1000000, 300, volatility=5.0)
        btc = _btc_candles(1000000, 300)

        e1 = BacktestEngine(s1, 10_000.0, 0.00035, fill_model=ClosePriceFill())
        r1 = e1.run({"SOL-PERP": sol, "BTC-PERP": btc})

        e2 = BacktestEngine(s2, 10_000.0, 0.00035, fill_model=SlippageFill(slippage_bps=20))
        r2 = e2.run({"SOL-PERP": sol, "BTC-PERP": btc})

        # Slippage should make PnL worse or equal
        assert r2.total_pnl <= r1.total_pnl + 50  # small tolerance

    def test_max_drawdown_bounded(self):
        """Max drawdown stays reasonable with conservative sizing."""
        s = MidnightGardener()
        # Volatile market
        sol = _candles(1000000, 500, volatility=8.0, trend=-0.3)
        btc = _btc_candles(1000000, 500, trend=-50)

        engine = BacktestEngine(s, 10_000.0, 0.00035)
        result = engine.run({"SOL-PERP": sol, "BTC-PERP": btc})
        # Strategy should not blow up — DD bounded by sizing
        assert result.max_drawdown < 0.50  # less than 50%

    def test_position_not_stacked(self):
        """Only one position open at a time (no venue mismatch stacking)."""
        s = MidnightGardener()
        sol = _candles(1000000, 300, volatility=5.0)
        btc = _btc_candles(1000000, 300)

        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(10_000.0, fill_model=ClosePriceFill(), fee_model=ZeroFeeModel())

        # Simulate running — positions should never exceed 1
        max_positions = 0
        history = []
        all_histories = {"SOL-PERP": sol, "BTC-PERP": btc}
        ctx.set_market_histories(all_histories)

        for candle in sol:
            ctx.set_candle(candle)
            ctx.process_pending_orders(candle)
            history.append(candle)
            s.on_candle(candle, history, ctx)
            ctx.process_market_orders(candle)
            pos_count = len(ctx.positions)
            max_positions = max(max_positions, pos_count)

        assert max_positions <= 1, f"Max positions was {max_positions}, expected <= 1"


class TestMidnightGardenerParameters:
    """Test parameter variations don't crash."""

    @pytest.mark.parametrize("rsi_period", [8, 10, 14])
    def test_rsi_period_variations(self, rsi_period):
        s = MidnightGardener(rsi_period=rsi_period)
        sol = _candles(1000000, 200, volatility=4.0)
        btc = _btc_candles(1000000, 200)
        engine = BacktestEngine(s, 10_000.0, 0.0005)
        result = engine.run({"SOL-PERP": sol, "BTC-PERP": btc})
        assert result is not None

    @pytest.mark.parametrize("stop_atr_mult", [1.5, 2.5, 3.5])
    def test_stop_atr_variations(self, stop_atr_mult):
        s = MidnightGardener(stop_atr_mult=stop_atr_mult)
        sol = _candles(1000000, 200, volatility=4.0)
        btc = _btc_candles(1000000, 200)
        engine = BacktestEngine(s, 10_000.0, 0.0005)
        result = engine.run({"SOL-PERP": sol, "BTC-PERP": btc})
        assert result is not None
