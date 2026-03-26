"""LatencyStage — models venue-specific execution delays."""
from __future__ import annotations

import random
from typing import Optional

from ..models import Order


class LatencyStage:
    """Computes execution delay for orders based on venue latency.

    Orders are delayed by base_latency +/- uniform jitter.
    Accepts an optional seed for reproducible backtests.
    """

    def __init__(
        self,
        base_latency_s: float = 1.0,
        latency_jitter_s: float = 0.5,
        seed: Optional[int] = None,
        enabled: bool = True,
    ):
        self._base = base_latency_s
        self._jitter = latency_jitter_s
        self._enabled = enabled
        self._rng = random.Random(seed) if seed is not None else random.Random()

    def compute_eligible_ts(self, order: Order) -> int:
        """Compute the earliest timestamp at which this order can fill."""
        if not self._enabled:
            return order.ts

        jitter = self._rng.uniform(-self._jitter, self._jitter) if self._jitter > 0 else 0.0
        delay = max(0.0, self._base + jitter)
        return int(order.ts + delay)

    @staticmethod
    def is_eligible(eligible_ts: int, current_ts: int) -> bool:
        """Check if an order's delay has elapsed."""
        return current_ts >= eligible_ts
