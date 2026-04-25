"""OrderbookFillModel hardening — Phase 3 T3.1.

Tests:
- Single-level fill with slippage attribution.
- Multi-level VWAP fill across 3 levels.
- Rejection when order size exceeds aggregate book depth.
- `reject_on_insufficient_depth=False` preserves legacy partial-fill behavior.
- Fallback when no book data present.
"""
from __future__ import annotations

import pytest

from flint.execution.fill_models import OrderbookFillModel
from flint.models import (
    Candle,
    Order,
    OrderbookLevel,
    OrderbookSnapshot,
    OrderType,
    Side,
)


def _book(bids, asks, ts=1000):
    return OrderbookSnapshot(
        market="SOL-PERP", ts=ts,
        bids=tuple(OrderbookLevel(p, s) for p, s in bids),
        asks=tuple(OrderbookLevel(p, s) for p, s in asks),
    )


def _candle(ts=1000, close=100.0):
    return Candle(ts=ts, open=close, high=close + 0.5, low=close - 0.5,
                  close=close, volume=1000, market="SOL-PERP",
                  resolution_s=60)


def _order(side: Side, size: float) -> Order:
    return Order(market="SOL-PERP", side=side, size=size,
                 order_type=OrderType.MARKET, order_id="o1", ts=1000)


class TestSingleLevelFill:
    def test_buy_fills_at_best_ask(self):
        book = _book(
            bids=[(99.95, 10.0), (99.90, 20.0)],
            asks=[(100.05, 10.0), (100.10, 20.0)],
        )
        model = OrderbookFillModel()
        model.set_orderbook(book)
        fill = model.fill_market(_order(Side.LONG, 5.0), _candle())
        assert fill is not None
        assert fill.price == pytest.approx(100.05)
        assert fill.size == 5.0
        # Single-level at best ask → impact = (100.05 - 100.00) / 100 * 10000 = 5 bps
        assert fill.impact_bps == pytest.approx(5.0, abs=1e-6)


class TestMultiLevelVWAP:
    def test_buy_walks_three_levels(self):
        book = _book(
            bids=[(99.95, 10.0)],
            asks=[(100.00, 5.0), (100.05, 5.0), (100.10, 10.0)],
        )
        model = OrderbookFillModel()
        model.set_orderbook(book)
        # Take 12: all of L1 (5@100), all of L2 (5@100.05), 2 of L3 (2@100.10)
        fill = model.fill_market(_order(Side.LONG, 12.0), _candle())
        assert fill is not None
        expected_vwap = (5 * 100.00 + 5 * 100.05 + 2 * 100.10) / 12
        assert fill.price == pytest.approx(expected_vwap)
        assert fill.size == pytest.approx(12.0)
        assert fill.impact_bps > 0  # worse than mid

    def test_sell_walks_bid_side(self):
        book = _book(
            bids=[(99.95, 5.0), (99.90, 10.0)],
            asks=[(100.05, 10.0)],
        )
        model = OrderbookFillModel()
        model.set_orderbook(book)
        fill = model.fill_market(_order(Side.SHORT, 10.0), _candle())
        assert fill is not None
        expected_vwap = (5 * 99.95 + 5 * 99.90) / 10
        assert fill.price == pytest.approx(expected_vwap)
        assert fill.impact_bps > 0


class TestDepthRejection:
    def test_rejects_when_size_exceeds_depth_default(self):
        """Default reject_on_insufficient_depth=True: size > total ask depth
        must return None (reject)."""
        book = _book(
            bids=[(99.95, 10.0)],
            asks=[(100.05, 5.0), (100.10, 5.0)],  # 10 total
        )
        model = OrderbookFillModel()
        model.set_orderbook(book)
        fill = model.fill_market(_order(Side.LONG, 50.0), _candle())
        assert fill is None

    def test_underfills_when_reject_disabled(self):
        """Legacy behavior: reject_on_insufficient_depth=False → partial fill."""
        book = _book(
            bids=[(99.95, 10.0)],
            asks=[(100.05, 5.0)],
        )
        model = OrderbookFillModel(reject_on_insufficient_depth=False)
        model.set_orderbook(book)
        fill = model.fill_market(_order(Side.LONG, 50.0), _candle())
        assert fill is not None
        assert fill.size == pytest.approx(5.0)  # underfilled


class TestFallback:
    def test_no_book_falls_back_to_slippage(self):
        model = OrderbookFillModel(fallback_slippage_bps=5.0)
        # No book set → should use SlippageFill
        fill = model.fill_market(_order(Side.LONG, 1.0), _candle(close=100.0))
        assert fill is not None
        # SlippageFill buys at close * (1 + 5bps) = 100.05
        assert fill.price == pytest.approx(100.05, abs=1e-6)

    def test_market_mismatch_falls_back(self):
        """Book for a different market shouldn't be used."""
        book = _book(
            bids=[(99.95, 10.0)],
            asks=[(100.05, 10.0)],
        )
        model = OrderbookFillModel()
        model.set_orderbook(book)
        order = Order(market="BTC-PERP", side=Side.LONG, size=1.0,
                      order_type=OrderType.MARKET, order_id="o2", ts=1000)
        fill = model.fill_market(order, _candle())
        # Fall back to SlippageFill on close price
        assert fill is not None


class TestImpactAttribution:
    def test_impact_sign_long(self):
        """Long side: fill price > mid → positive impact_bps."""
        book = _book(
            bids=[(99.90, 10.0)],
            asks=[(100.10, 10.0)],
        )
        model = OrderbookFillModel()
        model.set_orderbook(book)
        fill = model.fill_market(_order(Side.LONG, 1.0), _candle())
        # mid = 100.00, fill = 100.10 → +10 bps
        assert fill.impact_bps == pytest.approx(10.0, abs=1e-6)

    def test_impact_sign_short(self):
        """Short side: fill price < mid → positive impact_bps (worse for taker)."""
        book = _book(
            bids=[(99.90, 10.0)],
            asks=[(100.10, 10.0)],
        )
        model = OrderbookFillModel()
        model.set_orderbook(book)
        fill = model.fill_market(_order(Side.SHORT, 1.0), _candle())
        # mid = 100.00, fill = 99.90 → +10 bps (worse for seller)
        assert fill.impact_bps == pytest.approx(10.0, abs=1e-6)
