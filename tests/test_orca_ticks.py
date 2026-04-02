"""Tests for OrcaTickFetcher — mocked RPC."""
import asyncio
import math
import pytest
from flint.providers.orca_ticks import OrcaTickFetcher
from flint.mev.clmm import CLMMPool, TickRange

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)

class TestBuildCLMMPool:
    def test_build_from_decoded_data(self):
        fetcher = OrcaTickFetcher()
        pool = fetcher._build_pool(
            pool_address="pool1",
            whirlpool_data={
                "token_mint_a": "SOL_MINT", "token_mint_b": "USDC_MINT",
                "tick_current_index": 50,
                "sqrt_price": int(math.sqrt(150.0) * (2**64)),
                "fee_rate": 300, "tick_spacing": 64,
            },
            tick_ranges=[
                TickRange(tick_lower=-1000, tick_upper=0, liquidity=500_000.0),
                TickRange(tick_lower=0, tick_upper=1000, liquidity=1_000_000.0),
            ],
        )
        assert isinstance(pool, CLMMPool)
        assert pool.dex == "orca"
        assert pool.current_tick == 50
        assert len(pool.tick_ranges) == 2

    def test_fee_rate_conversion(self):
        fetcher = OrcaTickFetcher()
        pool = fetcher._build_pool(
            pool_address="pool1",
            whirlpool_data={
                "token_mint_a": "A", "token_mint_b": "B",
                "tick_current_index": 0, "sqrt_price": 2**64,
                "fee_rate": 3000, "tick_spacing": 64,
            },
            tick_ranges=[],
        )
        assert abs(pool.fee_rate - 0.003) < 0.0001


class TestTicksToRanges:
    def test_decode_from_tick_data(self):
        fetcher = OrcaTickFetcher()
        raw_ticks = [
            {"tick_index": -128, "liquidity_net": 1000000, "liquidity_gross": 1000000, "initialized": True},
            {"tick_index": 0, "liquidity_net": -500000, "liquidity_gross": 500000, "initialized": True},
            {"tick_index": 128, "liquidity_net": -500000, "liquidity_gross": 0, "initialized": True},
        ]
        ranges = fetcher._ticks_to_ranges(raw_ticks, tick_spacing=64)
        assert len(ranges) > 0
        assert all(isinstance(r, TickRange) for r in ranges)


class TestConstruction:
    def test_creates(self):
        fetcher = OrcaTickFetcher(rpc_url="https://api.mainnet-beta.solana.com")
        assert fetcher._rpc_url is not None
