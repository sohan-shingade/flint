"""CLMMPool — concentrated liquidity model for tick-range AMM pools."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List

from ..models import PoolState


@dataclass
class TickRange:
    """A liquidity range in a concentrated liquidity pool."""
    tick_lower: int
    tick_upper: int
    liquidity: float


class CLMMPool:
    """Concentrated liquidity pool with tick-range price impact."""

    def __init__(self, pool_address: str, dex: str, token_a_mint: str, token_b_mint: str,
                 tick_ranges: List[TickRange], current_tick: int, tick_spacing: int,
                 fee_rate: float, sqrt_price: float):
        self.pool_address = pool_address
        self.dex = dex
        self.token_a_mint = token_a_mint
        self.token_b_mint = token_b_mint
        self.tick_ranges = sorted(tick_ranges, key=lambda t: t.tick_lower)
        self.current_tick = current_tick
        self.tick_spacing = tick_spacing
        self.fee_rate = fee_rate
        self.sqrt_price = sqrt_price

    def price_at_tick(self, tick: int) -> float:
        return 1.0001 ** tick

    def output_amount(self, amount_in: float, a_to_b: bool) -> float:
        """Compute output by walking through active tick ranges."""
        if not self.tick_ranges or amount_in <= 0:
            return 0.0

        remaining = amount_in * (1 - self.fee_rate)
        total_output = 0.0

        if a_to_b:
            active_ranges = [r for r in self.tick_ranges if r.tick_upper > self.current_tick - 5000]
            active_ranges.sort(key=lambda r: r.tick_lower, reverse=True)
        else:
            active_ranges = [r for r in self.tick_ranges if r.tick_lower < self.current_tick + 5000]
            active_ranges.sort(key=lambda r: r.tick_lower)

        for tr in active_ranges:
            if remaining <= 0:
                break
            if tr.liquidity <= 0:
                continue

            sqrt_lower = math.sqrt(self.price_at_tick(tr.tick_lower))
            sqrt_upper = math.sqrt(self.price_at_tick(tr.tick_upper))

            if a_to_b:
                max_a_in_range = tr.liquidity * abs(1.0 / sqrt_lower - 1.0 / sqrt_upper)
                consumed = min(remaining, max_a_in_range)
                if max_a_in_range > 0:
                    fraction = consumed / max_a_in_range
                    max_b_out = tr.liquidity * abs(sqrt_upper - sqrt_lower)
                    output = max_b_out * fraction
                else:
                    output = 0.0
            else:
                max_b_in_range = tr.liquidity * abs(sqrt_upper - sqrt_lower)
                consumed = min(remaining, max_b_in_range)
                if max_b_in_range > 0:
                    fraction = consumed / max_b_in_range
                    max_a_out = tr.liquidity * abs(1.0 / sqrt_lower - 1.0 / sqrt_upper)
                    output = max_a_out * fraction
                else:
                    output = 0.0

            remaining -= consumed
            total_output += output

        return total_output

    def to_pool_state(self) -> PoolState:
        total_a = 0.0
        total_b = 0.0
        for tr in self.tick_ranges:
            sqrt_lower = math.sqrt(self.price_at_tick(tr.tick_lower))
            sqrt_upper = math.sqrt(self.price_at_tick(tr.tick_upper))
            total_a += tr.liquidity * abs(1.0 / sqrt_lower - 1.0 / sqrt_upper)
            total_b += tr.liquidity * abs(sqrt_upper - sqrt_lower)
        return PoolState(
            pool_address=self.pool_address, dex=self.dex,
            token_a_mint=self.token_a_mint, token_b_mint=self.token_b_mint,
            reserve_a=max(total_a, 0.001), reserve_b=max(total_b, 0.001),
            fee_rate=self.fee_rate,
        )
