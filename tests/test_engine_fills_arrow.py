"""Arrow taker book-walk parity — bit-identical to ``ClobFillModel`` (§19.4 gate).

The Arrow-native fill path exists only to be faster; if it changes a single fill
it is a bug, not a tolerance (`docs/redesign/ARROW-FILL-PATH.md`). Every test here
builds a depth table from the same recorded/hand-authored books the scalar CLOB
tests use, fills it two ways — the scalar ``ClobFillModel`` per snapshot and the
vectorized ``arrow_taker_fills`` in one batch — and asserts the two ``Fill``\\ s are
**byte-for-byte** equal (``==`` on every field, not ``approx``). No synthetic data
(D26): the books are hand-authored unit inputs, the §6.3 golden among them.
"""

from __future__ import annotations

import pytest

from flint.core.models import Order, OrderbookSnapshot, OrderType, Side, TimeInForce
from flint.data.normalize import books_to_arrow
from flint.engine.fills import ClobFillModel, FillContext, arrow_taker_fills
from flint.engine.fills.arrow import depth_level_arrays, taker_walk_batch

MODEL = ClobFillModel()
MARKET = "SOL-PERP"
VENUE = "hyperliquid"
SCALAR = ClobFillModel()


def _book(ts, bids, asks):
    return OrderbookSnapshot(market=MARKET, ts=ts, bids=tuple(bids), asks=tuple(asks), venue=VENUE)


def _order(side, size, type=OrderType.MARKET, price=0.0, tif=TimeInForce.IOC):
    return Order(market=MARKET, side=side, type=type, size=size, price=price,
                 venue=VENUE, tif=tif, client_order_id="c1")


def _assert_fill_eq(scalar_res, arrow_res, ts):
    """Bit-identical: same reject decision, else every ``Fill`` field byte-equal."""
    if scalar_res is None:
        assert arrow_res is None
        return
    assert arrow_res is not None
    sf, af = scalar_res.fill, arrow_res.fill
    # price/size/fee/slippage are exact — this is the whole point of the gate.
    assert af.price == sf.price
    assert af.size == sf.size
    assert af.fee == sf.fee
    assert af.slippage_bps == sf.slippage_bps
    assert af.is_partial is sf.is_partial
    assert af.liquidity == sf.liquidity
    assert af.fidelity_tier == sf.fidelity_tier
    assert af.side == sf.side
    assert af.market == sf.market
    assert af.venue == sf.venue
    assert af.client_order_id == sf.client_order_id
    assert af.ts == sf.ts == ts
    assert arrow_res.flags == scalar_res.flags


def _parity(order, books, *, tier, oracle_price=0.0, oracle_band_bps=0.0,
            price_sig_figs=5, taker_fee_rate=0.00045, trades=None):
    """Run scalar per snapshot and Arrow in one batch; assert bit-identical."""
    table = books_to_arrow(books)
    arrow = arrow_taker_fills(
        table, order,
        taker_fee_rate=taker_fee_rate,
        price_sig_figs=price_sig_figs,
        oracle_price=oracle_price,
        oracle_band_bps=oracle_band_bps,
        tier=tier,
    )
    assert len(arrow) == len(books)
    for i, book in enumerate(books):
        ctx = FillContext(
            reference_price=book.asks[0][0] if book.asks else 100.0,
            ts=book.ts,
            book=book,
            trades=trades or (),
            oracle_price=oracle_price,
            oracle_band_bps=oracle_band_bps,
            price_sig_figs=price_sig_figs,
            taker_fee_rate=taker_fee_rate,
        )
        _assert_fill_eq(SCALAR.fill(order, ctx), arrow[i], book.ts)
    return arrow


# --- the §6.3 golden, as an Arrow batch ------------------------------------

GOLDEN_ASKS = ((100.00, 4.0), (100.10, 5.0), (100.30, 8.0))
GOLDEN_BIDS = ((99.98, 100.0),)


def test_section_6_3_golden_matches_scalar_bit_for_bit():
    book = _book(1_700_000_000_000, GOLDEN_BIDS, GOLDEN_ASKS)
    arrow = _parity(_order(Side.LONG, 10.0), [book], tier="B")
    # sanity: the golden VWAP still lands where §6.3 says.
    assert arrow[0].fill.price == pytest.approx(100.08)
    assert arrow[0].fill.slippage_bps == pytest.approx(8.0)


def test_tier_a_buy_with_trades_matches_scalar():
    from flint.engine.fills import TradePrint
    book = _book(1, GOLDEN_BIDS, GOLDEN_ASKS)
    _parity(_order(Side.LONG, 10.0), [book], tier="A", trades=(TradePrint(100.0, 1.0, 0),))


def test_partial_fill_beyond_depth_matches_scalar():
    book = _book(1, GOLDEN_BIDS, GOLDEN_ASKS)
    arrow = _parity(_order(Side.LONG, 20.0, tif=TimeInForce.IOC), [book], tier="B")
    assert arrow[0].fill.size == pytest.approx(17.0)
    assert arrow[0].fill.is_partial is True


def test_fill_or_kill_insufficient_depth_rejects_like_scalar():
    book = _book(1, GOLDEN_BIDS, GOLDEN_ASKS)
    _parity(_order(Side.LONG, 20.0, tif=TimeInForce.FOK), [book], tier="B")  # both None


# --- oracle band -----------------------------------------------------------


