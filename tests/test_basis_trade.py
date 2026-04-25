"""Tests for BasisTradeStrategy."""
from unittest.mock import MagicMock

from flint.models import Candle, Side, Signal
from flint.strategy.basis_trade import BasisTradeStrategy


def _candle(close=100.0, ts=1000, market="SOL-PERP", venue="default"):
    return Candle(
        ts=ts, open=close, high=close + 1.0, low=close - 1.0,
        close=close, volume=500.0, market=market, resolution_s=3600,
        venue=venue,
    )


def _make_ctx(candles_by_market=None):
    ctx = MagicMock()
    candles_by_market = candles_by_market or {}
    ctx.get_candles.side_effect = lambda market: candles_by_market.get(market, [])
    ctx.market_order = MagicMock(return_value="ord-1")
    ctx.close_position = MagicMock(return_value="close-1")
    return ctx


def _venue_candles(prices: dict, ts=1000, market="SOL-PERP"):
    """Build a list of candles with different venues."""
    return [_candle(close=price, ts=ts, market=market, venue=venue)
            for venue, price in prices.items()]


class TestName:
    def test_name(self):
        s = BasisTradeStrategy()
        assert s.name == "basis_trade"


class TestParameters:
    def test_parameters(self):
        params = BasisTradeStrategy.parameters()
        assert "entry_basis_bps" in params
        assert "exit_basis_bps" in params
        assert "max_hold_hours" in params
        assert "position_size_usd" in params
        assert "candle_resolution_s" in params

    def test_parameter_types(self):
        params = BasisTradeStrategy.parameters()
        assert params["entry_basis_bps"]["type"] == "float"
        assert params["exit_basis_bps"]["type"] == "float"
        assert params["max_hold_hours"]["type"] == "int"
        assert params["position_size_usd"]["type"] == "float"

    def test_parameter_defaults(self):
        params = BasisTradeStrategy.parameters()
        assert params["entry_basis_bps"]["default"] == 30.0
        assert params["exit_basis_bps"]["default"] == 5.0
        assert params["max_hold_hours"]["default"] == 12
        assert params["position_size_usd"]["default"] == 1000.0


class TestReset:
    def test_reset(self):
        s = BasisTradeStrategy()
        s._entry_ts = 99999
        s._long_venue = "drift"
        s._short_venue = "hyperliquid"
        s.reset()
        assert s._entry_ts == 0
        assert s._long_venue == ""
        assert s._short_venue == ""

    def test_reset_initial_state(self):
        s = BasisTradeStrategy()
        assert s._entry_ts == 0
        assert s._long_venue == ""
        assert s._short_venue == ""


