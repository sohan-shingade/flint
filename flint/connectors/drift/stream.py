"""Drift event stream — polls the DLOB and Data APIs for live updates.

A full WebSocket-based stream would require geyser/Yellowstone integration.
This module provides a polling-based alternative that works with free APIs.
"""
from __future__ import annotations

import time
from typing import Callable, List, Optional

from ...models import FundingRate, OrderbookSnapshot
from ...providers.drift_api import DriftDataProvider


class DriftPoller:
    """Polls Drift APIs at configurable intervals.

    Usage::

        poller = DriftPoller()
        poller.on_orderbook = my_callback
        poller.start("SOL-PERP", interval_s=2.0)
    """

    def __init__(self, provider: Optional[DriftDataProvider] = None) -> None:
        self._provider = provider or DriftDataProvider()
        self._owns_provider = provider is None
        self.on_orderbook: Optional[Callable[[OrderbookSnapshot], None]] = None
        self.on_funding: Optional[Callable[[List[FundingRate]], None]] = None
        self._running = False

    def poll_once(self, market: str = "SOL-PERP", depth: int = 10) -> OrderbookSnapshot:
        """Fetch orderbook once and fire callback if set."""
        ob = self._provider.fetch_orderbook(market, depth)
        if self.on_orderbook:
            self.on_orderbook(ob)
        return ob

    def poll_funding(self, market: str = "SOL-PERP", limit: int = 10) -> List[FundingRate]:
        """Fetch latest funding rates and fire callback if set."""
        market_indices = {
            "SOL-PERP": 0, "BTC-PERP": 1, "ETH-PERP": 2,
        }
        idx = market_indices.get(market, 0)
        rates = self._provider.fetch_funding_rates(idx, market, limit)
        if self.on_funding:
            self.on_funding(rates)
        return rates

    def run_loop(
        self,
        market: str = "SOL-PERP",
        interval_s: float = 2.0,
        max_iterations: Optional[int] = None,
    ) -> None:
        """Blocking poll loop. Use ``max_iterations`` for testing."""
        self._running = True
        count = 0
        while self._running:
            self.poll_once(market)
            count += 1
            if max_iterations is not None and count >= max_iterations:
                break
            time.sleep(interval_s)

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self.stop()
        if self._owns_provider:
            self._provider.close()
