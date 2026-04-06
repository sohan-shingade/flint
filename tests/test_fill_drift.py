"""Tests for the Drift 3-tier fill model (JIT auction + DLOB + vAMM)."""
from __future__ import annotations

import pytest

from flint.execution.fill_drift import DriftFillModel
from flint.execution.fill_models import FillModel
from flint.models import (
    Candle, Order, OrderType, OrderbookLevel, OrderbookSnapshot, Side,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ORACLE_PRICE = 100.0
ORDER_SIZE = 10.0


def _candle(price: float = ORACLE_PRICE, market: str = "SOL-PERP") -> Candle:
    return Candle(
        ts=1_000_000,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=50_000.0,
        market=market,
        resolution_s=3600,
    )


def _order(side: Side = Side.LONG, size: float = ORDER_SIZE,
           market: str = "SOL-PERP") -> Order:
    return Order(
        market=market,
        side=side,
        order_type=OrderType.MARKET,
        size=size,
    )


def _book_with_liquidity(market: str = "SOL-PERP") -> OrderbookSnapshot:
    """Deep book: 2 ask levels and 2 bid levels, each 10 units."""
    asks = (
        OrderbookLevel(price=100.05, size=10.0),
        OrderbookLevel(price=100.10, size=10.0),
    )
    bids = (
        OrderbookLevel(price=99.95, size=10.0),
        OrderbookLevel(price=99.90, size=10.0),
    )
    return OrderbookSnapshot(market=market, ts=1_000_000, bids=bids, asks=asks)


def _empty_book(market: str = "SOL-PERP") -> OrderbookSnapshot:
    """Book with no resting orders."""
    return OrderbookSnapshot(market=market, ts=1_000_000, bids=(), asks=())


def _thin_book(market: str = "SOL-PERP") -> OrderbookSnapshot:
    """Book with only 2 units at the best level — forces vAMM backstop."""
    asks = (OrderbookLevel(price=100.05, size=2.0),)
    bids = (OrderbookLevel(price=99.95, size=2.0),)
    return OrderbookSnapshot(market=market, ts=1_000_000, bids=bids, asks=asks)


# ---------------------------------------------------------------------------
# Basic contract
# ---------------------------------------------------------------------------

class TestDriftFillModelIsAFillModel:
    def test_subclass(self):
        assert issubclass(DriftFillModel, FillModel)

    def test_instantiation_defaults(self):
        model = DriftFillModel()
        assert model is not None

    def test_instantiation_with_params(self):
        model = DriftFillModel(
            jit_fill_probability=0.5,
            auction_slots=10,
            auction_price_improvement_bps=1.5,
            seed=99,
        )
        assert model is not None


# ---------------------------------------------------------------------------
# Tier 1: JIT Dutch Auction
# ---------------------------------------------------------------------------

class TestJitTier:
    def test_jit_price_improvement_long(self):
        """JIT _jit_price for a buy must be strictly below oracle price."""
        model = DriftFillModel(
            jit_fill_probability=1.0,
            auction_price_improvement_bps=2.0,
            seed=42,
        )
        candle = _candle()
        jit_px = model._jit_price(candle, Side.LONG)
        assert jit_px < ORACLE_PRICE

    def test_jit_price_improvement_short(self):
        """JIT _jit_price for a sell must be strictly above oracle price."""
        model = DriftFillModel(
            jit_fill_probability=1.0,
            auction_price_improvement_bps=2.0,
            seed=42,
        )
        candle = _candle()
        jit_px = model._jit_price(candle, Side.SHORT)
        assert jit_px > ORACLE_PRICE

    def test_jit_long_fill_better_than_no_jit(self):
        """A long fill that went through JIT should be priced better (lower)
        than the same fill without JIT, all else equal."""
        candle = _candle()
        book = _book_with_liquidity()

        model_jit = DriftFillModel(jit_fill_probability=1.0, seed=42)
        model_jit.set_orderbook(book)
        fill_jit = model_jit.fill_market(_order(Side.LONG), candle)

        model_no_jit = DriftFillModel(jit_fill_probability=0.0, seed=42)
        model_no_jit.set_orderbook(book)
        fill_no_jit = model_no_jit.fill_market(_order(Side.LONG), candle)

        assert fill_jit is not None and fill_no_jit is not None
        # JIT improves the price for the buyer, so VWAP should be lower
        assert fill_jit.price < fill_no_jit.price

    def test_jit_short_fill_better_than_no_jit(self):
        """A short fill that went through JIT should be priced better (higher)
        than the same fill without JIT, all else equal."""
        candle = _candle()
        book = _book_with_liquidity()

        model_jit = DriftFillModel(jit_fill_probability=1.0, seed=42)
        model_jit.set_orderbook(book)
        fill_jit = model_jit.fill_market(_order(Side.SHORT), candle)

        model_no_jit = DriftFillModel(jit_fill_probability=0.0, seed=42)
        model_no_jit.set_orderbook(book)
        fill_no_jit = model_no_jit.fill_market(_order(Side.SHORT), candle)

        assert fill_jit is not None and fill_no_jit is not None
        # JIT improves the price for the seller, so VWAP should be higher
        assert fill_jit.price > fill_no_jit.price

    def test_jit_never_fires_at_zero_probability(self):
        """With jit_fill_probability=0 the JIT tier is bypassed entirely."""
        model = DriftFillModel(jit_fill_probability=0.0, seed=42)
        model.set_orderbook(_book_with_liquidity())
        fill_no_jit = model.fill_market(_order(Side.LONG), _candle())

        model2 = DriftFillModel(jit_fill_probability=1.0, seed=42)
        model2.set_orderbook(_book_with_liquidity())
        fill_jit = model2.fill_market(_order(Side.LONG), _candle())

        # JIT improves price so fill_no_jit price >= fill_jit price for longs
        assert fill_no_jit.price >= fill_jit.price

    def test_jit_fills_portion_of_order(self):
        """JIT should always result in a fully filled order (remaining tiers cover rest)."""
        model = DriftFillModel(jit_fill_probability=1.0, seed=42)
        model.set_orderbook(_book_with_liquidity())
        fill = model.fill_market(_order(), _candle())
        assert fill is not None
        assert fill.size == pytest.approx(ORDER_SIZE, rel=1e-9)


# ---------------------------------------------------------------------------
# Tier 2: DLOB Walk
# ---------------------------------------------------------------------------

class TestDlobTier:
    def test_dlob_used_when_jit_disabled(self):
        """Without JIT, the fill should walk the book."""
        model = DriftFillModel(jit_fill_probability=0.0, seed=42)
        model.set_orderbook(_book_with_liquidity())
        fill = model.fill_market(_order(), _candle())
        assert fill is not None
        # Ask side starts at 100.05, so avg price for a long must be >= 100.05
        assert fill.price >= 100.05

    def test_dlob_uses_synthetic_book_when_no_real_book(self):
        """When no orderbook is provided, fall back to synthetic depth."""
        model = DriftFillModel(jit_fill_probability=0.0, seed=42)
        # Do NOT call set_orderbook — synthetic fallback should engage
        fill = model.fill_market(_order(), _candle())
        assert fill is not None
        assert fill.size == pytest.approx(ORDER_SIZE)

    def test_dlob_uses_synthetic_book_on_market_mismatch(self):
        """A book for a different market should trigger the synthetic fallback."""
        model = DriftFillModel(jit_fill_probability=0.0, seed=42)
        model.set_orderbook(_book_with_liquidity(market="BTC-PERP"))
        fill = model.fill_market(_order(market="SOL-PERP"), _candle())
        assert fill is not None
        assert fill.size == pytest.approx(ORDER_SIZE)

    def test_bid_side_walked_for_shorts(self):
        """Short orders must walk the bid side (sell into bids)."""
        model = DriftFillModel(jit_fill_probability=0.0, seed=42)
        model.set_orderbook(_book_with_liquidity())
        fill = model.fill_market(_order(Side.SHORT), _candle())
        assert fill is not None
        # Bid side top is 99.95, so fill price for a short must be <= 99.95
        assert fill.price <= 99.95


# ---------------------------------------------------------------------------
# Tier 3: vAMM Backstop
# ---------------------------------------------------------------------------

class TestVammTier:
    def test_vamm_backstop_used_when_book_is_empty(self):
        """With an empty book and no JIT, the vAMM must absorb the order."""
        model = DriftFillModel(jit_fill_probability=0.0, seed=42)
        model.set_orderbook(_empty_book())
        fill = model.fill_market(_order(), _candle())
        assert fill is not None
        # vAMM fill price for a long should be above oracle (slippage)
        assert fill.price > ORACLE_PRICE
        assert fill.size == pytest.approx(ORDER_SIZE)

    def test_vamm_applied_to_remainder_after_thin_book(self):
        """Thin book fills 2 units; vAMM must cover the remaining 8 units."""
        model = DriftFillModel(jit_fill_probability=0.0, seed=42)
        model.set_orderbook(_thin_book())
        fill = model.fill_market(_order(), _candle())
        assert fill is not None
        assert fill.size == pytest.approx(ORDER_SIZE)
        # Mixed fill: 2 @ 100.05 (book) + 8 via vAMM (>100); avg > 100.05 for longs
        assert fill.price > 100.0

    def test_vamm_short_backstop_below_oracle(self):
        """vAMM short fill price should be below oracle due to slippage."""
        model = DriftFillModel(jit_fill_probability=0.0, seed=42)
        model.set_orderbook(_empty_book())
        fill = model.fill_market(_order(Side.SHORT), _candle())
        assert fill is not None
        assert fill.price < ORACLE_PRICE

    def test_vamm_uses_fallback_sqrt_k_for_unknown_market(self):
        """Unknown markets should use the fallback sqrt_k without raising."""
        model = DriftFillModel(jit_fill_probability=0.0, seed=42)
        model.set_orderbook(_empty_book(market="UNKNOWN-PERP"))
        order = _order(market="UNKNOWN-PERP")
        candle = _candle(market="UNKNOWN-PERP")
        fill = model.fill_market(order, candle)
        assert fill is not None


# ---------------------------------------------------------------------------
# Full-order split across tiers
# ---------------------------------------------------------------------------

class TestThreeTierSplit:
    def test_full_order_fills_across_all_tiers(self):
        """With moderate JIT prob and a thin book, all 3 tiers contribute."""
        model = DriftFillModel(jit_fill_probability=0.5, seed=42)
        model.set_orderbook(_thin_book())
        fill = model.fill_market(_order(), _candle())
        assert fill is not None
        assert fill.size == pytest.approx(ORDER_SIZE, rel=1e-9)

    def test_full_order_fills_with_deep_book_and_jit(self):
        """With deep book and JIT enabled, the order should always fully fill."""
        model = DriftFillModel(jit_fill_probability=1.0, seed=7)
        model.set_orderbook(_book_with_liquidity())
        fill = model.fill_market(_order(), _candle())
        assert fill is not None
        assert fill.size == pytest.approx(ORDER_SIZE)


# ---------------------------------------------------------------------------
# Side-specific price direction assertions
# ---------------------------------------------------------------------------

class TestPriceDirection:
    def test_long_fill_price_is_reasonable(self):
        """Long fills should be near oracle; exact value is VWAP across tiers."""
        model = DriftFillModel(jit_fill_probability=0.5, seed=1)
        model.set_orderbook(_book_with_liquidity())
        fill = model.fill_market(_order(Side.LONG), _candle())
        assert fill is not None
        # Reasonable sanity: within 1% of oracle
        assert abs(fill.price - ORACLE_PRICE) / ORACLE_PRICE < 0.01

    def test_short_fill_price_is_reasonable(self):
        """Short fills should be near oracle; exact value is VWAP across tiers."""
        model = DriftFillModel(jit_fill_probability=0.5, seed=1)
        model.set_orderbook(_book_with_liquidity())
        fill = model.fill_market(_order(Side.SHORT), _candle())
        assert fill is not None
        assert abs(fill.price - ORACLE_PRICE) / ORACLE_PRICE < 0.01


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_seed_produces_same_fill(self):
        """Two models with the same seed must produce identical fills."""
        candle = _candle()
        order = _order()
        book = _book_with_liquidity()

        model1 = DriftFillModel(jit_fill_probability=0.5, seed=42)
        model1.set_orderbook(book)
        fill1 = model1.fill_market(order, candle)

        model2 = DriftFillModel(jit_fill_probability=0.5, seed=42)
        model2.set_orderbook(book)
        fill2 = model2.fill_market(order, candle)

        assert fill1.price == pytest.approx(fill2.price)
        assert fill1.size == pytest.approx(fill2.size)

    def test_different_seeds_may_produce_different_fills(self):
        """With different seeds and borderline jit_prob, fills can differ."""
        candle = _candle()
        order = _order()
        book = _book_with_liquidity()

        # Run many pairs; at least one pair should differ
        prices = set()
        for seed in range(20):
            m = DriftFillModel(jit_fill_probability=0.5, seed=seed)
            m.set_orderbook(book)
            f = m.fill_market(order, candle)
            if f:
                prices.add(round(f.price, 10))

        assert len(prices) > 1, "Expected price variation across seeds"


# ---------------------------------------------------------------------------
# Fill metadata
# ---------------------------------------------------------------------------

class TestFillMetadata:
    def test_fill_venue_is_drift(self):
        model = DriftFillModel(seed=0)
        model.set_orderbook(_book_with_liquidity())
        fill = model.fill_market(_order(), _candle())
        assert fill.venue == "drift"

    def test_fill_market_matches_order_market(self):
        model = DriftFillModel(seed=0)
        model.set_orderbook(_book_with_liquidity())
        fill = model.fill_market(_order(market="SOL-PERP"), _candle(market="SOL-PERP"))
        assert fill.market == "SOL-PERP"

    def test_fill_side_matches_order_side(self):
        model = DriftFillModel(seed=0)
        model.set_orderbook(_book_with_liquidity())
        fill = model.fill_market(_order(Side.SHORT), _candle())
        assert fill.side == Side.SHORT

    def test_fill_ts_matches_candle_ts(self):
        model = DriftFillModel(seed=0)
        model.set_orderbook(_book_with_liquidity())
        candle = _candle()
        fill = model.fill_market(_order(), candle)
        assert fill.ts == candle.ts


# ---------------------------------------------------------------------------
# Limit order passthrough
# ---------------------------------------------------------------------------

class TestLimitOrder:
    def test_limit_long_fills_when_price_crosses(self):
        """Limit buy fills when candle.low <= order.price."""
        model = DriftFillModel(seed=0)
        order = Order(
            market="SOL-PERP", side=Side.LONG,
            order_type=OrderType.LIMIT, size=5.0, price=99.0,
        )
        candle = Candle(ts=1_000_000, open=100.0, high=101.0, low=98.0,
                        close=100.0, volume=1000.0, market="SOL-PERP", resolution_s=3600)
        fill = model.fill_limit(order, candle)
        assert fill is not None
        assert fill.price == pytest.approx(99.0)
        assert fill.venue == "drift"

    def test_limit_long_does_not_fill_when_price_not_crossed(self):
        """Limit buy does not fill when candle.low > order.price."""
        model = DriftFillModel(seed=0)
        order = Order(
            market="SOL-PERP", side=Side.LONG,
            order_type=OrderType.LIMIT, size=5.0, price=95.0,
        )
        candle = Candle(ts=1_000_000, open=100.0, high=101.0, low=98.0,
                        close=100.0, volume=1000.0, market="SOL-PERP", resolution_s=3600)
        fill = model.fill_limit(order, candle)
        assert fill is None

    def test_limit_short_fills_when_price_crosses(self):
        """Limit sell fills when candle.high >= order.price."""
        model = DriftFillModel(seed=0)
        order = Order(
            market="SOL-PERP", side=Side.SHORT,
            order_type=OrderType.LIMIT, size=5.0, price=101.0,
        )
        candle = Candle(ts=1_000_000, open=100.0, high=102.0, low=99.0,
                        close=100.0, volume=1000.0, market="SOL-PERP", resolution_s=3600)
        fill = model.fill_limit(order, candle)
        assert fill is not None
        assert fill.price == pytest.approx(101.0)

    def test_limit_short_does_not_fill_when_price_not_crossed(self):
        """Limit sell does not fill when candle.high < order.price."""
        model = DriftFillModel(seed=0)
        order = Order(
            market="SOL-PERP", side=Side.SHORT,
            order_type=OrderType.LIMIT, size=5.0, price=105.0,
        )
        candle = Candle(ts=1_000_000, open=100.0, high=102.0, low=99.0,
                        close=100.0, volume=1000.0, market="SOL-PERP", resolution_s=3600)
        fill = model.fill_limit(order, candle)
        assert fill is None
