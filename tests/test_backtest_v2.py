"""Tests for v0.2 backtest engine — fill models, fee models, BacktestContext,
stop-loss/take-profit, limit orders, funding, multi-position, v1 compat."""
from __future__ import annotations

from typing import Dict, List, Optional

import pytest

from flint.backtest.engine import BacktestEngine
from flint.execution.backtest_context import BacktestContext
from flint.execution.context import ExecutionContext
from flint.execution.fee_models import FlatFeeModel, DriftFeeModel, ZeroFeeModel
from flint.execution.fill_models import (
    ClosePriceFill, NextBarOpenFill, SlippageFill,
)
from flint.models import (
    AccountState, Candle, Fill, FundingRate, Order, OrderType,
    PositionInfo, Side, Signal,
)
from flint.strategy import Strategy, MACrossoverStrategy


# --- Helpers ---

def _c(ts: int, close: float, high: float = 0, low: float = 0,
       open_: float = 0, market: str = "SOL-PERP") -> Candle:
    h = high or close + 1
    l = low or close - 1
    o = open_ or close
    return Candle(ts=ts, open=o, high=h, low=l, close=close,
                  volume=100.0, market=market, resolution_s=3600)


def _rising(n=50, start=100.0, step=0.5) -> List[Candle]:
    return [_c(i * 3600, start + i * step) for i in range(n)]


def _down_up(n=60) -> List[Candle]:
    """Down then up pattern to trigger buy/sell for MA crossover."""
    candles = []
    for i in range(n):
        if i < 20:
            price = 100 - i * 0.5
        elif i < 40:
            price = 90 + (i - 20) * 1.0
        else:
            price = 110 - (i - 40) * 0.5
        candles.append(_c(i * 3600, price))
    return candles


# --- v2 strategy that uses context ---

class StopLossStrategy(Strategy):
    """Opens long, attaches stop-loss at 5% below entry."""

    @property
    def name(self) -> str:
        return "StopLossTest"

    def reset(self) -> None:
        self._entered = False

    def on_candle(self, candle, history, ctx=None):
        if ctx is None:
            return Signal.HOLD
        if not self._entered and len(history) >= 2:
            ctx.market_order(candle.market, Side.LONG, 10.0)
            stop_price = candle.close * 0.95
            ctx.stop_order(candle.market, Side.SHORT, 10.0, stop_price)
            self._entered = True
        return Signal.HOLD


class LimitOrderStrategy(Strategy):
    """Places a limit buy below current price."""

    @property
    def name(self) -> str:
        return "LimitTest"

    def reset(self) -> None:
        self._placed = False

    def on_candle(self, candle, history, ctx=None):
        if ctx is None:
            return Signal.HOLD
        if not self._placed and len(history) >= 2:
            limit_price = candle.close * 0.98
            ctx.limit_order(candle.market, Side.LONG, 10.0, limit_price)
            self._placed = True
        return Signal.HOLD


# --- Fill Model Tests ---

class TestFillModels:
    def test_close_price_fill_market(self):
        fm = ClosePriceFill()
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=10, order_id="o1")
        candle = _c(1000, close=150.0)
        fill = fm.fill_market(order, candle)
        assert fill is not None
        assert fill.price == 150.0
        assert fill.size == 10

    def test_close_price_fill_limit_buy_triggers(self):
        fm = ClosePriceFill()
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.LIMIT,
                      size=10, price=148.0, order_id="o1")
        candle = _c(1000, close=150.0, low=147.0)  # low < limit
        fill = fm.fill_limit(order, candle)
        assert fill is not None
        assert fill.price == 148.0

    def test_close_price_fill_limit_buy_no_trigger(self):
        fm = ClosePriceFill()
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.LIMIT,
                      size=10, price=148.0, order_id="o1")
        candle = _c(1000, close=150.0, low=149.0)  # low > limit
        fill = fm.fill_limit(order, candle)
        assert fill is None

    def test_slippage_fill_buys_higher(self):
        fm = SlippageFill(slippage_bps=10)
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=10, order_id="o1")
        candle = _c(1000, close=100.0)
        fill = fm.fill_market(order, candle)
        assert fill.price > 100.0
        assert abs(fill.price - 100.1) < 0.001  # 10bps of 100

    def test_slippage_fill_sells_lower(self):
        fm = SlippageFill(slippage_bps=10)
        order = Order(market="SOL-PERP", side=Side.SHORT, order_type=OrderType.MARKET,
                      size=10, order_id="o1")
        candle = _c(1000, close=100.0)
        fill = fm.fill_market(order, candle)
        assert fill.price < 100.0

    def test_next_bar_open_fill(self):
        fm = NextBarOpenFill()
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=10, order_id="o1")
        candle = _c(1000, close=150.0, open_=148.0)
        fill = fm.fill_market(order, candle)
        assert fill.price == 148.0

    def test_stop_trigger_long_position(self):
        fm = ClosePriceFill()
        # Stop-loss for a long position: sell side, triggers when price drops
        order = Order(market="SOL-PERP", side=Side.SHORT, order_type=OrderType.STOP_LOSS,
                      size=10, price=95.0, order_id="o1")
        candle_triggers = _c(1000, close=94.0, low=93.0)
        candle_no = _c(1000, close=100.0, low=96.0)
        assert fm.check_stop_trigger(order, candle_triggers) is True
        assert fm.check_stop_trigger(order, candle_no) is False


