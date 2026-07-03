"""Latency — the one-way decision→eligible delay, seeded and reproducible (§6.3).

A market order does not fill against the book the strategy saw; it fills against
the book at ``submit_ts + latency``, because the book moves in the tens-to-
hundreds of ms between deciding and the order arriving. Filling at bar-open
ignores this — we don't (§6.3 item 1).

The delay is drawn from a **lognormal** calibrated to a ``(median, p95)`` pair:
lognormal is non-negative with a heavy right tail, matching real block-inclusion
latency. Draws come from the engine's seeded ``ctx.rng`` so a run — and its cost
attribution — is deterministic (§6.3, §19.4).
"""

from __future__ import annotations

import math
from random import Random

from flint.venues import LatencyProfile

# z-score of the 95th percentile of a standard normal.
_Z95 = 1.6448536269514722


class LatencyModel:
    """Lognormal one-way latency in ms with a given median and 95th percentile.

    ``median = exp(mu)`` and ``p95 = exp(mu + z95 * sigma)``, so ``mu = ln(base)``
    and ``sigma = ln(p95 / base) / z95``. A draw is ``exp(mu + sigma * Z)`` for a
    standard-normal ``Z`` from the seeded rng.
    """

    def __init__(self, profile: LatencyProfile) -> None:
        if profile.base_ms <= 0 or profile.p95_ms < profile.base_ms:
            raise ValueError("latency needs 0 < base_ms <= p95_ms")
        self._mu = math.log(profile.base_ms)
        self._sigma = math.log(profile.p95_ms / profile.base_ms) / _Z95

    def draw_ms(self, rng: Random) -> float:
        """Draw one latency in milliseconds from the seeded ``rng``."""
        return math.exp(self._mu + self._sigma * rng.gauss(0.0, 1.0))
