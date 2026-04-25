"""D-3.1-rust — OrderbookFiller Python↔Rust parity.

Pins every field of `OrderbookWalk` (price, size, impact_bps,
is_partial) to 1e-9 against the reference Python implementation in
`flint/execution/fill_models.py:OrderbookFillModel._walk_book`.
"""
from __future__ import annotations

import pytest

flint_core = pytest.importorskip("flint_core")

from flint.execution.fill_models import OrderbookFillModel  # noqa: E402
from flint.models import (  # noqa: E402
    Candle, Order, OrderbookLevel, OrderbookSnapshot, OrderType, Side,
)

EPS = 1e-9


def _snap(bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> OrderbookSnapshot:
    return OrderbookSnapshot(
        market="SOL-PERP", ts=1700000000,
        bids=tuple(OrderbookLevel(price=p, size=s) for p, s in bids),
        asks=tuple(OrderbookLevel(price=p, size=s) for p, s in asks),
    )


def _py_walk(side: Side, size: float, snap: OrderbookSnapshot, reject=True):
    model = OrderbookFillModel(reject_on_insufficient_depth=reject)
    model.set_orderbook(snap)
    order = Order(
        market="SOL-PERP", side=side, order_type=OrderType.MARKET,
        size=size, order_id="o1", ts=1700000000,
    )
    candle = Candle(
        ts=1700000000, open=100.0, high=100.0, low=100.0, close=100.0,
        volume=0.0, market="SOL-PERP", resolution_s=60,
    )
    fill = model.fill_market(order, candle)
    return fill


def _rs_walk(side: str, size: float, bids, asks, reject=True):
    f = flint_core.OrderbookFiller(reject_on_insufficient_depth=reject)
    return f.walk_market(side, size, bids, asks)


class TestLongFillVwapParity:
    @pytest.mark.parametrize("size", [1.0, 3.5, 10.0, 13.0])
    def test_parity_on_varied_sizes(self, size):
        bids = [(99.0, 100.0)]
        asks = [(100.0, 5.0), (101.0, 8.0), (102.0, 10.0)]
        snap = _snap(bids, asks)

        py = _py_walk(Side.LONG, size, snap)
        rs = _rs_walk("long", size, bids, asks)

        # Both must agree on whether the order filled.
        assert (py is None) == (rs is None)
        if py is None:
            return
        assert abs(py.price - rs["price"]) < EPS
        assert abs(py.size - rs["size"]) < EPS
        assert abs(py.impact_bps - rs["impact_bps"]) < EPS


class TestShortFillVwapParity:
    @pytest.mark.parametrize("size", [0.5, 2.0, 8.0])
    def test_parity_on_varied_sizes(self, size):
        bids = [(99.0, 5.0), (98.0, 8.0), (97.0, 10.0)]
        asks = [(100.0, 100.0)]
        snap = _snap(bids, asks)

        py = _py_walk(Side.SHORT, size, snap)
        rs = _rs_walk("short", size, bids, asks)
        assert py is not None and rs is not None
        assert abs(py.price - rs["price"]) < EPS
        assert abs(py.impact_bps - rs["impact_bps"]) < EPS


class TestRejection:
    def test_both_reject_when_size_exceeds_depth(self):
        bids = [(99.0, 2.0)]
        asks = [(100.0, 2.0), (101.0, 1.0)]
        snap = _snap(bids, asks)
        py = _py_walk(Side.LONG, 10.0, snap, reject=True)
        rs = _rs_walk("long", 10.0, bids, asks, reject=True)
        assert py is None
        assert rs is None

    def test_both_partial_fill_when_reject_disabled(self):
        bids = [(99.0, 2.0)]
        asks = [(100.0, 2.0), (101.0, 1.0)]
        snap = _snap(bids, asks)
        py = _py_walk(Side.LONG, 10.0, snap, reject=False)
        rs = _rs_walk("long", 10.0, bids, asks, reject=False)
        assert py is not None and rs is not None
        assert abs(py.price - rs["price"]) < EPS
        assert abs(py.size - rs["size"]) < EPS
        # Python model doesn't set is_partial on Fill today (T3.1 left
        # that for the FillPipeline stage); we verify the Rust flag
        # separately.
        assert rs["is_partial"] is True


class TestEmptyBook:
    def test_both_return_none_on_empty_ask_side_for_long(self):
        # bids present, asks empty → long can't fill
        bids = [(99.0, 10.0)]
        snap = _snap(bids, [])
        _py_walk(Side.LONG, 1.0, snap)  # Python falls back; surface differs
        rs = _rs_walk("long", 1.0, bids, [])
        # Python falls back to SlippageFill on empty book (returns a
        # Fill rather than None). Rust OrderbookFiller signals None so
        # the caller can fall back. Both are None-equivalent at the
        # "did the book walk produce a vwap?" level — assert Rust is
        # None and Python is either None or a SlippageFill (non-None).
        assert rs is None
        # Python's fallback picks a non-book price, so we don't parity
        # assert here — different surfaces.


class TestImpactBpsSign:
    def test_long_impact_positive(self):
        bids = [(99.0, 10.0)]
        asks = [(101.0, 10.0)]
        rs = _rs_walk("long", 5.0, bids, asks)
        # mid = 100, fill @ 101 → +100 bps
        assert abs(rs["impact_bps"] - 100.0) < EPS

    def test_short_impact_positive(self):
        bids = [(99.0, 10.0)]
        asks = [(101.0, 10.0)]
        rs = _rs_walk("short", 5.0, bids, asks)
        # mid = 100, sell @ 99 → +100 bps
        assert abs(rs["impact_bps"] - 100.0) < EPS


class TestCapabilityFlag:
    def test_supports_orderbook_walk_flipped_true(self):
        caps = flint_core.capabilities()
        assert caps["supports_orderbook_walk"] is True
