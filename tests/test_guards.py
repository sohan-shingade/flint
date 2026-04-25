"""Tests for extended risk guards."""
import time
from flint.models import AccountState, Order, OrderType, PositionInfo, Side
from flint.risk.guards import MaxOrdersPerMinute, PerMarketPositionLimit


class TestMaxOrdersPerMinute:
    def _make_order(self, ts, order_id="o1"):
        return Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                     size=1.0, order_id=order_id, ts=ts)
    def _account(self):
        return AccountState(equity=10000, cash=10000)

    def test_allows_within_limit(self):
        guard = MaxOrdersPerMinute(max_orders=5)
        now = int(time.time())
        for i in range(5):
            result = guard.check(self._make_order(now, f"o{i}"), self._account(), [])
            assert result is not None

    def test_rejects_over_limit(self):
        guard = MaxOrdersPerMinute(max_orders=3)
        now = int(time.time())
        for i in range(3):
            guard.check(self._make_order(now, f"o{i}"), self._account(), [])
        result = guard.check(self._make_order(now, "o3"), self._account(), [])
        assert result is None

    def test_old_orders_expire(self):
        guard = MaxOrdersPerMinute(max_orders=2)
        old_ts = int(time.time()) - 61
        guard.check(self._make_order(old_ts, "o0"), self._account(), [])
        guard.check(self._make_order(old_ts, "o1"), self._account(), [])
        now = int(time.time())
        result = guard.check(self._make_order(now, "o2"), self._account(), [])
        assert result is not None


class TestPerMarketPositionLimit:
    def _make_order(self, market, size, price=150.0):
        return Order(market=market, side=Side.LONG, order_type=OrderType.MARKET,
                     size=size, price=price, order_id="o1", ts=int(time.time()))
    def _account(self):
        return AccountState(equity=10000, cash=10000)

    def test_allows_within_limit(self):
        guard = PerMarketPositionLimit(limits={"SOL-PERP": 10000})
        order = self._make_order("SOL-PERP", 10.0, 150.0)
        result = guard.check(order, self._account(), [])
        assert result is not None

    def test_rejects_over_limit(self):
        guard = PerMarketPositionLimit(limits={"SOL-PERP": 1000})
        order = self._make_order("SOL-PERP", 10.0, 150.0)
        result = guard.check(order, self._account(), [])
        assert result is None

    def test_includes_existing_position(self):
        guard = PerMarketPositionLimit(limits={"SOL-PERP": 2000})
        existing = PositionInfo(market="SOL-PERP", side=Side.LONG, size=10.0, entry_price=150.0)
        order = self._make_order("SOL-PERP", 5.0, 150.0)
        result = guard.check(order, self._account(), [existing])
        assert result is None

    def test_uncapped_market_passes(self):
        guard = PerMarketPositionLimit(limits={"SOL-PERP": 1000})
        order = self._make_order("BTC-PERP", 100.0, 65000.0)
        result = guard.check(order, self._account(), [])
        assert result is not None

    def test_empty_limits_passes_all(self):
        guard = PerMarketPositionLimit(limits={})
        order = self._make_order("SOL-PERP", 1000.0, 150.0)
        result = guard.check(order, self._account(), [])
        assert result is not None
