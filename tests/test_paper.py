"""Tests for paper trading — PaperBroker, LiveContext, PaperTradingEngine, API."""
from __future__ import annotations


from flint.execution.fee_models import ZeroFeeModel
from flint.execution.live_context import LiveContext
from flint.execution.paper_broker import PaperBroker
from flint.models import Candle, Order, OrderType, Side, Signal
from flint.strategy import Strategy


def _c(ts, close, high=0, low=0, market="SOL-PERP"):
    h = high or close + 1
    l_ = low or close - 1
    return Candle(ts=ts, open=close, high=h, low=l_, close=close,
                  volume=100, market=market, resolution_s=3600)


class SimpleStrategy(Strategy):
    @property
    def name(self):
        return "Simple"

    def reset(self):
        pass

    def on_candle(self, candle, history, ctx=None):
        return Signal.HOLD


# --- PaperBroker Tests ---

class TestPaperBroker:
    def test_initial_state(self):
        broker = PaperBroker(10000, fee_model=ZeroFeeModel())
        assert broker.equity == 10000
        assert broker.cash == 10000
        assert len(broker.positions) == 0

    def test_market_order_fills(self):
        broker = PaperBroker(10000, fee_model=ZeroFeeModel())
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=10, order_id="o1")
        broker.submit_order(order)
        candle = _c(1000, 100.0)
        fills = broker.process_candle(candle)
        assert len(fills) == 1
        assert "SOL-PERP" in broker.positions
        assert broker.positions["SOL-PERP"]["size"] == 10

    def test_close_position_pnl(self):
        broker = PaperBroker(10000, fee_model=ZeroFeeModel())
        # Open
        broker.submit_order(Order(market="SOL-PERP", side=Side.LONG,
                                  order_type=OrderType.MARKET, size=10, order_id="o1"))
        broker.process_candle(_c(1000, 100.0))
        # Close
        broker.submit_order(Order(market="SOL-PERP", side=Side.SHORT,
                                  order_type=OrderType.MARKET, size=10, order_id="o2"))
        broker.process_candle(_c(2000, 110.0))
        assert len(broker.positions) == 0
        assert len(broker.closed_trades) == 1
        # SlippageFill(5bps): entry ~100.05, exit ~109.945 → pnl ~98.95
        import pytest
        assert broker.closed_trades[0]["pnl"] == pytest.approx(98.95, rel=1e-3)

    def test_stop_loss_triggers(self):
        broker = PaperBroker(10000, fee_model=ZeroFeeModel())
        # Open long
        broker.submit_order(Order(market="SOL-PERP", side=Side.LONG,
                                  order_type=OrderType.MARKET, size=10, order_id="o1"))
        broker.process_candle(_c(1000, 100.0))
        # Place stop at 95
        broker.submit_order(Order(market="SOL-PERP", side=Side.SHORT,
                                  order_type=OrderType.STOP_LOSS, size=10,
                                  price=95.0, order_id="o2"))
        # Price drops to 94
        fills = broker.process_candle(_c(2000, 96.0, low=94.0))
        assert len(fills) == 1
        assert len(broker.positions) == 0

    def test_limit_order_fills(self):
        broker = PaperBroker(10000, fee_model=ZeroFeeModel())
        broker.submit_order(Order(market="SOL-PERP", side=Side.LONG,
                                  order_type=OrderType.LIMIT, size=10,
                                  price=98.0, order_id="o1"))
        # Price doesn't reach
        fills1 = broker.process_candle(_c(1000, 100.0, low=99.0))
        assert len(fills1) == 0
        # Price reaches
        fills2 = broker.process_candle(_c(2000, 100.0, low=97.0))
        assert len(fills2) == 1
        assert fills2[0].price == 98.0

    def test_cancel_order(self):
        broker = PaperBroker(10000)
        broker.submit_order(Order(market="SOL-PERP", side=Side.LONG,
                                  order_type=OrderType.LIMIT, size=10,
                                  price=95.0, order_id="o1"))
        assert broker.cancel_order("o1") is True
        assert len(broker.pending_orders) == 0

    def test_unrealized_pnl_updates(self):
        import pytest
        broker = PaperBroker(10000, fee_model=ZeroFeeModel())
        broker.submit_order(Order(market="SOL-PERP", side=Side.LONG,
                                  order_type=OrderType.MARKET, size=10, order_id="o1"))
        broker.process_candle(_c(1000, 100.0))
        broker.process_candle(_c(2000, 105.0))
        # SlippageFill(5bps): entry ~100.05 → unrealized ~49.5, equity ~10049.5
        assert broker.positions["SOL-PERP"]["unrealized_pnl"] == pytest.approx(49.5, rel=1e-3)
        assert broker.equity == pytest.approx(10049.5, rel=1e-3)


# --- LiveContext Tests ---

class TestLiveContext:
    def test_wraps_broker(self):
        broker = PaperBroker(10000, fee_model=ZeroFeeModel())
        ctx = LiveContext(broker)
        assert ctx.account.equity == 10000
        assert ctx.positions == []

    def test_market_order_via_context(self):
        broker = PaperBroker(10000, fee_model=ZeroFeeModel())
        ctx = LiveContext(broker)
        candle = _c(1000, 100.0)
        ctx.set_candle(candle)
        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        broker.process_candle(candle)
        assert len(ctx.positions) == 1

    def test_close_position_via_context(self):
        broker = PaperBroker(10000, fee_model=ZeroFeeModel())
        ctx = LiveContext(broker)
        ctx.set_candle(_c(1000, 100.0))
        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        broker.process_candle(_c(1000, 100.0))
        ctx.set_candle(_c(2000, 110.0))
        ctx.close_position("SOL-PERP")
        broker.process_candle(_c(2000, 110.0))
        assert len(ctx.positions) == 0


# --- PaperTradingEngine Tests ---

class TestPaperEngine:
    def test_start_and_stop_session(self):
        from flint.paper.engine import PaperTradingEngine
        from flint.store import FlintStore

        store = FlintStore(":memory:")
        engine = PaperTradingEngine(store)
        strategy = SimpleStrategy()
        sid = engine.start_session(strategy, "SOL-PERP", 3600, 10000)
        assert sid in engine.sessions
        status = engine.get_status(sid)
        assert status["status"] in ("running", "live", "replaying")
        assert status["equity"] == 10000

        ok = engine.stop_session(sid)
        assert ok is True
        assert engine.sessions[sid].status == "stopped"
        store.close()

    def test_list_sessions(self):
        from flint.paper.engine import PaperTradingEngine
        from flint.store import FlintStore

        store = FlintStore(":memory:")
        engine = PaperTradingEngine(store)
        engine.start_session(SimpleStrategy(), "SOL-PERP")
        engine.start_session(SimpleStrategy(), "BTC-PERP")
        sessions = engine.list_sessions()
        assert len(sessions) == 2
        store.close()

    def test_kill_session(self):
        from flint.paper.engine import PaperTradingEngine
        from flint.store import FlintStore

        store = FlintStore(":memory:")
        engine = PaperTradingEngine(store)
        sid = engine.start_session(SimpleStrategy(), "SOL-PERP")
        ok = engine.kill_session(sid)
        assert ok is True
        assert engine.sessions[sid].status == "killed"
        store.close()

    def test_nonexistent_session(self):
        from flint.paper.engine import PaperTradingEngine
        from flint.store import FlintStore

        store = FlintStore(":memory:")
        engine = PaperTradingEngine(store)
        assert engine.get_status("nope") is None
        assert engine.stop_session("nope") is False
        store.close()
