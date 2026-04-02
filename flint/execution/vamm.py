"""VammCurve — constant-product AMM model for Drift fill price estimation."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional

DEFAULT_SQRT_K: Dict[str, float] = {
    "SOL-PERP": 5_000_000, "BTC-PERP": 50_000_000, "ETH-PERP": 20_000_000,
    "DOGE-PERP": 1_000_000, "ARB-PERP": 1_000_000, "SUI-PERP": 1_000_000,
    "XRP-PERP": 2_000_000, "LINK-PERP": 1_000_000, "OP-PERP": 500_000,
    "INJ-PERP": 500_000, "WIF-PERP": 500_000,
}


class VammCurve:
    """Constant-product virtual AMM with peg multiplier."""

    def __init__(self, sqrt_k: float, peg_multiplier: float):
        self._sqrt_k = sqrt_k
        self._k = sqrt_k * sqrt_k
        self._peg = peg_multiplier
        self._base_reserve = sqrt_k
        self._quote_reserve = sqrt_k

    @classmethod
    def from_oracle_price(cls, sqrt_k: float, oracle_price: float) -> "VammCurve":
        return cls(sqrt_k=sqrt_k, peg_multiplier=oracle_price)

    @property
    def reserve_price(self) -> float:
        if self._base_reserve <= 0:
            return 0.0
        return (self._quote_reserve / self._base_reserve) * self._peg

    def fill_price(self, base_amount: float, direction: str) -> float:
        if base_amount <= 0:
            return self.reserve_price
        if direction == "long":
            new_base = self._base_reserve - base_amount
            if new_base <= 0:
                return self.reserve_price * (1 + base_amount / self._base_reserve)
            new_quote = self._k / new_base
            quote_delta = new_quote - self._quote_reserve
            return (quote_delta / base_amount) * self._peg
        else:
            new_base = self._base_reserve + base_amount
            new_quote = self._k / new_base
            quote_delta = self._quote_reserve - new_quote
            return (quote_delta / base_amount) * self._peg

    def impact_bps(self, base_amount: float, direction: str, oracle_price: float) -> float:
        if oracle_price <= 0 or base_amount <= 0:
            return 0.0
        fp = self.fill_price(base_amount, direction)
        return abs(fp - oracle_price) / oracle_price * 10_000


@dataclass
class VammAccuracyReport:
    market: str
    num_fills: int
    vamm_mae_bps: float
    orderbook_mae_bps: float
    sqrt_mae_bps: float
    close_mae_bps: float
    recommended_model: str

    def summary(self) -> str:
        return (
            f"Fill Model Accuracy: {self.market}\n"
            f"{'=' * 45}\n"
            f"Fills:     {self.num_fills}\n"
            f"vAMM MAE:       {self.vamm_mae_bps:.1f} bps\n"
            f"Orderbook MAE:  {self.orderbook_mae_bps:.1f} bps\n"
            f"Sqrt MAE:       {self.sqrt_mae_bps:.1f} bps\n"
            f"Close MAE:      {self.close_mae_bps:.1f} bps\n"
            f"Recommended:    {self.recommended_model}\n"
            f"{'=' * 45}"
        )

    def to_dict(self) -> dict:
        return {
            "market": self.market, "num_fills": self.num_fills,
            "vamm_mae_bps": self.vamm_mae_bps, "orderbook_mae_bps": self.orderbook_mae_bps,
            "sqrt_mae_bps": self.sqrt_mae_bps, "close_mae_bps": self.close_mae_bps,
            "recommended_model": self.recommended_model,
        }
