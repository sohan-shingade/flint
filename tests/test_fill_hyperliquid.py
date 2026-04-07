"""Tests for HyperliquidFillModel.

Covers:
- Orderbook walk for exact fill (no remainder)
- HLP backstop when book is exhausted
- Synthetic book fallback when no snapshot is set
- Limit order fills (long and short)
- Limit order non-fill (price not crossed)
- HLP price direction correctness for short orders
- set_orderbook with None clears to synthetic fallback
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import pytest

from flint.execution.fill_hyperliquid import HyperliquidFillModel
from flint.models import (
    Candle,
    Fill,
    Order,
    OrderbookLevel,
    OrderbookSnapshot,
    OrderType,
    Side,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candle(p: float = 100.0, volume: float = 10_000.0, market: str = "SOL-PERP") -> Candle:
    return Candle(
        ts=1_700_000_000,
        open=p,
        high=p + 1,
        low=p - 1,
        close=p,
        volume=volume,
        market=market,
        resolution_s=3600,
        venue="pyth",
    )


def _order(
    side: Side = Side.LONG,
    size: float = 10.0,
    order_type: OrderType = OrderType.MARKET,
    price: float = 0.0,
    market: str = "SOL-PERP",
) -> Order:
    return Order(
        market=market,
        side=side,
        order_type=order_type,
        size=size,
        price=price,
        order_id="t1",
        ts=1_700_000_000,
        venue="hyperliquid",
    )


def _book(
    asks: Optional[List[Tuple[float, float]]] = None,
    bids: Optional[List[Tuple[float, float]]] = None,
    market: str = "SOL-PERP",
) -> OrderbookSnapshot:
    ask_levels = tuple(OrderbookLevel(p, s) for p, s in (asks or []))
    bid_levels = tuple(OrderbookLevel(p, s) for p, s in (bids or []))
    return OrderbookSnapshot(market=market, ts=1_700_000_000, bids=bid_levels, asks=ask_levels)


# ---------------------------------------------------------------------------
# Market order — orderbook walk
# ---------------------------------------------------------------------------

def test_walks_orderbook_exact_fill():
    """Order fills entirely within the first ask level, no HLP needed."""
    m = HyperliquidFillModel()
    m.set_orderbook(_book(asks=[(100.1, 50.0), (100.2, 100.0)],
                          bids=[(99.9, 50.0), (99.8, 100.0)]))
    f = m.fill_market(_order(size=10.0), _candle())
    assert f is not None
    assert f.price == pytest.approx(100.1)
    assert f.size == pytest.approx(10.0)
    assert f.venue == "hyperliquid"


def test_walks_multiple_ask_levels():
    """Order spanning two ask levels gets a volume-weighted average price."""
    m = HyperliquidFillModel()
    m.set_orderbook(_book(asks=[(100.0, 5.0), (101.0, 10.0)],
                          bids=[(99.0, 50.0)]))
    # Buy 10: 5 @ 100.0, 5 @ 101.0 → avg = 100.5
    f = m.fill_market(_order(size=10.0), _candle())
    assert f is not None
    assert f.price == pytest.approx(100.5)
    assert f.size == pytest.approx(10.0)


def test_walks_bid_levels_for_short():
    """Short market order walks the bid side."""
    m = HyperliquidFillModel()
    m.set_orderbook(_book(asks=[(100.1, 100.0)],
                          bids=[(99.9, 5.0), (99.8, 10.0)]))
    # Sell 5: fills at 99.9 exactly
    f = m.fill_market(_order(side=Side.SHORT, size=5.0), _candle())
    assert f is not None
    assert f.price == pytest.approx(99.9)
    assert f.size == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# HLP backstop
# ---------------------------------------------------------------------------

def test_hlp_backstop_fills_remainder():
    """When book depth is insufficient, HLP fills the remainder at a worse price.

    5 units fill at book ask 100.1; the remaining 15 go to HLP at close*(1+impact),
    which with volume=10_000 gives a tiny premium over close (100.0).
    The VWAP of (5*100.1 + 15*~100.0) / 20 lands between 100.0 and 100.1.
    What matters is the full order size is filled and the average is above close.
    """
    m = HyperliquidFillModel()
    m.set_orderbook(_book(asks=[(100.1, 5.0)],
                          bids=[(99.9, 5.0)]))
    f = m.fill_market(_order(size=20.0), _candle())
    assert f is not None
    assert f.size == pytest.approx(20.0)
    # Weighted avg is above close (100.0) because book fill at 100.1 pulls it up
    assert f.price > 100.0


def test_hlp_backstop_short_price_is_lower():
    """For short orders, HLP backstop price is lower than close (worse for seller).

    2 units fill at best bid 99.9; the remaining 8 go to HLP at close*(1-impact).
    With close=100.0 and volume=10_000 the HLP price is ~99.9996, just below close.
    The VWAP of (2*99.9 + 8*~99.9996)/10 ≈ 99.98 — below close but above 99.9.
    The key invariant: fill price is strictly below candle close (100.0).
    """
    m = HyperliquidFillModel()
    m.set_orderbook(_book(asks=[(100.1, 100.0)],
                          bids=[(99.9, 2.0)]))
    candle = _candle(p=100.0)
    f = m.fill_market(_order(side=Side.SHORT, size=10.0), candle)
    assert f is not None
    assert f.size == pytest.approx(10.0)
    # Weighted avg must be strictly below close because both book fill (99.9)
    # and HLP fill (close*(1-impact) < close) are below close.
    assert f.price < candle.close


def test_hlp_backstop_entire_order():
    """Empty orderbook: entire order filled by HLP."""
    m = HyperliquidFillModel()
    m.set_orderbook(_book(asks=[], bids=[]))
    candle = _candle(p=100.0, volume=1_000.0)
    f = m.fill_market(_order(size=10.0), candle)
    assert f is not None
    assert f.size == pytest.approx(10.0)
    # HLP long price: close * (1 + k * 10/1000) = 100 * 1.00005
    expected_price = 100.0 * (1 + 0.005 * 10.0 / 1_000.0)
    assert f.price == pytest.approx(expected_price)


# ---------------------------------------------------------------------------
# Synthetic fallback
# ---------------------------------------------------------------------------

def test_synthetic_fallback_when_no_book_set():
    """No orderbook set → falls back to synthetic depth, still returns a fill."""
    m = HyperliquidFillModel()
    f = m.fill_market(_order(), _candle())
    assert f is not None
    assert f.size > 0
    assert f.venue == "hyperliquid"


def test_synthetic_fallback_after_none_set():
    """set_orderbook(None) should trigger synthetic fallback on next fill."""
    m = HyperliquidFillModel()
    m.set_orderbook(_book(asks=[(100.1, 5.0)], bids=[(99.9, 5.0)]))
    m.set_orderbook(None)  # clear
    f = m.fill_market(_order(), _candle())
    assert f is not None


def test_synthetic_fallback_wrong_market():
    """Book for a different market is treated as missing → synthetic fallback."""
    m = HyperliquidFillModel()
    m.set_orderbook(_book(asks=[(100.1, 50.0)], bids=[(99.9, 50.0)], market="BTC-PERP"))
    f = m.fill_market(_order(market="SOL-PERP"), _candle(market="SOL-PERP"))
    assert f is not None  # still fills via synthetic book


# ---------------------------------------------------------------------------
# Limit orders
# ---------------------------------------------------------------------------

def test_limit_long_fills_when_low_crosses():
    """Long limit order fills when candle.low <= limit price."""
    m = HyperliquidFillModel()
    order = _order(side=Side.LONG, order_type=OrderType.LIMIT, price=99.5, size=5.0)
    candle = _candle(p=100.0)  # low = 99.0
    f = m.fill_limit(order, candle)
    assert f is not None
    assert f.price == pytest.approx(99.5)
    assert f.size == pytest.approx(5.0)
    assert f.venue == "hyperliquid"


def test_limit_long_no_fill_when_low_above_price():
    """Long limit order does not fill when candle.low > limit price."""
    m = HyperliquidFillModel()
    order = _order(side=Side.LONG, order_type=OrderType.LIMIT, price=98.0, size=5.0)
    candle = _candle(p=100.0)  # low = 99.0, so 99.0 > 98.0 → no cross
    f = m.fill_limit(order, candle)
    assert f is None


def test_limit_short_fills_when_high_crosses():
    """Short limit order fills when candle.high >= limit price."""
    m = HyperliquidFillModel()
    order = _order(side=Side.SHORT, order_type=OrderType.LIMIT, price=100.5, size=5.0)
    candle = _candle(p=100.0)  # high = 101.0
    f = m.fill_limit(order, candle)
    assert f is not None
    assert f.price == pytest.approx(100.5)
    assert f.venue == "hyperliquid"


def test_limit_short_no_fill_when_high_below_price():
    """Short limit order does not fill when candle.high < limit price."""
    m = HyperliquidFillModel()
    order = _order(side=Side.SHORT, order_type=OrderType.LIMIT, price=102.0, size=5.0)
    candle = _candle(p=100.0)  # high = 101.0 < 102.0
    f = m.fill_limit(order, candle)
    assert f is None


# ---------------------------------------------------------------------------
# Impact coefficient
# ---------------------------------------------------------------------------

def test_custom_impact_coefficient():
    """Higher impact coefficient produces larger HLP price deviation."""
    candle = _candle(p=100.0, volume=1_000.0)
    size = 100.0  # entire order goes to HLP (empty book)

    m_low = HyperliquidFillModel(impact_coefficient=0.001)
    m_low.set_orderbook(_book(asks=[], bids=[]))
    f_low = m_low.fill_market(_order(size=size), candle)

    m_high = HyperliquidFillModel(impact_coefficient=0.01)
    m_high.set_orderbook(_book(asks=[], bids=[]))
    f_high = m_high.fill_market(_order(size=size), candle)

    assert f_high.price > f_low.price  # higher coefficient → worse price for buyer
