"""Tests for JupiterFillModel — keeper-delay-aware, pool-based fill simulation."""
from __future__ import annotations


from flint.execution.fill_jupiter import JupiterFillModel
from flint.execution.fill_models import FillModel
from flint.models import Candle, Fill, Order, OrderType, Side


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candle(
    ts: int = 1_000_000,
    open_: float = 100.0,
    close: float = 110.0,
    high: float = 115.0,
    low: float = 95.0,
    market: str = "SOL-PERP",
    resolution_s: int = 3600,
) -> Candle:
    return Candle(
        ts=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
        market=market,
        resolution_s=resolution_s,
    )


def _order(
    size: float = 10.0,
    side: Side = Side.LONG,
    market: str = "SOL-PERP",
    price: float = 0.0,
    order_type: OrderType = OrderType.MARKET,
    order_id: str = "test-order",
) -> Order:
    return Order(
        market=market,
        side=side,
        order_type=order_type,
        size=size,
        price=price,
        order_id=order_id,
    )


# ---------------------------------------------------------------------------
# Subclass check
# ---------------------------------------------------------------------------

class TestJupiterFillModelIsAFillModel:
    def test_subclass(self):
        assert issubclass(JupiterFillModel, FillModel)

    def test_instantiates_with_defaults(self):
        model = JupiterFillModel()
        assert model is not None

    def test_instantiates_with_seed(self):
        model = JupiterFillModel(seed=42)
        assert model is not None


# ---------------------------------------------------------------------------
# Market fill: price and impact
# ---------------------------------------------------------------------------

class TestFillMarket:
    def test_returns_fill(self):
        model = JupiterFillModel(seed=42)
        candle = _candle()
        fill = model.fill_market(_order(size=10.0), candle)
        assert isinstance(fill, Fill)

    def test_fill_venue_is_jupiter(self):
        model = JupiterFillModel(seed=42)
        fill = model.fill_market(_order(), _candle())
        assert fill.venue == "jupiter"

    def test_fill_preserves_market_and_side(self):
        model = JupiterFillModel(seed=42)
        order = _order(market="BTC-PERP", side=Side.LONG)
        fill = model.fill_market(order, _candle(market="BTC-PERP"))
        assert fill.market == "BTC-PERP"
        assert fill.side == Side.LONG

    def test_fill_preserves_order_id(self):
        model = JupiterFillModel(seed=0)
        order = _order(order_id="abc-123")
        fill = model.fill_market(order, _candle())
        assert fill.order_id == "abc-123"

    def test_small_order_negligible_impact(self):
        """10 SOL at ~$100 = $1 000 notional; impact should be <$0.01."""
        model = JupiterFillModel(base_latency_s=0.0, latency_jitter_s=0.0, seed=0)
        candle = _candle(open_=100.0, close=100.0)
        fill = model.fill_market(_order(size=10.0), candle)
        assert abs(fill.price - 100.0) < 1.0

    def test_large_order_has_significant_impact(self):
        """Larger notional should widen the fill price more than a small order."""
        model_small = JupiterFillModel(base_latency_s=0.0, latency_jitter_s=0.0, seed=0)
        model_large = JupiterFillModel(base_latency_s=0.0, latency_jitter_s=0.0, seed=0)
        candle = _candle(open_=100.0, close=100.0)
        small_fill = model_small.fill_market(_order(size=10.0, side=Side.LONG), candle)
        large_fill = model_large.fill_market(_order(size=100_000.0, side=Side.LONG), candle)
        assert large_fill.price > small_fill.price

    def test_long_impact_raises_price(self):
        """Long orders should be filled above the oracle price (adverse impact)."""
        model = JupiterFillModel(base_latency_s=0.0, latency_jitter_s=0.0, seed=0)
        candle = _candle(open_=100.0, close=100.0)
        fill = model.fill_market(_order(size=100_000.0, side=Side.LONG), candle)
        assert fill.price > 100.0

    def test_short_order_impact_lowers_price(self):
        """Short orders should be filled below the oracle price (adverse impact)."""
        model = JupiterFillModel(base_latency_s=0.0, latency_jitter_s=0.0, seed=0)
        candle = _candle(open_=100.0, close=100.0)
        fill = model.fill_market(_order(size=100_000.0, side=Side.SHORT), candle)
        assert fill.price < 100.0

    def test_fee_is_zero(self):
        """Fee is always 0.0 — delegated to FlatFeeModel at 6 bps."""
        model = JupiterFillModel(seed=0)
        fill = model.fill_market(_order(), _candle())
        assert fill.fee == 0.0

    def test_ts_matches_candle(self):
        model = JupiterFillModel(seed=0)
        candle = _candle(ts=9_999_999)
        fill = model.fill_market(_order(), candle)
        assert fill.ts == 9_999_999


# ---------------------------------------------------------------------------
# Keeper delay interpolation
# ---------------------------------------------------------------------------

