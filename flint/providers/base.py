"""Abstract base class for candle providers."""
from __future__ import annotations

import abc
from typing import List

from ..models import Candle


class CandleProvider(abc.ABC):
    @abc.abstractmethod
    def fetch_candles(
        self,
        market: str,
        resolution_s: int,
        start_ts: int,
        end_ts: int,
    ) -> List[Candle]:
        ...