# --- Fee Model Tests ---

class TestFeeModels:
    def test_flat_fee(self):
        fm = FlatFeeModel(fee_bps=5)
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=100.0,
                    size=10, fee=0, ts=1000)
        fee = fm.compute_fee(fill)
        assert abs(fee - 0.5) < 0.001  # 10 * 100 * 0.0005

    def test_zero_fee(self):
        fm = ZeroFeeModel()
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=100.0,
                    size=10, fee=0, ts=1000)
        assert fm.compute_fee(fill) == 0.0

    def test_drift_taker_fee(self):
        fm = DriftFeeModel(taker_fee=0.001)
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=100.0,
                    size=10, fee=0, ts=1000)
        fee = fm.compute_fee(fill)
        assert abs(fee - 1.0) < 0.001  # 10 * 100 * 0.001


# --- BacktestContext Tests ---

class TestBacktestContext:
    def test_initial_state(self):
        ctx = BacktestContext(10000, fee_model=ZeroFeeModel())
        assert ctx.account.equity == 10000
        assert ctx.account.cash == 10000
        assert ctx.positions == []
        assert ctx.pending_orders == []

    def test_market_order_opens_position(self):
        ctx = BacktestContext(10000, fee_model=ZeroFeeModel())
        candle = _c(1000, 100.0)
        ctx.set_candle(candle)
        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        fills = ctx.process_market_orders(candle)
        assert len(fills) == 1
        assert len(ctx.positions) == 1
        assert ctx.positions[0].side == Side.LONG
        assert ctx.positions[0].size == 10.0

    def test_close_position(self):
        ctx = BacktestContext(10000, fee_model=ZeroFeeModel())
        c1 = _c(1000, 100.0)
        ctx.set_candle(c1)
        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        ctx.process_market_orders(c1)

        c2 = _c(2000, 110.0)
        ctx.set_candle(c2)
        ctx.close_position("SOL-PERP")
        ctx.process_market_orders(c2)
        assert len(ctx.positions) == 0
        assert len(ctx.closed_trades) == 1
        assert ctx.closed_trades[0]["pnl"] == 100.0  # (110-100)*10

    def test_stop_loss_triggers(self):
        ctx = BacktestContext(10000, fee_model=ZeroFeeModel())
        c1 = _c(1000, 100.0)
        ctx.set_candle(c1)
        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        ctx.process_market_orders(c1)

        # Place stop at 95
        ctx.stop_order("SOL-PERP", Side.SHORT, 10.0, 95.0)
        assert len(ctx.pending_orders) == 1

        # Candle with low=94 should trigger stop
        c2 = _c(2000, 96.0, low=94.0)
        ctx.set_candle(c2)
        fills = ctx.process_pending_orders(c2)
        assert len(fills) == 1
        assert len(ctx.positions) == 0  # position closed

    def test_limit_order_fills(self):
        ctx = BacktestContext(10000, fee_model=ZeroFeeModel())
        c1 = _c(1000, 100.0)
        ctx.set_candle(c1)
        ctx.limit_order("SOL-PERP", Side.LONG, 10.0, 98.0)

        # Price doesn't reach 98
        c2 = _c(2000, 101.0, low=99.0)
        ctx.set_candle(c2)
        fills = ctx.process_pending_orders(c2)
        assert len(fills) == 0

        # Price reaches 98
        c3 = _c(3000, 100.0, low=97.0)
        ctx.set_candle(c3)
        fills = ctx.process_pending_orders(c3)
        assert len(fills) == 1
        assert fills[0].price == 98.0

    def test_cancel_order(self):
        ctx = BacktestContext(10000, fee_model=ZeroFeeModel())
        c1 = _c(1000, 100.0)
        ctx.set_candle(c1)
        oid = ctx.limit_order("SOL-PERP", Side.LONG, 10.0, 95.0)
        assert len(ctx.pending_orders) == 1
        assert ctx.cancel(oid) is True
        assert len(ctx.pending_orders) == 0

    def test_cancel_all(self):
        ctx = BacktestContext(10000, fee_model=ZeroFeeModel())
        c1 = _c(1000, 100.0)
        ctx.set_candle(c1)
        ctx.limit_order("SOL-PERP", Side.LONG, 10.0, 95.0)
        ctx.limit_order("SOL-PERP", Side.LONG, 5.0, 90.0)
        assert ctx.cancel_all() == 2
        assert len(ctx.pending_orders) == 0

    def test_funding_payment(self):
        ctx = BacktestContext(10000, fee_model=ZeroFeeModel())
        c1 = _c(1000, 100.0)
        ctx.set_candle(c1)
        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        ctx.process_market_orders(c1)

        fr = FundingRate(market="SOL-PERP", ts=2000, rate=0.001,
                         oracle_price=100.0, mark_price=100.0)
        payment = ctx.apply_funding(fr)
        # 10 * 100 * 0.001 = 1.0
        assert abs(payment - 1.0) < 0.001
        assert ctx.total_funding > 0

    def test_fees_deducted(self):
        ctx = BacktestContext(10000, fee_model=FlatFeeModel(fee_bps=10))
        c1 = _c(1000, 100.0)
        ctx.set_candle(c1)
        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        ctx.process_market_orders(c1)
        # Fee: 10 * 100 * 0.001 = 1.0
        assert ctx.total_fees > 0
        assert ctx.account.cash < 10000