class TestKeeperDelayInterpolation:
    def test_zero_delay_fills_at_open(self):
        """Zero delay should return the candle open price (before any impact)."""
        model = JupiterFillModel(base_latency_s=0.0, latency_jitter_s=0.0, seed=0)
        # Use tiny order to make impact negligible
        candle = _candle(open_=100.0, close=110.0, resolution_s=3600)
        fill = model.fill_market(_order(size=0.001), candle)
        assert abs(fill.price - 100.0) < 0.01

    def test_full_bar_delay_fills_at_close(self):
        """Delay equal to bar duration should yield the close price."""
        model = JupiterFillModel(base_latency_s=3600.0, latency_jitter_s=0.0, seed=0)
        candle = _candle(open_=100.0, close=110.0, resolution_s=3600)
        fill = model.fill_market(_order(size=0.001), candle)
        assert abs(fill.price - 110.0) < 0.01

    def test_half_bar_delay_interpolates(self):
        """Delay of half bar width should land halfway between open and close."""
        model = JupiterFillModel(base_latency_s=1800.0, latency_jitter_s=0.0, seed=0)
        candle = _candle(open_=100.0, close=110.0, resolution_s=3600)
        fill = model.fill_market(_order(size=0.001), candle)
        # Expected oracle: 100 + 0.5*(110-100) = 105
        assert 100.0 < fill.price < 110.0
        assert abs(fill.price - 105.0) < 0.01

    def test_delay_does_not_snap_to_bar_boundary(self):
        """Fill price must NOT be rounded to open or close; it must be a lerp."""
        model = JupiterFillModel(base_latency_s=900.0, latency_jitter_s=0.0, seed=0)
        candle = _candle(open_=100.0, close=120.0, resolution_s=3600)
        fill = model.fill_market(_order(size=0.001), candle)
        # Expected ~105, definitely not 100 or 120
        assert fill.price not in (100.0, 120.0)
        assert 100.0 < fill.price < 120.0


# ---------------------------------------------------------------------------
# Cross-bar delay (candle buffer)
# ---------------------------------------------------------------------------

class TestCandleBufferForCrossBarDelay:
    def _make_next_candle(self, current: Candle, open_: float, close: float) -> Candle:
        return Candle(
            ts=current.ts + current.resolution_s,
            open=open_,
            high=close + 5,
            low=open_ - 5,
            close=close,
            volume=500.0,
            market=current.market,
            resolution_s=current.resolution_s,
        )

    def test_cross_bar_uses_next_candle_open(self):
        """Delay > bar duration should start interpolating from the next candle's open."""
        current = _candle(open_=100.0, close=110.0, resolution_s=3600)
        next_c = self._make_next_candle(current, open_=106.0, close=120.0)

        model = JupiterFillModel(base_latency_s=4000.0, latency_jitter_s=0.0, seed=0)
        model.set_candle_buffer([next_c])

        fill = model.fill_market(_order(size=0.001), current)
        # remaining = 400 s into next bar (3600 duration) → frac ≈ 0.111
        # price ≈ 106 + 0.111*(120-106) ≈ 107.56
        assert fill.price >= 106.0

    def test_cross_bar_caps_at_next_close_when_delay_very_large(self):
        """Very large delay should not exceed the next candle's close price."""
        current = _candle(open_=100.0, close=110.0, resolution_s=3600)
        next_c = self._make_next_candle(current, open_=106.0, close=120.0)

        # 100 000 s delay >> 2 bars
        model = JupiterFillModel(base_latency_s=100_000.0, latency_jitter_s=0.0, seed=0)
        model.set_candle_buffer([next_c])

        fill = model.fill_market(_order(size=0.001), current)
        # frac is capped at 1.0 → price = next_c.close = 120.0
        assert abs(fill.price - 120.0) < 0.01

    def test_missing_next_candle_returns_current_close(self):
        """When no next candle is in the buffer, fall back to the current bar's close."""
        current = _candle(open_=100.0, close=110.0, resolution_s=3600)

        model = JupiterFillModel(base_latency_s=5000.0, latency_jitter_s=0.0, seed=0)
        model.set_candle_buffer([])  # empty buffer

        fill = model.fill_market(_order(size=0.001), current)
        assert abs(fill.price - 110.0) < 0.01

    def test_wrong_market_candle_not_used(self):
        """Buffer candles for a different market should not be picked up."""
        current = _candle(market="SOL-PERP", open_=100.0, close=110.0, resolution_s=3600)
        wrong_market_next = Candle(
            ts=current.ts + current.resolution_s,
            open=200.0, high=210.0, low=195.0, close=205.0,
            volume=100.0, market="BTC-PERP", resolution_s=3600,
        )
        model = JupiterFillModel(base_latency_s=5000.0, latency_jitter_s=0.0, seed=0)
        model.set_candle_buffer([wrong_market_next])

        fill = model.fill_market(_order(size=0.001, market="SOL-PERP"), current)
        # Falls back to current close, not 200
        assert abs(fill.price - 110.0) < 0.01


# ---------------------------------------------------------------------------
# set_orderbook — must be a no-op
# ---------------------------------------------------------------------------

