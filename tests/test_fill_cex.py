"""Tests for CEX fill models (Binance, OKX, Bybit)."""

from flint.execution.fill_cex import BinanceFillModel, OkxFillModel, BybitFillModel
from flint.models import Candle, Order, OrderbookLevel, OrderbookSnapshot, OrderType, Side


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candle(price=100.0):
    return Candle(1700000000, price, price + 1, price - 1, price, 10000.0, "SOL-PERP", 3600, "pyth")


def _make_order(side=Side.LONG, size=10.0):
    return Order(
        market="SOL-PERP", side=side, order_type=OrderType.MARKET, size=size,
        price=0.0, order_id="test-1", ts=1700000000, venue="binance",
    )


def _make_book(mid=100.0, depth_per_level=100.0, levels=5):
    bids = tuple(OrderbookLevel(mid - 0.1 * (i + 1), depth_per_level) for i in range(levels))
    asks = tuple(OrderbookLevel(mid + 0.1 * (i + 1), depth_per_level) for i in range(levels))
    return OrderbookSnapshot(market="SOL-PERP", ts=1700000000, bids=bids, asks=asks)


# ---------------------------------------------------------------------------
# Binance
# ---------------------------------------------------------------------------

def test_binance_fill_walks_book():
    model = BinanceFillModel()
    model.set_orderbook(_make_book())
    fill = model.fill_market(_make_order(), _make_candle())
    assert fill is not None
    assert fill.price > 100.0
    assert fill.size == 10.0


def test_binance_fill_uses_synthetic_when_no_book():
    model = BinanceFillModel()
    fill = model.fill_market(_make_order(), _make_candle())
    assert fill is not None
    assert fill.price > 100.0


def test_binance_venue_tag():
    model = BinanceFillModel()
    fill = model.fill_market(_make_order(), _make_candle())
    assert fill is not None
    assert fill.venue == "binance"


def test_binance_fee_applied():
    model = BinanceFillModel()
    model.set_orderbook(_make_book())
    fill = model.fill_market(_make_order(), _make_candle())
    assert fill is not None
    # Fee should be ~5bps of notional — just verify it's positive and small
    expected_fee_approx = fill.price * fill.size * 0.0005
    assert fill.fee > 0
    assert abs(fill.fee - expected_fee_approx) < 1e-6


# ---------------------------------------------------------------------------
# OKX
# ---------------------------------------------------------------------------

def test_okx_fill_model():
    model = OkxFillModel()
    model.set_orderbook(_make_book())
    fill = model.fill_market(_make_order(), _make_candle())
    assert fill is not None


def test_okx_fill_uses_synthetic_when_no_book():
    model = OkxFillModel()
    fill = model.fill_market(_make_order(), _make_candle())
    assert fill is not None
    assert fill.price > 100.0


def test_okx_venue_tag():
    model = OkxFillModel()
    fill = model.fill_market(_make_order(), _make_candle())
    assert fill is not None
    assert fill.venue == "okx"


# ---------------------------------------------------------------------------
# Bybit — IOC band
# ---------------------------------------------------------------------------

def test_bybit_ioc_price_band_caps_fill():
    model = BybitFillModel()
    # 1% band around close=100.0 → cap at 101.0 for buys
    # Level at 100.1 is within band (size=5), level at 102.0 is outside band
    thin_book = OrderbookSnapshot(
        market="SOL-PERP", ts=1700000000,
        bids=tuple([OrderbookLevel(99.0, 5.0)]),
        asks=tuple([OrderbookLevel(100.1, 5.0), OrderbookLevel(102.0, 100.0)]),
    )
    model.set_orderbook(thin_book)
    order = _make_order(side=Side.LONG, size=50.0)
    fill = model.fill_market(order, _make_candle())
    assert fill is not None
    assert fill.size < 50.0  # partial fill — the 102.0 level is outside the band


def test_bybit_ioc_full_fill_within_band():
    """When book has enough liquidity inside the band, fills completely."""
    model = BybitFillModel()
    # All levels are within the 1% band (100.1..100.5 vs cap 101.0)
    fat_book = _make_book(mid=100.0, depth_per_level=200.0, levels=5)
    model.set_orderbook(fat_book)
    fill = model.fill_market(_make_order(size=10.0), _make_candle())
    assert fill is not None
    assert fill.size == 10.0