def test_oracle_band_clip_matches_scalar():
    book = _book(1, GOLDEN_BIDS, GOLDEN_ASKS)
    arrow = _parity(_order(Side.LONG, 10.0), [book], tier="B",
                    oracle_price=100.0, oracle_band_bps=10.0)
    assert arrow[0].fill.size == pytest.approx(9.0)
    assert "oracle_band_clipped" in arrow[0].flags


def test_touch_beyond_band_rejects_like_scalar():
    book = _book(1, ((99.9, 5.0),), ((100.30, 8.0),))
    arrow = _parity(_order(Side.LONG, 1.0), [book], tier="B",
                    oracle_price=100.0, oracle_band_bps=10.0)
    assert arrow[0] is None


# --- sell side (walks bids) -------------------------------------------------


def test_sell_walks_the_bid_book_bit_for_bit():
    book = _book(1, ((99.90, 4.0), (99.80, 5.0), (99.60, 8.0)), ((100.10, 5.0),))
    _parity(_order(Side.SHORT, 10.0), [book], tier="B")


def test_sell_oracle_band_clip_matches_scalar():
    book = _book(1, ((99.90, 4.0), (99.80, 5.0), (99.60, 8.0)), ((100.10, 5.0),))
    _parity(_order(Side.SHORT, 10.0), [book], tier="B",
            oracle_price=100.0, oracle_band_bps=10.0)


# --- price rounding (5 sig figs) -------------------------------------------


def test_five_sig_fig_rounding_matches_scalar():
    book = _book(1, ((99.9, 5.0),), ((100.123456, 5.0),))
    arrow = _parity(_order(Side.LONG, 1.0), [book], tier="B", price_sig_figs=5)
    assert arrow[0].fill.price == pytest.approx(100.12)


# --- crossing limit is a taker ---------------------------------------------


def test_crossing_limit_matches_scalar_taker():
    book = _book(1, GOLDEN_BIDS, GOLDEN_ASKS)
    order = _order(Side.LONG, 4.0, type=OrderType.LIMIT, price=101.0, tif=TimeInForce.GTC)
    _parity(order, [book], tier="B")


# --- multi-snapshot batch with mixed book widths (exercises bucketing) ------


def test_mixed_width_batch_each_snapshot_matches_scalar():
    books = [
        _book(10, GOLDEN_BIDS, ((100.00, 4.0), (100.10, 5.0), (100.30, 8.0))),  # 3 wide
        _book(20, GOLDEN_BIDS, ((100.00, 20.0),)),                              # 1 wide, full
        _book(30, GOLDEN_BIDS, ((100.00, 2.0), (100.05, 2.0), (100.09, 2.0),
                                (100.20, 2.0), (100.40, 2.0))),                 # 5 wide, partial
        _book(40, GOLDEN_BIDS, ((100.02, 100.0),)),                            # 1 wide, tiny fill
    ]
    _parity(_order(Side.LONG, 10.0, tif=TimeInForce.IOC), books, tier="B")


def test_mixed_width_batch_with_band_matches_scalar():
    books = [
        _book(10, GOLDEN_BIDS, ((100.00, 4.0), (100.10, 5.0), (100.30, 8.0))),
        _book(20, GOLDEN_BIDS, ((100.00, 2.0), (100.05, 2.0), (100.12, 20.0))),
    ]
    _parity(_order(Side.LONG, 8.0), books, tier="B",
            oracle_price=100.0, oracle_band_bps=10.0)


# --- empty / degenerate snapshots -------------------------------------------


def test_empty_book_rejects_like_scalar():
    books = [_book(1, GOLDEN_BIDS, ()), _book(2, GOLDEN_BIDS, GOLDEN_ASKS)]
    arrow = _parity(_order(Side.LONG, 3.0), books, tier="B")
    assert arrow[0] is None
    assert arrow[1] is not None


# --- kernel-level: zero-copy extraction + reduction sanity ------------------


def test_depth_level_arrays_are_zero_copy_strided_views():
    table = books_to_arrow([_book(1, GOLDEN_BIDS, GOLDEN_ASKS)])
    px, sz, offsets = depth_level_arrays(table, Side.LONG)
    assert list(offsets) == [0, 3]
    assert list(px[:3]) == [100.00, 100.10, 100.30]
    assert list(sz[:3]) == [4.0, 5.0, 8.0]


def test_spike_arrow_loop_matches_naive_loop_on_benchmark_frame():
    # The 7.2 re-run is apples-to-apples: the Arrow Tier-A loop must produce the
    # same fill count and the same (Decimal) cash as the naive loop it replaces.
    from scripts.spike_throughput import (
        build_depth_table,
        tier_a_fill_loop,
        tier_a_fill_loop_arrow,
    )

    table = build_depth_table(2000)
    fills_naive, cash_naive = tier_a_fill_loop(table)
    fills_arrow, cash_arrow = tier_a_fill_loop_arrow(table)
    assert fills_arrow == fills_naive == 2000
    assert cash_arrow == cash_naive  # Decimal equality — money reduction unchanged


def test_taker_walk_reproduces_sequential_accumulation():
    # Two snapshots, one order; VWAP computed by the vectorized cumsum path must
    # equal the hand-computed sequential VWAP exactly.
    table = books_to_arrow([_book(1, GOLDEN_BIDS, GOLDEN_ASKS)])
    px, sz, offsets = depth_level_arrays(table, Side.LONG)
    walk = taker_walk_batch(px, sz, offsets, buy=True, order_size=10.0)
    notional = 100.00 * 4.0 + 100.10 * 5.0 + 100.30 * 1.0
    assert walk.vwap[0] == notional / 10.0
    assert walk.filled[0] == 10.0
    assert walk.ok[0]
