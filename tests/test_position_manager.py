"""D-2.1.b Step 1 — PositionManager unit tests.

Validates the position-state container that BacktestContext now
composes. Steps 2–7 migrate call sites to use PositionManager's
explicit API; this file pins the contract before that work.
"""
from __future__ import annotations


from flint.execution.position_manager import PositionManager
from flint.models import Side


class _StubPosition:
    """Minimal stand-in for `_Position` with the fields the manager touches."""

    def __init__(self, market: str, venue: str, side: Side, size: float,
                 entry_price: float = 100.0):
        self.market = market
        self.venue = venue
        self.side = side
        self.size = size
        self.entry_price = entry_price
        self.unrealized_pnl = 0.0

    def update_pnl(self, current_price: float) -> None:
        if self.side == Side.LONG:
            self.unrealized_pnl = (current_price - self.entry_price) * self.size
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.size


class TestSinglePositionOps:
    def test_get_set_delete(self):
        pm = PositionManager()
        key = ("drift", "SOL-PERP")
        assert pm.get(key) is None
        assert key not in pm

        p = _StubPosition("SOL-PERP", "drift", Side.LONG, 1.0)
        pm.set(key, p)
        assert pm.get(key) is p
        assert key in pm
        assert len(pm) == 1
        assert bool(pm) is True

        pm.delete(key)
        assert pm.get(key) is None
        assert len(pm) == 0
        assert bool(pm) is False


class TestDictLikeSurface:
    def test_iter_keys_values_items(self):
        pm = PositionManager()
        a = _StubPosition("SOL-PERP", "drift", Side.LONG, 1.0)
        b = _StubPosition("BTC-PERP", "hyperliquid", Side.SHORT, 0.5)
        pm.set(("drift", "SOL-PERP"), a)
        pm.set(("hyperliquid", "BTC-PERP"), b)

        keys = sorted(pm.keys())
        assert keys == [("drift", "SOL-PERP"), ("hyperliquid", "BTC-PERP")]
        assert sorted(k for k in pm) == keys

        values = list(pm.values())
        assert a in values and b in values

        items = dict(pm.items())
        assert items[("drift", "SOL-PERP")] is a


class TestUpdatePnlForMarket:
    def test_only_target_market_updates(self):
        pm = PositionManager()
        sol = _StubPosition("SOL-PERP", "drift", Side.LONG, 2.0, entry_price=150.0)
        btc = _StubPosition("BTC-PERP", "drift", Side.SHORT, 0.5, entry_price=70_000.0)
        pm.set(("drift", "SOL-PERP"), sol)
        pm.set(("drift", "BTC-PERP"), btc)

        pm.update_pnl_for_market("SOL-PERP", price=160.0)
        assert sol.unrealized_pnl == (160.0 - 150.0) * 2.0
        assert btc.unrealized_pnl == 0.0  # unchanged

        pm.update_pnl_for_market("BTC-PERP", price=68_000.0)
        # short: (entry - price) * size
        assert btc.unrealized_pnl == (70_000.0 - 68_000.0) * 0.5

    def test_short_pnl_sign(self):
        pm = PositionManager()
        s = _StubPosition("SOL-PERP", "drift", Side.SHORT, 1.0, entry_price=100.0)
        pm.set(("drift", "SOL-PERP"), s)
        pm.update_pnl_for_market("SOL-PERP", price=90.0)
        assert s.unrealized_pnl == 10.0  # short profits when price falls


class TestClosedLedger:
    def test_record_close_appends(self):
        pm = PositionManager()
        record = {"market": "SOL-PERP", "pnl": 12.34, "ts": 1700000000}
        pm.record_close(record)
        assert pm.closed == [record]

        pm.record_close({"market": "BTC-PERP", "pnl": -5.0, "ts": 1700001000})
        assert len(pm.closed) == 2

    def test_closed_is_a_copy(self):
        pm = PositionManager()
        pm.record_close({"x": 1})
        snap = pm.closed
        snap.append({"injected": True})
        # Internal ledger should not be polluted
        assert pm.closed == [{"x": 1}]


class TestBacktestContextIntegration:
    def test_context_uses_position_manager(self):
        from flint.execution.backtest_context import BacktestContext

        ctx = BacktestContext(initial_capital=10_000)
        assert isinstance(ctx._pm, PositionManager)
        # Legacy aliases still work (Steps 2–7 migrate call sites).
        assert ctx._positions == {}
        assert ctx._closed_positions == []

    def test_legacy_dict_mutation_routes_through_manager(self):
        from flint.execution.backtest_context import BacktestContext, _Position

        ctx = BacktestContext(initial_capital=10_000)
        # Mutating the alias must reflect on the manager (otherwise
        # `_apply_fill`'s `self._positions[key] = ...` breaks).
        p = _Position(market="SOL-PERP", side=Side.LONG, size=1.0,
                      entry_price=150.0, entry_ts=1, venue="drift")
        ctx._positions[("drift", "SOL-PERP")] = p
        assert ctx._pm.get(("drift", "SOL-PERP")) is p

        del ctx._positions[("drift", "SOL-PERP")]
        assert ctx._pm.get(("drift", "SOL-PERP")) is None
