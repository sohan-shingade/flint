"""Integration tests for per-venue fill pipelines."""
from flint.models import Candle, Order, OrderbookLevel, OrderbookSnapshot, OrderType, Side
from flint.execution.fill_registry import create_fill_model


def _make_candles(n=5, price=100.0):
    return [
        Candle(1700000000 + i * 3600, price + i, price + i + 1,
               price + i - 1, price + i + 0.5, 10000.0, "SOL-PERP", 3600, "pyth")
        for i in range(n)
    ]


def _make_book(mid=100.0):
    return OrderbookSnapshot(
        market="SOL-PERP", ts=1700000000,
        bids=tuple([OrderbookLevel(mid - 0.1 * (i + 1), 100.0) for i in range(10)]),
        asks=tuple([OrderbookLevel(mid + 0.1 * (i + 1), 100.0) for i in range(10)]),
    )


def test_all_venues_produce_fills():
    """Every venue should produce a fill for a standard order."""
    venues = ["drift", "hyperliquid", "jupiter", "binance", "okx", "bybit"]
    candle = _make_candles(1)[0]
    book = _make_book(100.0)

    for venue in venues:
        kwargs = {"seed": 42} if venue in ("drift", "jupiter") else {}
        model = create_fill_model(venue, **kwargs)
        if hasattr(model, 'set_orderbook'):
            model.set_orderbook(book)
        if hasattr(model, 'set_candle_buffer'):
            model.set_candle_buffer(_make_candles(5))

        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=10.0, price=0.0, order_id=f"test-{venue}", ts=1700000000,
                      venue=venue)
        fill = model.fill_market(order, candle)
        assert fill is not None, f"{venue} failed to produce a fill"
        assert fill.size > 0, f"{venue} fill has zero size"
        assert fill.price > 0, f"{venue} fill has zero price"


def test_venue_fill_prices_differ():
    """Different venues should produce different fill prices.

    Each venue uses its own synthetic depth profile (no shared orderbook),
    so spread, concentration, and JIT mechanics produce distinct VWAP prices.
    """
    candle = _make_candles(1)[0]
    prices = {}

    for venue in ["drift", "hyperliquid", "binance"]:
        kwargs = {"seed": 42} if venue == "drift" else {}
        model = create_fill_model(venue, **kwargs)
        # Deliberately omit set_orderbook so each model falls back to its own
        # synthetic depth profile, which differs across venues.
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=100.0, price=0.0, order_id=f"test-{venue}", ts=1700000000,
                      venue=venue)
        fill = model.fill_market(order, candle)
        prices[venue] = fill.price

    unique_prices = set(round(p, 4) for p in prices.values())
    assert len(unique_prices) >= 2, f"Expected different prices, got {prices}"


def test_switching_venues_mid_strategy():
    """Close Drift position, open Jupiter position same bar."""
    drift_model = create_fill_model("drift", seed=42)
    jupiter_model = create_fill_model("jupiter", seed=42)
    candles = _make_candles(5)
    book = _make_book(100.0)

    drift_model.set_orderbook(book)
    jupiter_model.set_candle_buffer(candles)

    open_order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                       size=10.0, price=0.0, order_id="open-drift", ts=1700000000, venue="drift")
    close_order = Order(market="SOL-PERP", side=Side.SHORT, order_type=OrderType.MARKET,
                        size=10.0, price=0.0, order_id="close-drift", ts=1700003600, venue="drift")
    jupiter_order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                          size=10.0, price=0.0, order_id="open-jup", ts=1700003600, venue="jupiter")

    open_fill = drift_model.fill_market(open_order, candles[0])
    close_fill = drift_model.fill_market(close_order, candles[1])
    jup_fill = jupiter_model.fill_market(jupiter_order, candles[1])

    assert open_fill is not None
    assert close_fill is not None
    assert jup_fill is not None


def test_orderbook_gap_uses_synthetic():
    """When no orderbook set, CEX fill model uses synthetic depth."""
    model = create_fill_model("binance")
    # Don't set orderbook — synthetic depth should apply slippage above mid price
    candle = _make_candles(1)[0]
    order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                  size=10.0, price=0.0, order_id="test", ts=1700000000, venue="binance")
    fill = model.fill_market(order, candle)
    assert fill is not None
    assert fill.price > candle.close  # synthetic depth applies slippage above close


def test_short_orders_all_venues():
    """All venues should handle short orders correctly."""
    candle = _make_candles(1)[0]
    book = _make_book(100.0)

    for venue in ["drift", "hyperliquid", "jupiter", "binance", "okx", "bybit"]:
        kwargs = {"seed": 42} if venue in ("drift", "jupiter") else {}
        model = create_fill_model(venue, **kwargs)
        if hasattr(model, 'set_orderbook'):
            model.set_orderbook(book)
        if hasattr(model, 'set_candle_buffer'):
            model.set_candle_buffer(_make_candles(5))

        order = Order(market="SOL-PERP", side=Side.SHORT, order_type=OrderType.MARKET,
                      size=10.0, price=0.0, order_id=f"short-{venue}", ts=1700000000,
                      venue=venue)
        fill = model.fill_market(order, candle)
        assert fill is not None, f"{venue} failed short fill"
        assert fill.price > 0


def test_large_order_shows_more_slippage():
    """Larger orders should have more price impact across all book-based venues."""
    candle = _make_candles(1)[0]
    book = _make_book(100.0)

    for venue in ["binance", "hyperliquid"]:
        model_small = create_fill_model(venue)
        model_large = create_fill_model(venue)
        model_small.set_orderbook(book)
        model_large.set_orderbook(book)

        small_order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                            size=5.0, price=0.0, order_id="small", ts=1700000000, venue=venue)
        large_order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                            size=500.0, price=0.0, order_id="large", ts=1700000000, venue=venue)

        small_fill = model_small.fill_market(small_order, candle)
        large_fill = model_large.fill_market(large_order, candle)

        assert large_fill.price >= small_fill.price, \
            f"{venue}: large order ({large_fill.price}) should cost more than small ({small_fill.price})"
