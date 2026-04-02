"""Tests for tx_cost integration."""
import pytest
from flint.models import Fill, BacktestResult, Side, Signal

class TestFillTxCost:
    def test_default_zero(self):
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=150.0, size=10.0, fee=0.075, ts=1000)
        assert fill.tx_cost == 0.0

    def test_with_tx_cost(self):
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=150.0, size=10.0,
                    fee=0.075, ts=1000, tx_cost=0.005)
        assert fill.tx_cost == 0.005

class TestBacktestResultTxCosts:
    def test_default_zero(self):
        result = BacktestResult(total_pnl=100, win_rate=0.5, max_drawdown=0.1,
            sharpe_ratio=1.5, total_trades=10, winning_trades=5, losing_trades=5)
        assert result.total_tx_costs == 0.0

    def test_with_tx_costs(self):
        result = BacktestResult(total_pnl=100, win_rate=0.5, max_drawdown=0.1,
            sharpe_ratio=1.5, total_trades=10, winning_trades=5, losing_trades=5,
            total_tx_costs=0.05)
        assert result.total_tx_costs == 0.05


from flint.backtest.engine import BacktestEngine
from flint.models import Candle
from flint.strategy.base import Strategy
from flint.execution.tx_costs import SolanaTxCostModel
from flint.execution.fill_models import FillPipeline


class SimpleStrategy(Strategy):
    @property
    def name(self):
        return "simple"
    def reset(self):
        self._bought = False
    def on_candle(self, candle, history, ctx=None):
        if ctx and not self._bought and len(history) >= 2:
            ctx.market_order(candle.market, Side.LONG, 1.0)
            self._bought = True
        return Signal.HOLD


class TestBacktestTxCostDeduction:
    def test_tx_cost_deducted(self):
        strategy = SimpleStrategy()
        tx_model = SolanaTxCostModel(
            priority_fee_lamports=1_000_000_000, jito_tip_lamports=1_000_000_000,
            sol_price_usd=150.0, exchange_fee_bps=0,
        )
        pipeline = FillPipeline(impact_coefficient=0.0, latency_enabled=False, tx_cost_model=tx_model)
        engine = BacktestEngine(strategy=strategy, initial_capital=10000.0, fill_model=pipeline, fee_rate=0.0)
        candles = [Candle(ts=1000+i*60, open=150.0, high=151.0, low=149.0, close=150.0,
                          volume=10000.0, market="SOL-PERP", resolution_s=60) for i in range(10)]
        result = engine.run(candles)
        assert result.total_tx_costs > 0

    def test_no_tx_cost_without_model(self):
        strategy = SimpleStrategy()
        engine = BacktestEngine(strategy=strategy, initial_capital=10000.0)
        candles = [Candle(ts=1000+i*60, open=150.0, high=151.0, low=149.0, close=150.0,
                          volume=10000.0, market="SOL-PERP", resolution_s=60) for i in range(10)]
        result = engine.run(candles)
        assert result.total_tx_costs == 0.0


from unittest.mock import MagicMock, PropertyMock, AsyncMock
from flint.execution.live_base import LiveExecutionContext
from flint.models import AccountState

def _make_mock_venue(venue_name, cash=5000.0):
    ctx = MagicMock(spec=LiveExecutionContext)
    ctx._venue = venue_name
    type(ctx).account = PropertyMock(return_value=AccountState(equity=cash, cash=cash))
    type(ctx).positions = PropertyMock(return_value=[])
    type(ctx).pending_orders = PropertyMock(return_value=[])
    type(ctx).current_candle = PropertyMock(return_value=None)
    type(ctx).timestamp = PropertyMock(return_value=1000)
    ctx.estimate_cost = MagicMock(return_value=None)
    ctx.connect = AsyncMock()
    ctx.disconnect = AsyncMock()
    ctx.submit_pending_orders = AsyncMock(return_value=[])
    ctx._poll_orders_loop = AsyncMock()
    return ctx


class TestEstimateCost:
    def test_default_returns_none(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        ctx = MultiVenueLiveContext(contexts={"drift": drift})
        result = ctx.estimate_cost("SOL-PERP", 10.0, venue="drift")
        assert result is None

    def test_multi_venue_delegates(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        from flint.execution.tx_costs import CostEstimate
        drift = _make_mock_venue("drift")
        drift.estimate_cost.return_value = CostEstimate(
            exchange_fee=0.75, network_fee=0.001, bundle_tip=0.01, impact_est=0.0,
        )
        ctx = MultiVenueLiveContext(contexts={"drift": drift})
        result = ctx.estimate_cost("SOL-PERP", 10.0, venue="drift")
        assert result is not None
        assert result.total > 0
