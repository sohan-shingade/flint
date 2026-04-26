"""Tests for multi-venue paper trading."""
from flint.paper.context import PaperContext
from flint.execution._position import _Position
from flint.models import Side


def _seed_position(ctx, market, side, size, entry_price=100.0, entry_ts=1000,
                   venue="drift", mark_price=None):
    """Helper — write a position directly into the PaperContext's
    PositionManager. Replaces the legacy `broker.positions[market] = {...}`
    dict-write pattern."""
    side_enum = side if isinstance(side, Side) else (
        Side.LONG if side == "long" else Side.SHORT
    )
    pos = _Position(
        market=market, side=side_enum, size=size,
        entry_price=entry_price, entry_ts=entry_ts, venue=venue,
    )
    pos.unrealized_pnl = 0.0
    pos.mark_price = mark_price if mark_price is not None else entry_price
    ctx._pm.set((venue, market), pos)
    return pos


def test_single_venue_default():
    """Without capital_allocation, should work as before."""
    ctx = PaperContext(initial_capital=10000)
    assert ctx._cm.allocator is None
    assert ctx.cash == 10000
    assert ctx.venue_balance("drift") == 10000


def test_multi_venue_allocation():
    """With capital_allocation, should track per-venue balances."""
    ctx = PaperContext(
        initial_capital=8000,
        capital_allocation={"drift": 5000, "hyperliquid": 3000},
    )
    assert ctx._cm.allocator is not None
    assert ctx.venue_balance("drift") == 5000
    assert ctx.venue_balance("hyperliquid") == 3000
    assert ctx.cash == 8000  # total


def test_multi_venue_equity():
    ctx = PaperContext(
        initial_capital=8000,
        capital_allocation={"drift": 5000, "hyperliquid": 3000},
    )
    assert ctx.equity == 8000


def test_venue_balances_dict():
    ctx = PaperContext(
        initial_capital=8000,
        capital_allocation={"drift": 5000, "hyperliquid": 3000},
    )
    balances = ctx.venue_balances()
    assert balances["drift"] == 5000
    assert balances["hyperliquid"] == 3000


def test_single_venue_balances():
    ctx = PaperContext(initial_capital=10000, venue="drift")
    balances = ctx.venue_balances()
    assert "drift" in balances
    assert balances["drift"] == 10000


def test_live_context_venue_balance():
    ctx = PaperContext(
        initial_capital=8000,
        capital_allocation={"drift": 5000, "hyperliquid": 3000},
    )
    assert ctx.venue_balance("drift") == 5000
    assert ctx.venue_balance("hyperliquid") == 3000


def test_live_context_venue_balances():
    ctx = PaperContext(
        initial_capital=8000,
        capital_allocation={"drift": 5000, "hyperliquid": 3000},
    )
    balances = ctx.venue_balances()
    assert balances["drift"] == 5000


def test_live_context_transfer():
    ctx = PaperContext(
        initial_capital=8000,
        capital_allocation={"drift": 5000, "hyperliquid": 3000},
    )
    result = ctx.transfer("drift", "hyperliquid", 1000)
    assert result is True
    # Drift balance decreased, hyper will increase after processing
    assert ctx.venue_balance("drift") == 4000 - 1.0  # minus transfer cost


def test_transfer_without_allocator():
    ctx = PaperContext(initial_capital=10000)
    result = ctx.transfer("drift", "hyperliquid", 1000)
    assert result is False


def test_position_has_venue_field():
    """New positions should include a venue field. Pin venue=`drift`
    explicitly — post-v1.5.4 the default flipped to `hyperliquid` so
    legacy single-venue tests have to opt back in."""
    from flint.models import Candle, Order, OrderType, Side
    ctx = PaperContext(initial_capital=10000, venue="drift")
    order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                  size=10, order_id="t1", ts=1000)
    ctx.submit_order(order)
    candle = Candle(market="SOL-PERP", resolution_s=3600, ts=1000,
                    open=100, high=101, low=99, close=100, volume=1000)
    ctx.process_candle(candle)
    assert ("drift", "SOL-PERP") in ctx._pm
    pos = ctx.position_at("drift", "SOL-PERP")
    assert pos is not None
    assert pos.venue == "drift"


# ─── Multi-venue funding (Option A) ─────────────────────────────

def _build_session_with_legs(legs):
    """Helper — build a PaperSession with the supplied (venue, market,
    side, size) positions pre-loaded on the ctx."""
    from flint.paper.engine import PaperSession
    ctx = PaperContext(
        initial_capital=20_000,
        capital_allocation={"drift": 10_000, "hyperliquid": 10_000},
    )
    for venue, market, side, size in legs:
        _seed_position(ctx, market, side, size, venue=venue)
    session = PaperSession(
        session_id="s1", strategy=None, market=legs[0][1],
        resolution_s=3600, ctx=ctx,
    )
    session._last_funding_ts = 0
    return session, ctx


def test_engine_funding_loop_iterates_venues_in_book():
    """Engine helper queries each distinct venue in positions, not just
    `session.broker.venue`. Mock store to count calls."""
    from unittest.mock import MagicMock
    from flint.paper.engine import PaperTradingEngine

    session, ctx = _build_session_with_legs([
        ("drift", "SOL-PERP", "long", 50),
        ("hyperliquid", "BTC-PERP", "short", 1),
    ])
    # Adjust market attr so loop targets SOL-PERP. With positions keyed
    # by (venue, market), only the SOL-PERP/drift leg matches the loop's
    # market filter — the BTC/hyperliquid leg is on a different market
    # so it doesn't appear in `venues_in_book`. Add an HL leg on
    # SOL-PERP so both venues show up for the SOL-PERP loop.
    _seed_position(ctx, "SOL-PERP", Side.SHORT, 10, venue="hyperliquid")
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

    session, ctx = _build_session_with_legs([
        ("drift", "SOL-PERP", "long", 50),
    ])
    # Add an HL leg on a different market so the ctx has positions
    # on both venues but the loop's `session.market = SOL-PERP` only
    # applies SOL rates; the HL leg shouldn't move.
    _seed_position(
        ctx, "BTC-PERP", Side.SHORT, 1.0,
        entry_price=60_000.0, entry_ts=1000,
        venue="hyperliquid", mark_price=60_000.0,
    )
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

    cash_before = ctx.cash
    engine._apply_session_funding(session, ss=None, fallback_mark_price=100.0)

    # Drift leg: long 50 * 100 * 0.0002 = $1.00 paid → cash -1.0
    # HL "rate" for SOL-PERP must be skipped because no HL SOL leg
    expected_drift_payment = 50 * 100.0 * 0.0002
    assert abs(ctx.cash - (cash_before - expected_drift_payment)) < 1e-6
    # session._last_funding_ts advances to the latest applied row
    assert session._last_funding_ts == 1100


def test_engine_funding_loop_swallows_query_error():
    """A failing venue query must not break the other venues' path."""
    from unittest.mock import MagicMock
    from flint.paper.engine import PaperTradingEngine

    session, ctx = _build_session_with_legs([
        ("drift", "SOL-PERP", "long", 50),
        ("hyperliquid", "SOL-PERP", "short", 50),  # same market, opposite legs
    ])
    # Post-D-2.1.d: positions are keyed by (venue, market) so both legs
    # co-exist in the book. The Drift query raises but the HL query
    # succeeds — the test verifies the engine swallows the per-venue
    # error and still books the surviving venue's payment.
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
    # Still applied the HL rate (the surviving leg got its payment).
    assert session._last_funding_ts == 1100
