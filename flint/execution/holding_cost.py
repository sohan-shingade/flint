"""Holding cost models for different venue fee structures.

FundingCostModel: Periodic funding rates (Drift, Hyperliquid) — can be positive or negative.
BorrowCostModel: Continuous borrow fees (Jupiter Perps) — always positive.
"""
from __future__ import annotations
from abc import ABC, abstractmethod


class HoldingCostModel(ABC):
    @abstractmethod
    def cost_at_bar(self, **kwargs) -> float:
        """Unrealized holding cost at a bar for margin/liquidation checks."""

    @abstractmethod
    def cost_at_close(self, **kwargs) -> float:
        """Realized holding cost when position is closed."""


class FundingCostModel(HoldingCostModel):
    def cost_at_bar(self, *, side: str, size_usd: float, rate: float) -> float:
        if side == "long":
            return rate * size_usd
        else:
            return -(rate * size_usd)

    def cost_at_close(self, *, side: str, size_usd: float, rate: float) -> float:
        return self.cost_at_bar(side=side, size_usd=size_usd, rate=rate)


class BorrowCostModel(HoldingCostModel):
    def cost_at_bar(self, *, cumulative_entry: float, cumulative_now: float, size_usd: float) -> float:
        return max(0.0, (cumulative_now - cumulative_entry) * size_usd)

    def cost_at_close(self, *, cumulative_entry: float, cumulative_close: float, size_usd: float) -> float:
        return max(0.0, (cumulative_close - cumulative_entry) * size_usd)
