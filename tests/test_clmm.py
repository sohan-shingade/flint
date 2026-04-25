"""Tests for CLMMPool tick-range model."""
import math
from flint.mev.clmm import TickRange, CLMMPool

class TestTickRange:
    def test_create(self):
        tr = TickRange(tick_lower=-100, tick_upper=100, liquidity=1_000_000.0)
        assert tr.tick_lower == -100
        assert tr.liquidity == 1_000_000.0

class TestCLMMPoolConstruction:
    def test_create_with_ticks(self):
        ticks = [
            TickRange(tick_lower=-1000, tick_upper=0, liquidity=500_000.0),
            TickRange(tick_lower=0, tick_upper=1000, liquidity=1_000_000.0),
        ]
        pool = CLMMPool(pool_address="pool1", dex="orca", token_a_mint="SOL", token_b_mint="USDC",
            tick_ranges=ticks, current_tick=50, tick_spacing=64, fee_rate=0.003, sqrt_price=math.sqrt(150.0))
        assert pool.dex == "orca"
        assert len(pool.tick_ranges) == 2

    def test_price_at_tick(self):
        pool = CLMMPool(pool_address="p", dex="orca", token_a_mint="A", token_b_mint="B",
            tick_ranges=[], current_tick=0, tick_spacing=1, fee_rate=0.003, sqrt_price=1.0)
        assert abs(pool.price_at_tick(0) - 1.0) < 0.001
        assert pool.price_at_tick(100) > 1.0

class TestOutputAmount:
    def _make_pool(self, liquidity=1_000_000.0):
        return CLMMPool(pool_address="p", dex="orca", token_a_mint="A", token_b_mint="B",
            tick_ranges=[TickRange(tick_lower=-5000, tick_upper=5000, liquidity=liquidity)],
            current_tick=0, tick_spacing=64, fee_rate=0.003, sqrt_price=1.0)

    def test_small_swap_positive(self):
        assert self._make_pool().output_amount(1.0, a_to_b=True) > 0

    def test_larger_swap_more_slippage(self):
        pool = self._make_pool()
        small_rate = pool.output_amount(1.0, True) / 1.0
        large_rate = pool.output_amount(100.0, True) / 100.0
        assert small_rate > large_rate

    def test_more_liquidity_less_slippage(self):
        thin = self._make_pool(100_000.0)
        thick = self._make_pool(10_000_000.0)
        # Use a large swap that exhausts the thin pool's range so slippage differs
        assert thick.output_amount(500_000.0, True) > thin.output_amount(500_000.0, True)

    def test_both_directions(self):
        pool = self._make_pool()
        assert pool.output_amount(1.0, a_to_b=True) > 0
        assert pool.output_amount(1.0, a_to_b=False) > 0

    def test_empty_range_no_output(self):
        pool = CLMMPool(pool_address="p", dex="orca", token_a_mint="A", token_b_mint="B",
            tick_ranges=[], current_tick=0, tick_spacing=64, fee_rate=0.003, sqrt_price=1.0)
        assert pool.output_amount(1.0, True) == 0.0

class TestToPoolState:
    def test_backward_compat(self):
        pool = CLMMPool(pool_address="p1", dex="orca", token_a_mint="SOL", token_b_mint="USDC",
            tick_ranges=[TickRange(-1000, 1000, 1_000_000.0)], current_tick=0,
            tick_spacing=64, fee_rate=0.003, sqrt_price=math.sqrt(150.0))
        ps = pool.to_pool_state()
        assert ps.pool_address == "p1"
        assert ps.reserve_a > 0
        assert ps.reserve_b > 0