class TestEntryLogic:
    def test_no_entry_below_threshold(self):
        """Small price difference (below entry_basis_bps) → no orders."""
        s = BasisTradeStrategy(
            entry_basis_bps=30.0,
            venues=["drift", "hyperliquid"],
        )
        # drift=100, hyperliquid=100.1 → ~10bps, below 30bps threshold
        candles = _venue_candles({"drift": 100.0, "hyperliquid": 100.1})
        ctx = _make_ctx({"SOL-PERP": candles})
        current = _candle(close=100.0, ts=1000)

        signal = s.on_candle(current, [current], ctx=ctx)
        assert signal == Signal.HOLD
        ctx.market_order.assert_not_called()

    def test_entry_above_threshold(self):
        """66bps diff → 2 market_order calls (long cheap + short expensive)."""
        s = BasisTradeStrategy(
            entry_basis_bps=30.0,
            position_size_usd=1000.0,
            venues=["drift", "hyperliquid"],
        )
        # drift=100, hyperliquid=100.66 → ~66bps
        drift_price = 100.0
        hl_price = 100.66
        candles = _venue_candles({"drift": drift_price, "hyperliquid": hl_price})
        ctx = _make_ctx({"SOL-PERP": candles})
        current = _candle(close=drift_price, ts=1000)

        signal = s.on_candle(current, [current], ctx=ctx)
        assert signal == Signal.HOLD
        assert ctx.market_order.call_count == 2

    def test_entry_longs_cheaper_venue(self):
        """The cheaper venue gets the LONG leg."""
        s = BasisTradeStrategy(
            entry_basis_bps=30.0,
            position_size_usd=1000.0,
            venues=["drift", "hyperliquid"],
        )
        # drift is cheaper
        candles = _venue_candles({"drift": 100.0, "hyperliquid": 100.66})
        ctx = _make_ctx({"SOL-PERP": candles})
        current = _candle(close=100.0, ts=1000)

        s.on_candle(current, [current], ctx=ctx)

        calls = ctx.market_order.call_args_list
        sides = {c[1]["venue"]: c[0][1] for c in calls}
        assert sides["drift"] == Side.LONG
        assert sides["hyperliquid"] == Side.SHORT

    def test_entry_sets_state(self):
        """Successful entry sets _entry_ts, _long_venue, _short_venue."""
        s = BasisTradeStrategy(entry_basis_bps=30.0, venues=["drift", "hyperliquid"])
        candles = _venue_candles({"drift": 100.0, "hyperliquid": 100.66})
        ctx = _make_ctx({"SOL-PERP": candles})
        current = _candle(close=100.0, ts=5000)

        s.on_candle(current, [current], ctx=ctx)
        assert s._entry_ts == 5000
        assert s._long_venue != ""
        assert s._short_venue != ""

    def test_no_entry_without_ctx(self):
        """ctx=None → HOLD, no orders."""
        s = BasisTradeStrategy()
        current = _candle()
        signal = s.on_candle(current, [current], ctx=None)
        assert signal == Signal.HOLD

    def test_no_entry_with_only_one_venue(self):
        """Only one venue available → can't compute basis → HOLD."""
        s = BasisTradeStrategy(entry_basis_bps=5.0, venues=["drift", "hyperliquid"])
        # Only drift candle provided
        candles = [_candle(close=100.0, ts=1000, venue="drift")]
        ctx = _make_ctx({"SOL-PERP": candles})
        current = _candle(close=100.0, ts=1000)

        signal = s.on_candle(current, [current], ctx=ctx)
        assert signal == Signal.HOLD
        ctx.market_order.assert_not_called()


class TestExitLogic:
    def _enter(self, s, market="SOL-PERP", ts=1000):
        """Manually set entry state."""
        s._entry_ts = ts
        s._long_venue = "drift"
        s._short_venue = "hyperliquid"

    def test_exit_on_max_hold(self):
        """Position held beyond max_hold_hours → both legs closed."""
        s = BasisTradeStrategy(
            entry_basis_bps=30.0, max_hold_hours=4,
            venues=["drift", "hyperliquid"],
        )
        self._enter(s, ts=1000)

        # Candle 5 hours after entry
        candle = _candle(close=100.0, ts=1000 + 5 * 3600)
        # Basis is still wide so it's not the convergence exit
        candles = _venue_candles({"drift": 100.0, "hyperliquid": 100.66})
        ctx = _make_ctx({"SOL-PERP": candles})

        signal = s.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        assert ctx.close_position.call_count == 2
        assert s._entry_ts == 0

    def test_exit_on_convergence(self):
        """Basis converges below exit_basis_bps → both legs closed."""
        s = BasisTradeStrategy(
            entry_basis_bps=30.0, exit_basis_bps=5.0,
            max_hold_hours=24, venues=["drift", "hyperliquid"],
        )
        self._enter(s, ts=1000)

        # drift and hyperliquid are now nearly the same price → ~2bps
        candle = _candle(close=100.0, ts=1000 + 1 * 3600)
        candles = _venue_candles({"drift": 100.0, "hyperliquid": 100.002})
        ctx = _make_ctx({"SOL-PERP": candles})

        signal = s.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        assert ctx.close_position.call_count == 2
        assert s._entry_ts == 0

    def test_no_exit_while_basis_still_wide(self):
        """Basis still wide and hold time not exceeded → no exit."""
        s = BasisTradeStrategy(
            entry_basis_bps=30.0, exit_basis_bps=5.0,
            max_hold_hours=24, venues=["drift", "hyperliquid"],
        )
        self._enter(s, ts=1000)

        candle = _candle(close=100.0, ts=1000 + 1 * 3600)
        candles = _venue_candles({"drift": 100.0, "hyperliquid": 100.66})
        ctx = _make_ctx({"SOL-PERP": candles})

        signal = s.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        ctx.close_position.assert_not_called()
