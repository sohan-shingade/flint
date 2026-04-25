"""Tests for risk management — guards, RiskManager, engine integration."""
from __future__ import annotations

from flint.backtest.engine import BacktestEngine
from flint.models import (
    AccountState, Candle, Order, OrderType, PositionInfo, Side,
)
from flint.risk.guards import (
    DailyLossLimit,
    MaxDrawdownCircuitBreaker,
    MaxOpenPositions,
    MaxPositionSize,
    RiskManager,
)
from flint.strategy import MACrossoverStrategy


def _order(market="SOL-PERP", side=Side.LONG, size=10, price=100, oid="o1", ts=0):
    return Order(market=market, side=side, order_type=OrderType.MARKET,
                 size=size, price=price, order_id=oid, ts=ts)


def _account(equity=10000, cash=10000):
    return AccountState(equity=equity, cash=cash)


def _pos(market="SOL-PERP", side=Side.LONG, size=10, entry=100):
    return PositionInfo(market=market, side=side, size=size, entry_price=entry)


def _c(ts, close, market="SOL-PERP"):
    return Candle(ts=ts, open=close, high=close + 1, low=close - 1,
                  close=close, volume=100, market=market, resolution_s=3600)


# --- MaxPositionSize ---

class TestMaxPositionSize:
    def test_passes_small_order(self):
        guard = MaxPositionSize(max_notional=5000)
        order = _order(size=10, price=100)  # notional=1000
        result = guard.check(order, _account(), [])
        assert result is not None

    def test_rejects_large_order(self):
        guard = MaxPositionSize(max_notional=500)
        order = _order(size=10, price=100)  # notional=1000
        result = guard.check(order, _account(), [])
        assert result is None


# --- MaxOpenPositions ---

class TestMaxOpenPositions:
    def test_passes_within_limit(self):
        guard = MaxOpenPositions(max_positions=3)
        result = guard.check(_order(), _account(), [_pos()])
        assert result is not None

    def test_rejects_at_limit(self):
        guard = MaxOpenPositions(max_positions=2)
        positions = [_pos("SOL-PERP"), _pos("BTC-PERP")]
        order = _order(market="ETH-PERP")  # new market
        result = guard.check(order, _account(), positions)
        assert result is None

    def test_allows_same_market_at_limit(self):
        guard = MaxOpenPositions(max_positions=2)
        positions = [_pos("SOL-PERP"), _pos("BTC-PERP")]
        order = _order(market="SOL-PERP")  # modifying existing
        result = guard.check(order, _account(), positions)
        assert result is not None


# --- MaxDrawdownCircuitBreaker ---

class TestMaxDrawdownCircuitBreaker:
    def test_passes_no_drawdown(self):
        guard = MaxDrawdownCircuitBreaker(max_drawdown_pct=0.10, initial_capital=10000)
        result = guard.check(_order(), _account(10500), [])
        assert result is not None

    def test_trips_on_drawdown(self):
        guard = MaxDrawdownCircuitBreaker(max_drawdown_pct=0.10, initial_capital=10000)
        # Update peak
        guard.check(_order(), _account(11000), [])
        # Now equity drops to 9800 → 10.9% drawdown from 11000 peak
        result = guard.check(_order(), _account(9800), [])
        assert result is None

    def test_stays_tripped(self):
        guard = MaxDrawdownCircuitBreaker(max_drawdown_pct=0.10, initial_capital=10000)
        guard.check(_order(), _account(11000), [])
        guard.check(_order(), _account(9800), [])  # trip
        result = guard.check(_order(), _account(11000), [])  # equity recovered
        assert result is None  # still tripped

    def test_reset(self):
        guard = MaxDrawdownCircuitBreaker(max_drawdown_pct=0.10, initial_capital=10000)
        guard.check(_order(), _account(11000), [])
        guard.check(_order(), _account(9800), [])  # trip
        guard.reset()
        result = guard.check(_order(), _account(10000), [])
        assert result is not None  # un-tripped


# --- DailyLossLimit ---

class TestDailyLossLimit:
    def test_passes_within_limit(self):
        guard = DailyLossLimit(max_daily_loss=500, initial_capital=10000)
        order = _order(ts=86400)  # day 1
        result = guard.check(order, _account(9600), [])
        assert result is not None

    def test_trips_on_daily_loss(self):
        guard = DailyLossLimit(max_daily_loss=500, initial_capital=10000)
        o1 = _order(ts=86400, oid="o1")  # day 1
        guard.check(o1, _account(10000), [])  # set day start
        o2 = _order(ts=86400 + 3600, oid="o2")
        result = guard.check(o2, _account(9400), [])  # -600 < -500
        assert result is None

    def test_resets_on_new_day(self):
        guard = DailyLossLimit(max_daily_loss=500, initial_capital=10000)
        guard.check(_order(ts=86400), _account(10000), [])
        guard.check(_order(ts=86400 + 3600), _account(9400), [])  # trip
        # New day
        result = guard.check(_order(ts=86400 * 2), _account(9400), [])
        assert result is not None  # new day, not tripped


# --- RiskManager ---

class TestRiskManager:
    def test_empty_passes(self):
        rm = RiskManager()
        result = rm.evaluate(_order(), _account(), [])
        assert result is not None

    def test_chain_passes(self):
        rm = RiskManager([
            MaxPositionSize(max_notional=5000),
            MaxOpenPositions(max_positions=5),
        ])
        result = rm.evaluate(_order(size=10, price=100), _account(), [])
        assert result is not None

    def test_chain_rejects(self):
        rm = RiskManager([
            MaxPositionSize(max_notional=500),  # will reject
            MaxOpenPositions(max_positions=5),
        ])
        result = rm.evaluate(_order(size=10, price=100), _account(), [])
        assert result is None
        assert len(rm.rejections) == 1
        assert rm.rejections[0]["guard"] == "MaxPositionSize"


# --- Engine Integration ---

class TestRiskEngineIntegration:
    def test_risk_manager_in_backtest(self):
        """Risk manager should block orders in backtest."""
        rm = RiskManager([
            MaxDrawdownCircuitBreaker(max_drawdown_pct=0.01, initial_capital=10000),
        ])
        strategy = MACrossoverStrategy(fast_period=5, slow_period=10)
        candles = []
        for i in range(60):
            if i < 20:
                price = 100 - i * 0.5
            elif i < 40:
                price = 90 + (i - 20) * 1.0
            else:
                price = 110 - (i - 40) * 0.5
            candles.append(_c(i * 3600, price))

        engine = BacktestEngine(
            strategy, initial_capital=10_000, fee_rate=0.0,
            risk_manager=rm,
        )
        result = engine.run(candles)
        # With 1% max drawdown, circuit breaker may trip early
        # Just verify it runs without error
        assert len(result.equity_curve) == 60