# --- Full Engine Tests ---

class TestEngineV2:
    def test_v1_strategy_backwards_compat(self):
        """v0.1 MACrossover strategy should still produce trades."""
        strategy = MACrossoverStrategy(fast_period=5, slow_period=10)
        engine = BacktestEngine(strategy, initial_capital=10_000, fee_rate=0.0)
        candles = _down_up(60)
        result = engine.run(candles)
        assert result.total_trades > 0
        assert len(result.equity_curve) == 60

    def test_v1_result_has_new_fields(self):
        """v0.2 engine populates fills, total_fees, funding_paid."""
        strategy = MACrossoverStrategy(fast_period=5, slow_period=10)
        engine = BacktestEngine(strategy, initial_capital=10_000, fee_rate=0.0005)
        candles = _down_up(60)
        result = engine.run(candles)
        assert hasattr(result, "fills")
        assert hasattr(result, "total_fees")
        assert hasattr(result, "funding_paid")
        if result.total_trades > 0:
            assert len(result.fills) > 0
            assert result.total_fees > 0

    def test_v2_strategy_with_stop_loss(self):
        """v2 strategy that places stop-loss should have it trigger."""
        strategy = StopLossStrategy()
        engine = BacktestEngine(strategy, initial_capital=10_000, fee_rate=0.0)
        # Price drops enough to trigger 5% stop
        candles = [
            _c(0, 100.0),
            _c(3600, 100.0),
            _c(7200, 100.0),  # entry here
            _c(10800, 94.0, low=93.0),  # triggers stop at 95
            _c(14400, 90.0),
        ]
        result = engine.run(candles)
        assert result.total_trades >= 1

    def test_v2_limit_order_strategy(self):
        """v2 strategy that places limit order should fill when price drops."""
        strategy = LimitOrderStrategy()
        engine = BacktestEngine(strategy, initial_capital=10_000, fee_rate=0.0)
        candles = [
            _c(0, 100.0),
            _c(3600, 100.0),  # places limit at 98
            _c(7200, 99.0, low=97.0),  # price dips to 97, fills at 98
            _c(10800, 105.0),
        ]
        result = engine.run(candles)
        # Should have opened via limit order and force-closed at end
        assert result.total_trades >= 1

    def test_custom_fill_model(self):
        """Engine accepts custom fill model."""
        strategy = MACrossoverStrategy(fast_period=5, slow_period=10)
        engine = BacktestEngine(
            strategy, initial_capital=10_000,
            fill_model=SlippageFill(slippage_bps=10),
            fee_model=ZeroFeeModel(),
        )
        candles = _down_up(60)
        result = engine.run(candles)
        assert len(result.equity_curve) == 60

    def test_custom_fee_model(self):
        """Engine accepts custom fee model."""
        strategy = MACrossoverStrategy(fast_period=5, slow_period=10)
        engine_no_fee = BacktestEngine(
            strategy, initial_capital=10_000,
            fee_model=ZeroFeeModel(),
        )
        engine_high_fee = BacktestEngine(
            strategy, initial_capital=10_000,
            fee_model=FlatFeeModel(fee_bps=50),  # 50bps = 0.5%
        )
        candles = _down_up(60)
        r1 = engine_no_fee.run(candles)
        strategy.reset()
        r2 = engine_high_fee.run(candles)
        if r1.total_trades > 0:
            assert r1.total_pnl > r2.total_pnl  # fees reduce PnL

    def test_funding_rates_applied(self):
        """Funding rates should affect equity."""
        strategy = MACrossoverStrategy(fast_period=5, slow_period=10)
        candles = _down_up(60)
        funding = [
            FundingRate(market="SOL-PERP", ts=c.ts, rate=0.001,
                        oracle_price=c.close, mark_price=c.close)
            for c in candles
        ]
        engine = BacktestEngine(
            strategy, initial_capital=10_000, fee_rate=0.0,
            funding_rates=funding,
        )
        result = engine.run(candles)
        assert result.funding_paid != 0 or result.total_trades == 0

    def test_empty_candles(self):
        strategy = MACrossoverStrategy()
        engine = BacktestEngine(strategy)
        result = engine.run([])
        assert result.total_trades == 0
        assert result.total_pnl == 0

    def test_equity_curve_length(self):
        strategy = MACrossoverStrategy(fast_period=5, slow_period=10)
        engine = BacktestEngine(strategy, initial_capital=10_000, fee_rate=0.0)
        candles = _rising(30)
        result = engine.run(candles)
        assert len(result.equity_curve) == 30
