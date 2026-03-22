"""Pluggable fee models for trade cost simulation."""
from __future__ import annotations

import abc

from ..models import Fill


class FeeModel(abc.ABC):
    """Computes the fee for a fill."""

    @abc.abstractmethod
    def compute_fee(self, fill: Fill) -> float:
        """Return the fee in quote currency for this fill."""
        ...


class FlatFeeModel(FeeModel):
    """v0.1 behavior: flat basis-point fee on notional."""

    def __init__(self, fee_bps: float = 5.0):
        self.fee_rate = fee_bps / 10_000

    def compute_fee(self, fill: Fill) -> float:
        return abs(fill.size) * fill.price * self.fee_rate


class DriftFeeModel(FeeModel):
    """Drift protocol tiered fees: maker rebate, taker fee."""

    def __init__(self, maker_fee: float = -0.0002, taker_fee: float = 0.001):
        self.maker_fee = maker_fee  # negative = rebate
        self.taker_fee = taker_fee

    def compute_fee(self, fill: Fill, is_maker: bool = False) -> float:
        notional = abs(fill.size) * fill.price
        rate = self.maker_fee if is_maker else self.taker_fee
        return notional * rate


class ZeroFeeModel(FeeModel):
    """No fees — useful for testing."""

    def compute_fee(self, fill: Fill) -> float:
        return 0.0