def test_bybit_ioc_sell_price_band():
    """Short orders respect the lower 1% band (cap at 99.0 for close=100)."""
    model = BybitFillModel()
    # Bid at 99.5 (within band), bid at 98.5 (outside band)
    asymmetric_book = OrderbookSnapshot(
        market="SOL-PERP", ts=1700000000,
        bids=tuple([OrderbookLevel(99.5, 5.0), OrderbookLevel(98.5, 100.0)]),
        asks=tuple([OrderbookLevel(100.5, 100.0)]),
    )
    model.set_orderbook(asymmetric_book)
    order = _make_order(side=Side.SHORT, size=50.0)
    fill = model.fill_market(order, _make_candle())
    assert fill is not None
    assert fill.size < 50.0  # 98.5 level excluded by the band


def test_bybit_uses_synthetic_when_no_book():
    model = BybitFillModel()
    fill = model.fill_market(_make_order(), _make_candle())
    assert fill is not None


def test_bybit_venue_tag():
    model = BybitFillModel()
    fill = model.fill_market(_make_order(), _make_candle())
    assert fill is not None
    assert fill.venue == "bybit"


# ---------------------------------------------------------------------------
# Short order walks bid side (shared across models)
# ---------------------------------------------------------------------------

def test_short_order_walks_bid_side():
    model = BinanceFillModel()
    model.set_orderbook(_make_book())
    fill = model.fill_market(_make_order(side=Side.SHORT), _make_candle())
    assert fill is not None
    assert fill.price < 100.0


# ---------------------------------------------------------------------------
# Limit orders
# ---------------------------------------------------------------------------

def test_limit_buy_fills_when_low_crosses():
    model = BinanceFillModel()
    order = Order(
        market="SOL-PERP", side=Side.LONG, order_type=OrderType.LIMIT,
        size=5.0, price=99.5, order_id="lim-1", ts=1700000000, venue="binance",
    )
    candle = _make_candle(price=100.0)  # low = 99.0
    fill = model.fill_limit(order, candle)
    assert fill is not None
    assert fill.price == 99.5


def test_limit_buy_no_fill_when_low_above_limit():
    model = BinanceFillModel()
    order = Order(
        market="SOL-PERP", side=Side.LONG, order_type=OrderType.LIMIT,
        size=5.0, price=98.0, order_id="lim-2", ts=1700000000, venue="binance",
    )
    candle = _make_candle(price=100.0)  # low = 99.0; 99.0 > 98.0 → no cross
    fill = model.fill_limit(order, candle)
    assert fill is None


def test_limit_sell_fills_when_high_crosses():
    model = OkxFillModel()
    order = Order(
        market="SOL-PERP", side=Side.SHORT, order_type=OrderType.LIMIT,
        size=5.0, price=100.5, order_id="lim-3", ts=1700000000, venue="okx",
    )
    candle = _make_candle(price=100.0)  # high = 101.0
    fill = model.fill_limit(order, candle)
    assert fill is not None
    assert fill.price == 100.5


# ---------------------------------------------------------------------------
# Partial fill flag
# ---------------------------------------------------------------------------

def test_partial_fill_flagged_when_book_exhausted():
    """Fill is flagged is_partial=True when book depth is insufficient."""
    model = BinanceFillModel()
    thin_book = OrderbookSnapshot(
        market="SOL-PERP", ts=1700000000,
        bids=tuple([OrderbookLevel(99.9, 3.0)]),
        asks=tuple([OrderbookLevel(100.1, 3.0)]),
    )
    model.set_orderbook(thin_book)
    fill = model.fill_market(_make_order(size=10.0), _make_candle())
    assert fill is not None
    assert fill.size == 3.0
    assert fill.is_partial is True


def test_full_fill_not_flagged_partial():
    model = BinanceFillModel()
    model.set_orderbook(_make_book(depth_per_level=200.0))
    fill = model.fill_market(_make_order(size=10.0), _make_candle())
    assert fill is not None
    assert fill.is_partial is False


# ---------------------------------------------------------------------------
# Volume-scaled synthetic depth fallback
# ---------------------------------------------------------------------------

def _make_candle_vol(price=100.0, volume=10000.0):
    """Candle helper with explicit volume control."""
    return Candle(1700000000, price, price + 1, price - 1, price, volume, "SOL-PERP", 3600, "pyth")