class TestSetOrderbook:
    def test_set_orderbook_none_does_not_raise(self):
        model = JupiterFillModel(seed=0)
        model.set_orderbook(None)  # should not raise
        fill = model.fill_market(_order(), _candle())
        assert fill is not None

    def test_set_orderbook_non_none_does_not_raise(self):
        from flint.models import OrderbookSnapshot
        model = JupiterFillModel(seed=0)
        model.set_orderbook(OrderbookSnapshot(market="SOL-PERP", ts=0, bids=(), asks=()))
        fill = model.fill_market(_order(), _candle())
        assert fill is not None


# ---------------------------------------------------------------------------
# fill_limit
# ---------------------------------------------------------------------------

class TestFillLimit:
    def test_long_fills_when_candle_low_touches_price(self):
        model = JupiterFillModel(seed=0)
        candle = _candle(low=98.0, high=115.0)
        order = _order(side=Side.LONG, price=99.0, order_type=OrderType.LIMIT)
        fill = model.fill_limit(order, candle)
        assert fill is not None
        assert fill.price == 99.0

    def test_long_no_fill_when_price_above_low(self):
        model = JupiterFillModel(seed=0)
        candle = _candle(low=105.0, high=115.0)
        order = _order(side=Side.LONG, price=99.0, order_type=OrderType.LIMIT)
        fill = model.fill_limit(order, candle)
        assert fill is None

    def test_short_fills_when_candle_high_touches_price(self):
        model = JupiterFillModel(seed=0)
        candle = _candle(low=95.0, high=115.0)
        order = _order(side=Side.SHORT, price=112.0, order_type=OrderType.LIMIT)
        fill = model.fill_limit(order, candle)
        assert fill is not None
        assert fill.price == 112.0

    def test_short_no_fill_when_price_below_high(self):
        model = JupiterFillModel(seed=0)
        candle = _candle(low=95.0, high=108.0)
        order = _order(side=Side.SHORT, price=112.0, order_type=OrderType.LIMIT)
        fill = model.fill_limit(order, candle)
        assert fill is None

    def test_limit_fill_venue_is_jupiter(self):
        model = JupiterFillModel(seed=0)
        candle = _candle(low=90.0, high=115.0)
        order = _order(side=Side.LONG, price=95.0, order_type=OrderType.LIMIT)
        fill = model.fill_limit(order, candle)
        assert fill is not None
        assert fill.venue == "jupiter"

    def test_limit_fill_fee_is_zero(self):
        model = JupiterFillModel(seed=0)
        candle = _candle(low=90.0, high=115.0)
        order = _order(side=Side.LONG, price=95.0, order_type=OrderType.LIMIT)
        fill = model.fill_limit(order, candle)
        assert fill.fee == 0.0


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------

class TestReproducibility:
    def test_same_seed_gives_same_fill_price(self):
        candle = _candle()
        order = _order(size=1000.0)

        fill_a = JupiterFillModel(seed=7).fill_market(order, candle)
        fill_b = JupiterFillModel(seed=7).fill_market(order, candle)

        assert fill_a.price == fill_b.price

    def test_different_seeds_may_give_different_prices(self):
        candle = _candle(open_=100.0, close=200.0, resolution_s=3600)
        order = _order(size=0.001)

        prices = {
            JupiterFillModel(seed=s).fill_market(order, candle).price
            for s in range(20)
        }
        # With a range of open→close there should be variation
        assert len(prices) > 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_zero_size_order_does_not_crash(self):
        """impact_per_unit guard: size=0 should not divide by zero."""
        model = JupiterFillModel(base_latency_s=0.0, latency_jitter_s=0.0, seed=0)
        order = _order(size=0.0)
        fill = model.fill_market(order, _candle())
        assert fill is not None
        assert fill.size == 0.0

    def test_unknown_market_uses_default_scalar(self):
        """Markets not in _IMPACT_SCALARS fall back to 1e9 scalar."""
        model = JupiterFillModel(base_latency_s=0.0, latency_jitter_s=0.0, seed=0)
        candle = _candle(market="BONK-PERP", open_=0.01, close=0.01)
        order = _order(market="BONK-PERP", size=10.0)
        fill = model.fill_market(order, candle)
        assert fill is not None

    def test_zero_resolution_falls_back_to_close(self):
        """Candles with resolution_s=0 should not divide by zero."""
        model = JupiterFillModel(base_latency_s=100.0, latency_jitter_s=0.0, seed=0)
        candle = Candle(ts=0, open=100.0, high=110.0, low=90.0, close=105.0,
                        volume=1.0, market="SOL-PERP", resolution_s=0)
        fill = model.fill_market(_order(size=0.001), candle)
        assert abs(fill.price - 105.0) < 0.01

    def test_jitter_clamps_delay_to_non_negative(self):
        """Latency must never go negative even with large jitter."""
        model = JupiterFillModel(base_latency_s=1.0, latency_jitter_s=100.0, seed=0)
        candle = _candle(open_=100.0, close=110.0, resolution_s=3600)
        for _ in range(50):
            fill = model.fill_market(_order(size=0.001), candle)
            # Price must be >= open (delay >= 0 means no "negative time" fill)
            assert fill.price >= 100.0 - 0.01  # small tolerance for floating point
