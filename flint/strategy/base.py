"""Strategy abstract base class."""
from __future__ import annotations

import abc
from typing import List

from ..models import Candle, Signal


class Strategy(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self) -> str:
        ...

    @abc.abstractmethod
    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        """Process a new candle and return a trading signal."""
        ...

    @abc.abstractmethod
    def reset(self) -> None:
        """Reset internal state for a fresh run."""
        ...