def test_low_volume_bar_produces_worse_fills():
    """Low-volume bars should produce more slippage than high-volume bars.

    Uses a 5000 SOL order ($500K notional) so the walk exercises multiple
    levels and the depth difference between thin/thick books is visible.
    """
    model = BinanceFillModel()
    order = _make_order(side=Side.LONG, size=5000.0)

    # Low volume → thin book (scale floors near 0.05, ~$1M depth)
    fill_low = model.fill_market(order, _make_candle_vol(volume=100_000.0))
    # High volume → thick book (scale ~2.5, ~$50M depth)
    fill_high = model.fill_market(order, _make_candle_vol(volume=10_000_000.0))

    assert fill_low is not None and fill_high is not None
    # Buying: higher price = worse fill.  Low volume should give worse (higher) price.
    assert fill_low.price > fill_high.price


def test_low_volume_bar_worse_fills_sell_side():
    """Short orders: low-volume bars should give worse (lower) fill prices."""
    model = BinanceFillModel()
    order = _make_order(side=Side.SHORT, size=5000.0)

    fill_low = model.fill_market(order, _make_candle_vol(volume=100_000.0))
    fill_high = model.fill_market(order, _make_candle_vol(volume=10_000_000.0))

    assert fill_low is not None and fill_high is not None
    # Selling: lower price = worse fill.
    assert fill_low.price < fill_high.price


def test_participation_cap_produces_partial_fill():
    """Orders exceeding 10 % of bar volume should be partially filled."""
    model = BinanceFillModel()
    # 10 % of 50 SOL = 5 SOL max fill
    candle = _make_candle_vol(volume=50.0)
    order = _make_order(size=20.0)

    fill = model.fill_market(order, candle)
    assert fill is not None
    assert fill.size <= 50.0 * 0.10 + 1e-9  # at most 10 % of bar volume
    assert fill.is_partial is True


def test_participation_cap_full_fill_when_small_order():
    """Small orders relative to bar volume should fill completely."""
    model = BinanceFillModel()
    candle = _make_candle_vol(volume=100_000.0)
    order = _make_order(size=10.0)  # 10 / 100_000 = 0.01 % — well under cap

    fill = model.fill_market(order, candle)
    assert fill is not None
    assert fill.size == 10.0
    assert fill.is_partial is False


def test_zero_volume_uses_unscaled_profile():
    """When volume is zero, the static profile is used (no scaling, no cap)."""
    model = BinanceFillModel()
    candle = _make_candle_vol(volume=0.0)
    order = _make_order(size=10.0)

    fill = model.fill_market(order, candle)
    assert fill is not None
    assert fill.size == 10.0  # no participation cap applied


def test_volume_scaling_does_not_affect_real_orderbook():
    """When a real orderbook is set, volume scaling and participation cap
    should NOT be applied — only the synthetic fallback uses them."""
    model = BinanceFillModel()
    model.set_orderbook(_make_book(depth_per_level=200.0))
    candle = _make_candle_vol(volume=5.0)  # tiny volume — would cap to 0.5 SOL
    order = _make_order(size=10.0)

    fill = model.fill_market(order, candle)
    assert fill is not None
    assert fill.size == 10.0  # real book: no cap


def test_bybit_synthetic_fallback_applies_participation_cap():
    """Bybit synthetic path should respect both IOC band and participation cap."""
    model = BybitFillModel()
    candle = _make_candle_vol(volume=30.0)  # 10 % = 3 SOL max
    order = _make_order(size=20.0)

    fill = model.fill_market(order, candle)
    assert fill is not None
    assert fill.size <= 30.0 * 0.10 + 1e-9
    assert fill.is_partial is True


def test_bybit_real_book_no_participation_cap():
    """Bybit with a real book should not apply participation cap."""
    model = BybitFillModel()
    model.set_orderbook(_make_book(depth_per_level=200.0))
    candle = _make_candle_vol(volume=5.0)
    order = _make_order(size=10.0)

    fill = model.fill_market(order, candle)
    assert fill is not None
    assert fill.size == 10.0


def test_okx_volume_scaling():
    """OKX synthetic fills should also respect volume scaling."""
    model = OkxFillModel()
    order = _make_order(side=Side.LONG, size=5000.0)

    fill_low = model.fill_market(order, _make_candle_vol(volume=100_000.0))
    fill_high = model.fill_market(order, _make_candle_vol(volume=10_000_000.0))

    assert fill_low is not None and fill_high is not None
    assert fill_low.price > fill_high.price
