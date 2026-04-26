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


# ─── Multi-venue funding (Option A) ─────────────────────────────

def _build_session_with_legs(legs):
    """Helper — build a PaperSession with the supplied (venue, market,
    side, size) positions pre-loaded on the broker."""
    from flint.paper.engine import PaperSession
    broker = PaperBroker(
        initial_capital=20_000,
        capital_allocation={"drift": 10_000, "hyperliquid": 10_000},
    )
    for venue, market, side, size in legs:
        broker.positions[market] = {
            "market": market, "venue": venue, "side": side, "size": size,
            "entry_price": 100.0, "entry_ts": 1000,
            "unrealized_pnl": 0.0, "mark_price": 100.0,
        }
    ctx = LiveContext(broker)
    session = PaperSession(
        session_id="s1", strategy=None, market=legs[0][1],
        resolution_s=3600, broker=broker, ctx=ctx,
    )
    session._last_funding_ts = 0
    return session, broker


def test_engine_funding_loop_iterates_venues_in_book():
    """Engine helper queries each distinct venue in positions, not just
    `session.broker.venue`. Mock store to count calls."""
    from unittest.mock import MagicMock
    from flint.paper.engine import PaperTradingEngine

    session, broker = _build_session_with_legs([
        ("drift", "SOL-PERP", "long", 50),
        ("hyperliquid", "BTC-PERP", "short", 1),
    ])
    # Adjust market attr so loop targets SOL-PERP, but venues_in_book
    # picks up both drift + hyperliquid from the position dict
    session.market = "SOL-PERP"

    fake_store = MagicMock()
    fake_store.query_venue_funding.return_value = []
    engine = PaperTradingEngine.__new__(PaperTradingEngine)
    engine.store = fake_store

    engine._apply_session_funding(session, ss=None, fallback_mark_price=100.0)

    venues_called = sorted({
        c.args[0] for c in fake_store.query_venue_funding.call_args_list
    })
    assert venues_called == ["drift", "hyperliquid"]


def test_engine_funding_loop_applies_per_venue_rate():
    """Two legs on different venues + different rates → cash debit
    matches each leg's own rate. The Drift rate must not touch the HL
    leg and vice versa."""
    from unittest.mock import MagicMock
    from flint.paper.engine import PaperTradingEngine

    session, broker = _build_session_with_legs([
        ("drift", "SOL-PERP", "long", 50),
    ])
    # Add an HL leg on a different market so the broker has positions
    # on both venues but the loop's `session.market = SOL-PERP` only
    # applies SOL rates; the HL leg shouldn't move.
    broker.positions["BTC-PERP"] = {
        "market": "BTC-PERP", "venue": "hyperliquid", "side": "short",
        "size": 1.0, "entry_price": 60_000.0, "entry_ts": 1000,
        "unrealized_pnl": 0.0, "mark_price": 60_000.0,
    }
    session.market = "SOL-PERP"

    fake_store = MagicMock()

    def _q(venue, market, since, until):
        if venue == "drift" and market == "SOL-PERP":
            return [{"ts": 1100, "rate_hourly": 0.0002, "mark_price": 100.0}]
        if venue == "hyperliquid" and market == "SOL-PERP":
            # HL has no SOL leg; rate should *not* apply
            return [{"ts": 1100, "rate_hourly": 0.001, "mark_price": 100.0}]
        return []

    fake_store.query_venue_funding.side_effect = _q
    engine = PaperTradingEngine.__new__(PaperTradingEngine)
    engine.store = fake_store

    cash_before = broker.cash
    engine._apply_session_funding(session, ss=None, fallback_mark_price=100.0)

    # Drift leg: long 50 * 100 * 0.0002 = $1.00 paid → cash -1.0
    # HL "rate" for SOL-PERP must be skipped because no HL SOL leg
    expected_drift_payment = 50 * 100.0 * 0.0002
    assert abs(broker.cash - (cash_before - expected_drift_payment)) < 1e-6
    # session._last_funding_ts advances to the latest applied row
    assert session._last_funding_ts == 1100


def test_engine_funding_loop_swallows_query_error():
    """A failing venue query must not break the other venues' path."""
    from unittest.mock import MagicMock
    from flint.paper.engine import PaperTradingEngine

    session, broker = _build_session_with_legs([
        ("drift", "SOL-PERP", "long", 50),
        ("hyperliquid", "SOL-PERP", "short", 50),  # same market, opposite legs
    ])
    # NOTE: with broker.positions keyed by market only, the HL leg
    # collides with drift in the dict — Option B fixes this. For this
    # test, we just want to verify error swallowing on one venue
    # doesn't kill the other.
    session.market = "SOL-PERP"

    fake_store = MagicMock()

    def _q(venue, market, since, until):
        if venue == "drift":
            raise RuntimeError("drift store down")
        return [{"ts": 1100, "rate_hourly": 0.0001, "mark_price": 100.0}]

    fake_store.query_venue_funding.side_effect = _q
    engine = PaperTradingEngine.__new__(PaperTradingEngine)
    engine.store = fake_store

    # Should not raise
    engine._apply_session_funding(session, ss=None, fallback_mark_price=100.0)
    # Still applied the HL rate (even though gap-2 means it hits the
    # surviving leg in the dict)
    assert session._last_funding_ts == 1100
