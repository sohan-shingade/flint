"""Tests for Strategy API v2 — ExecutionContext, backwards compatibility, parameters."""
from __future__ import annotations

from typing import Dict, List, Optional

from flint.execution.context import ExecutionContext
from flint.models import (
    AccountState,
    Candle,
    Order,
    PositionInfo,
    Side,
    Signal,
)
from flint.strategy import (
    Strategy,
    MACrossoverStrategy,
    EMACrossoverStrategy,
    RSIStrategy,
    BollingerStrategy,
    MomentumStrategy,
)


# --- Test fixtures ---

def _make_candle(ts: int, close: float, market: str = "SOL-PERP") -> Candle:
    return Candle(ts=ts, open=close, high=close + 1, low=close - 1,
                  close=close, volume=100.0, market=market, resolution_s=3600)


def _rising_candles(n: int = 50) -> List[Candle]:
    return [_make_candle(i * 3600, 100 + i * 0.5) for i in range(n)]


# --- Stub context for testing ---

class StubContext(ExecutionContext):
    """Minimal ExecutionContext for testing strategy v2 API."""

    def __init__(self):
        self.orders: List[dict] = []
        self._candle: Optional[Candle] = None

    @property
    def account(self) -> AccountState:
        return AccountState(equity=10000, cash=10000)

    @property
    def positions(self) -> List[PositionInfo]:
        return []

    @property
    def pending_orders(self) -> List[Order]:
        return []

    @property
    def current_candle(self) -> Optional[Candle]:
        return self._candle

    @property
    def timestamp(self) -> int:
        return self._candle.ts if self._candle else 0

    def market_order(self, market, side, size, reduce_only=False, tag=""):
        oid = f"order-{len(self.orders)}"
        self.orders.append({"type": "market", "market": market, "side": side,
                           "size": size, "id": oid, "tag": tag})
        return oid

    def limit_order(self, market, side, size, price, reduce_only=False, tag=""):
        oid = f"order-{len(self.orders)}"
        self.orders.append({"type": "limit", "market": market, "side": side,
                           "size": size, "price": price, "id": oid, "tag": tag})
        return oid

    def stop_order(self, market, side, size, trigger_price, tag=""):
        oid = f"order-{len(self.orders)}"
        self.orders.append({"type": "stop", "market": market, "side": side,
                           "size": size, "trigger": trigger_price, "id": oid})
        return oid

    def take_profit_order(self, market, side, size, trigger_price, tag=""):
        oid = f"order-{len(self.orders)}"
        self.orders.append({"type": "tp", "market": market, "side": side,
                           "size": size, "trigger": trigger_price, "id": oid})
        return oid

    def cancel(self, order_id):
        return True

    def cancel_all(self, market=None):
        return 0


# --- A v2 strategy that uses the context ---

class ContextStrategy(Strategy):
    """Example v2 strategy that uses ExecutionContext."""

    @property
    def name(self) -> str:
        return "ContextTest"

    def reset(self) -> None:
        pass

    def on_candle(self, candle: Candle, history: List[Candle],
                  ctx: Optional[ExecutionContext] = None) -> Signal:
        if ctx is None:
            return Signal.HOLD
        if len(history) >= 5 and candle.close > history[-5].close:
            ctx.market_order(candle.market, Side.LONG, 1.0)
        return Signal.HOLD

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {"threshold": {"type": "float", "low": 0.1, "high": 10.0}}


# --- Tests ---

class TestBackwardsCompatibility:
    """v1 strategies must still work with the new on_candle(ctx=None) signature."""

    def test_ma_crossover_works_without_ctx(self):
        strategy = MACrossoverStrategy(fast_period=5, slow_period=10)
        candles = _rising_candles(15)
        history = []
        for c in candles:
            history.append(c)
            signal = strategy.on_candle(c, history)
            assert isinstance(signal, Signal)

    def test_all_strategies_accept_ctx_none(self):
        strategies = [
            MACrossoverStrategy(),
            EMACrossoverStrategy(),
            RSIStrategy(),
            BollingerStrategy(),
            MomentumStrategy(),
        ]
        candles = _rising_candles(35)
        for strategy in strategies:
            strategy.reset()
            history = []
            for c in candles:
                history.append(c)
                signal = strategy.on_candle(c, history, ctx=None)
                assert isinstance(signal, Signal)

    def test_all_strategies_accept_ctx_instance(self):
        ctx = StubContext()
        strategies = [
            MACrossoverStrategy(),
            EMACrossoverStrategy(),
            RSIStrategy(),
            BollingerStrategy(),
            MomentumStrategy(),
        ]
        candles = _rising_candles(35)
        for strategy in strategies:
            strategy.reset()
            history = []
            for c in candles:
                history.append(c)
                signal = strategy.on_candle(c, history, ctx=ctx)
                assert isinstance(signal, Signal)


class TestExecutionContext:
    def test_context_abc_cannot_be_instantiated(self):
        import pytest
        with pytest.raises(TypeError):
            ExecutionContext()

    def test_stub_context_market_order(self):
        ctx = StubContext()
        oid = ctx.market_order("SOL-PERP", Side.LONG, 1.0)
        assert oid == "order-0"
        assert len(ctx.orders) == 1
        assert ctx.orders[0]["type"] == "market"

    def test_stub_context_limit_order(self):
        ctx = StubContext()
        oid = ctx.limit_order("SOL-PERP", Side.LONG, 1.0, 150.0)
        assert oid == "order-0"
        assert ctx.orders[0]["price"] == 150.0

    def test_close_position_no_position(self):
        ctx = StubContext()
        result = ctx.close_position("SOL-PERP")
        assert result is None

    def test_position_helper(self):
        ctx = StubContext()
        assert ctx.position("SOL-PERP") is None


class TestV2Strategy:
    def test_context_strategy_uses_ctx(self):
        ctx = StubContext()
        strategy = ContextStrategy()
        candles = _rising_candles(10)
        history = []
        for c in candles:
            history.append(c)
            strategy.on_candle(c, history, ctx=ctx)
        # Should have placed orders once history >= 5 and price rising
        assert len(ctx.orders) > 0

    def test_context_strategy_works_without_ctx(self):
        strategy = ContextStrategy()
        candles = _rising_candles(10)
        history = []
        for c in candles:
            history.append(c)
            signal = strategy.on_candle(c, history)
            assert signal == Signal.HOLD


class TestParameters:
    def test_base_returns_empty(self):
        assert Strategy.parameters() == {}

    def test_ma_crossover_parameters(self):
        params = MACrossoverStrategy.parameters()
        assert "fast_period" in params
        assert "slow_period" in params
        assert params["fast_period"]["type"] == "int"

    def test_all_strategies_have_parameters(self):
        for cls in [MACrossoverStrategy, EMACrossoverStrategy, RSIStrategy,
                    BollingerStrategy, MomentumStrategy]:
            params = cls.parameters()
            assert isinstance(params, dict)
            assert len(params) >= 2  # all have at least 2 params

    def test_context_strategy_parameters(self):
        params = ContextStrategy.parameters()
        assert "threshold" in params


class TestNewModels:
    def test_account_state(self):
        acc = AccountState(equity=10000, cash=8000, unrealized_pnl=2000)
        assert acc.equity == 10000
        assert acc.cash == 8000
        assert acc.margin_used == 0.0

    def test_position_info(self):
        pos = PositionInfo(market="SOL-PERP", side=Side.LONG, size=10,
                          entry_price=150.0, unrealized_pnl=50.0)
        assert pos.market == "SOL-PERP"
        assert pos.side == Side.LONG
        assert pos.size == 10
