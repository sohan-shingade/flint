from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List
import numpy as np


class TestStratStrategy(Strategy):
    def __init__(self):
        pass

    @property
    def name(self) -> str:
        return "test_strat"

    @classmethod
    def parameters(cls) -> dict:
        return {}  # Add optimizable params here

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < 20:
            return Signal.HOLD

        # Your logic here
        # Return Signal.BUY, Signal.SELL, or Signal.HOLD

        return Signal.HOLD

    def reset(self) -> None:
        pass
