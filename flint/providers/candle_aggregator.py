"""CandleAggregator — converts raw trade events into OHLCV candles.

One instance per market. Receives trade-by-trade data and emits
closed candles at the configured resolution.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from ..models import Candle

logger = logging.getLogger("flint.candle_aggregator")


class CandleAggregator:
    """Aggregates raw trades into OHLCV candle bars.

    Args:
        market: Market symbol (e.g. "SOL-PERP").
        venue: Venue name (e.g. "drift").
        resolution_s: Candle width in seconds.
        on_candle_close: Callback fired when a bar closes.
        store: Optional FlintStore for persisting closed candles.
    """

    def __init__(
        self,
        market: str,
        venue: str,
        resolution_s: int,
        on_candle_close: Callable[[Candle], None],
        store=None,
    ):
        self._market = market
        self._venue = venue
        self._resolution_s = resolution_s
        self._on_candle_close = on_candle_close
        self._store = store

        self._bar_start: int = 0
        self._open: float = 0.0
        self._high: float = 0.0
        self._low: float = 0.0
        self._close: float = 0.0
        self._volume: float = 0.0
        self._has_data = False

    def process_trade(self, price: float, size: float, ts: int) -> None:
        """Process a single trade. Closes bar if ts crosses boundary."""
        if not self._has_data:
            self._start_bar(price, size, ts)
            return

        bar_end = self._bar_start + self._resolution_s
        if ts >= bar_end:
            self._close_and_emit()
            self._start_bar(price, size, ts)
        else:
            self._high = max(self._high, price)
            self._low = min(self._low, price)
            self._close = price
            self._volume += size

    def current_bar(self) -> Optional[Candle]:
        """Return the in-progress (unclosed) candle, or None if no data."""
        if not self._has_data:
            return None
        return Candle(
            ts=self._bar_start, open=self._open, high=self._high,
            low=self._low, close=self._close, volume=self._volume,
            market=self._market, resolution_s=self._resolution_s,
            venue=self._venue,
        )

    def close_bar(self) -> Optional[Candle]:
        """Force-close the current bar. Returns the closed candle or None."""
        if not self._has_data:
            return None
        return self._close_and_emit()

    def _start_bar(self, price: float, size: float, ts: int) -> None:
        self._bar_start = (ts // self._resolution_s) * self._resolution_s
        self._open = price
        self._high = price
        self._low = price
        self._close = price
        self._volume = size
        self._has_data = True

    def _close_and_emit(self) -> Candle:
        candle = Candle(
            ts=self._bar_start, open=self._open, high=self._high,
            low=self._low, close=self._close, volume=self._volume,
            market=self._market, resolution_s=self._resolution_s,
            venue=self._venue,
        )
        self._has_data = False
        if self._store:
            try:
                self._store.upsert_candles([candle])
            except Exception as e:
                logger.error("Failed to persist candle: %s", e)
        self._on_candle_close(candle)
        return candle
