"""Tests for ArbDetector CLMM integration."""
import pytest
from flint.models import PoolState
from flint.mev.arb import ArbDetector
from flint.mev.clmm import CLMMPool, TickRange

def _make_clmm(pool_address, token_a, token_b, liquidity=1_000_000.0):
    return CLMMPool(pool_address=pool_address, dex="orca",
        token_a_mint=token_a, token_b_mint=token_b,
        tick_ranges=[TickRange(-5000, 5000, liquidity)],
        current_tick=0, tick_spacing=64, fee_rate=0.003, sqrt_price=1.0)

class TestCLMMEdge:
    def test_clmm_used(self):
        pool = PoolState(pool_address="p1", dex="orca", token_a_mint="A", token_b_mint="B",
            reserve_a=1000, reserve_b=1000, fee_rate=0.003)
        det = ArbDetector(min_profit_bps=0)
        det.update_pools([pool], clmm_pools={"p1": _make_clmm("p1", "A", "B")})
        assert det._adjacency["A"][0].output_amount(1.0) > 0

    def test_fallback_without_clmm(self):
        pool = PoolState(pool_address="p1", dex="orca", token_a_mint="A", token_b_mint="B",
            reserve_a=1000, reserve_b=1000, fee_rate=0.003)
        det = ArbDetector(min_profit_bps=0)
        det.update_pools([pool])
        assert det._adjacency["A"][0].output_amount(1.0) > 0

    def test_clmm_differs_from_cp(self):
        pool = PoolState(pool_address="p1", dex="orca", token_a_mint="A", token_b_mint="B",
            reserve_a=1000, reserve_b=1000, fee_rate=0.003)
        det_cp = ArbDetector(min_profit_bps=0)
        det_cp.update_pools([pool])
        cp_out = det_cp._adjacency["A"][0].output_amount(10.0)

        det_clmm = ArbDetector(min_profit_bps=0)
        det_clmm.update_pools([pool], clmm_pools={"p1": _make_clmm("p1", "A", "B", 500_000.0)})
        clmm_out = det_clmm._adjacency["A"][0].output_amount(10.0)
        assert abs(cp_out - clmm_out) > 0.001

    def test_backward_compat(self):
        pool = PoolState(pool_address="p1", dex="raydium", token_a_mint="A", token_b_mint="B",
            reserve_a=1000, reserve_b=1000, fee_rate=0.003)
        det = ArbDetector(min_profit_bps=0)
        det.update_pools([pool])
        assert det._adjacency["A"][0].output_amount(1.0) > 0
