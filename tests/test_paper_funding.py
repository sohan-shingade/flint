"""Tests for paper trading funding rate application."""
import os
import tempfile

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


def test_apply_funding_long_positive_rate():
    """Long pays when funding rate is positive."""
    ctx = PaperContext(initial_capital=10000)
    _seed_position(ctx, "SOL-PERP", Side.LONG, 100)
    payment = ctx.apply_funding("SOL-PERP", rate=0.0001, mark_price=100.0)
    assert abs(payment - 1.0) < 0.01  # 100 * 100 * 0.0001 = 1.0
    assert ctx.cash < 10000
    assert ctx.total_funding > 0


def test_apply_funding_short_positive_rate():
    """Short receives when funding rate is positive."""
    ctx = PaperContext(initial_capital=10000)
    _seed_position(ctx, "SOL-PERP", Side.SHORT, 100)
    payment = ctx.apply_funding("SOL-PERP", rate=0.0001, mark_price=100.0)
    assert payment < 0  # short receives
    assert ctx.cash > 10000


def test_apply_funding_no_position():
    ctx = PaperContext(initial_capital=10000)
    payment = ctx.apply_funding("SOL-PERP", rate=0.0001, mark_price=100.0)
    assert payment == 0.0
    assert ctx.cash == 10000


def test_apply_funding_negative_rate():
    """Negative rate = longs receive."""
    ctx = PaperContext(initial_capital=10000)
    _seed_position(ctx, "SOL-PERP", Side.LONG, 50)
    payment = ctx.apply_funding("SOL-PERP", rate=-0.0002, mark_price=100.0)
    assert payment < 0  # long receives when rate negative
    assert ctx.cash > 10000


def test_close_all_positions():
    ctx = PaperContext(initial_capital=10000)
    _seed_position(ctx, "SOL-PERP", Side.LONG, 10)
    ctx.close_all_positions({"SOL-PERP": 110.0})
    assert len(ctx._pm) == 0
    assert len(ctx.closed_trades) == 1
    assert ctx.closed_trades[0]["pnl"] == 100.0  # (110-100)*10


def test_limit_order_expires_after_timeout():
    """Limit orders should expire after N bars."""
    from flint.models import Order, OrderType, Side, Candle
    ctx = PaperContext(initial_capital=10000)
    ctx._limit_timeout_bars = 3
    ctx._resolution_s = 3600

    order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.LIMIT,
                  size=10, price=50.0, order_id="test-timeout", ts=1000)
    ctx.submit_order(order)
    assert len(ctx.pending_orders) == 1

    # Process 4 candles at higher prices (limit won't fill since price > 50)
    for i in range(4):
        candle = Candle(market="SOL-PERP", resolution_s=3600, ts=1000 + (i + 1) * 3600,
                        open=100, high=101, low=99, close=100, volume=1000)
        ctx.process_candle(candle)

    assert len(ctx.pending_orders) == 0  # expired after 3 bars


def test_market_orders_dont_expire():
    """Market orders should not be affected by timeout."""
    from flint.models import Order, OrderType, Side, Candle
    ctx = PaperContext(initial_capital=10000)
    ctx._limit_timeout_bars = 1

    order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                  size=10, order_id="test-market", ts=1000)
    ctx.submit_order(order)

    # Market order should fill on next candle, not expire
    candle = Candle(market="SOL-PERP", resolution_s=3600, ts=5000,
                    open=100, high=101, low=99, close=100, volume=1000)
    fills = ctx.process_candle(candle)
    assert len(fills) == 1  # filled, not expired


def test_funding_persistence():
    """Test funding payment save/load through session store."""
    from flint.store import FlintStore
    from flint.paper.session_store import PaperSessionStore
    db = os.path.join(tempfile.gettempdir(), "test_funding_persist.duckdb")
    try:
        store = FlintStore(db)
        ss = PaperSessionStore(store)
        ss.save_funding_payment("s1", 1000, "SOL-PERP", 0.0001, 1.0, 100.0, 100.0)
        payments = ss.get_funding_payments("s1")
        assert len(payments) == 1
        assert payments[0]["rate"] == 0.0001
        assert payments[0]["payment"] == 1.0
        assert payments[0]["venue"] == "unknown"  # default for back-compat
        store.close()
    finally:
        if os.path.exists(db):
            os.unlink(db)


def test_apply_funding_venue_filter_match():
    """venue= passed and matches position venue → applies normally."""
    ctx = PaperContext(initial_capital=10000)
    _seed_position(ctx, "SOL-PERP", Side.LONG, 100, venue="drift")
    payment = ctx.apply_funding(
        "SOL-PERP", rate=0.0001, mark_price=100.0, venue="drift",
    )
    assert abs(payment - 1.0) < 0.01
    assert ctx.cash < 10000


def test_apply_funding_venue_filter_skip():
    """venue= passed but doesn't match position venue → skip, cash unchanged."""
    ctx = PaperContext(initial_capital=10000)
    _seed_position(ctx, "SOL-PERP", Side.LONG, 100, venue="drift")
    payment = ctx.apply_funding(
        "SOL-PERP", rate=0.0001, mark_price=100.0, venue="hyperliquid",
    )
    assert payment == 0.0
    assert ctx.cash == 10000
    assert ctx.total_funding == 0


def test_apply_funding_venue_none_back_compat():
    """venue=None (default) → applies regardless of position venue.
    Single-venue path stays unchanged."""
    ctx = PaperContext(initial_capital=10000)
    _seed_position(ctx, "SOL-PERP", Side.LONG, 100, venue="hyperliquid")
    payment = ctx.apply_funding("SOL-PERP", rate=0.0001, mark_price=100.0)
    assert abs(payment - 1.0) < 0.01


def test_funding_persistence_multi_venue():
    """Two payments at the same ts on the same market, different venues
    must both persist (PK widening to (session_id, market, venue, ts))."""
    from flint.store import FlintStore
    from flint.paper.session_store import PaperSessionStore
    db = os.path.join(tempfile.gettempdir(), "test_funding_mv.duckdb")
    try:
        if os.path.exists(db):
            os.unlink(db)
        store = FlintStore(db)
        ss = PaperSessionStore(store)
        ss.save_funding_payment(
            "s1", 1000, "SOL-PERP", 0.0001, 1.0, 100.0, 100.0,
            venue="drift",
        )
        ss.save_funding_payment(
            "s1", 1000, "SOL-PERP", 0.00015, 1.5, 100.0, 100.0,
            venue="hyperliquid",
        )
        payments = ss.get_funding_payments("s1")
        assert len(payments) == 2
        venues = sorted(p["venue"] for p in payments)
        assert venues == ["drift", "hyperliquid"]
        store.close()
    finally:
        if os.path.exists(db):
            os.unlink(db)
