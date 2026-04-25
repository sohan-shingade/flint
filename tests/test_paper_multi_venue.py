"""Tests for multi-venue paper trading."""
from flint.execution.paper_broker import PaperBroker
from flint.execution.live_context import LiveContext


def test_single_venue_default():
    """Without capital_allocation, should work as before."""
    broker = PaperBroker(initial_capital=10000)
    assert broker._allocator is None
    assert broker.cash == 10000
    assert broker.venue_balance("drift") == 10000


def test_multi_venue_allocation():
    """With capital_allocation, should track per-venue balances."""
    broker = PaperBroker(
        initial_capital=8000,
        capital_allocation={"drift": 5000, "hyperliquid": 3000},
    )
    assert broker._allocator is not None
    assert broker.venue_balance("drift") == 5000
    assert broker.venue_balance("hyperliquid") == 3000
    assert broker.cash == 8000  # total


def test_multi_venue_equity():
    broker = PaperBroker(
        initial_capital=8000,
        capital_allocation={"drift": 5000, "hyperliquid": 3000},
    )
    assert broker.equity == 8000


def test_venue_balances_dict():
    broker = PaperBroker(
        initial_capital=8000,
        capital_allocation={"drift": 5000, "hyperliquid": 3000},
    )
    balances = broker.venue_balances()
    assert balances["drift"] == 5000
    assert balances["hyperliquid"] == 3000


def test_single_venue_balances():
    broker = PaperBroker(initial_capital=10000, venue="drift")
    balances = broker.venue_balances()
    assert "drift" in balances
    assert balances["drift"] == 10000


def test_live_context_venue_balance():
    broker = PaperBroker(
        initial_capital=8000,
        capital_allocation={"drift": 5000, "hyperliquid": 3000},
    )
    ctx = LiveContext(broker)
    assert ctx.venue_balance("drift") == 5000
    assert ctx.venue_balance("hyperliquid") == 3000


def test_live_context_venue_balances():
    broker = PaperBroker(
        initial_capital=8000,
        capital_allocation={"drift": 5000, "hyperliquid": 3000},
    )
    ctx = LiveContext(broker)
    balances = ctx.venue_balances()
    assert balances["drift"] == 5000


def test_live_context_transfer():
    broker = PaperBroker(
        initial_capital=8000,
        capital_allocation={"drift": 5000, "hyperliquid": 3000},
    )
    ctx = LiveContext(broker)
    result = ctx.transfer("drift", "hyperliquid", 1000)
    assert result is True
    # Drift balance decreased, hyper will increase after processing
    assert ctx.venue_balance("drift") == 4000 - 1.0  # minus transfer cost


def test_transfer_without_allocator():
    broker = PaperBroker(initial_capital=10000)
    ctx = LiveContext(broker)
    result = ctx.transfer("drift", "hyperliquid", 1000)
    assert result is False


def test_position_has_venue_field():
    """New positions should include a venue field."""
    from flint.models import Candle, Order, OrderType, Side
    broker = PaperBroker(initial_capital=10000)
    order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                  size=10, order_id="t1", ts=1000)
    broker.submit_order(order)
    candle = Candle(market="SOL-PERP", resolution_s=3600, ts=1000,
                    open=100, high=101, low=99, close=100, volume=1000)
    broker.process_candle(candle)
    assert "SOL-PERP" in broker.positions
    pos = broker.positions["SOL-PERP"]
    assert "venue" in pos
    assert pos["venue"] == "drift"  # default venue
