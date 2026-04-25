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


class MakerTakerFeeModel(FeeModel):
    """Generic maker/taker fee model. Callers pass `is_maker=True` to the
    `compute_fee` kwarg when the fill was a passive / resting fill.

    Phase 3 T3.3 — matches the Rust `FeeModel::MakerTaker` variant at 1e-9.
    """

    def __init__(self, maker_bps: float = 1.0, taker_bps: float = 5.0):
        # bps → decimal. Negative values express rebates (maker rebate = -2bps → -0.0002).
        self.maker_fee = maker_bps / 10_000.0
        self.taker_fee = taker_bps / 10_000.0

    def compute_fee(self, fill: Fill, is_maker: bool = False) -> float:
        notional = abs(fill.size) * fill.price
        rate = self.maker_fee if is_maker else self.taker_fee
        return notional * rate


class HyperliquidFeeModel(MakerTakerFeeModel):
    """Hyperliquid standard perps fee schedule: 3.5 bps taker / 1 bp maker.

    Tier-based discounts (vip1..vip6) are out of scope for Phase 3 T3.3;
    callers needing the discount can subclass with overridden rates.
    """

    def __init__(self, maker_bps: float = 1.0, taker_bps: float = 3.5):
        super().__init__(maker_bps=maker_bps, taker_bps=taker_bps)
