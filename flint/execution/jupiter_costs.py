"""Jupiter Perps transaction cost model.

Provides JupiterCostEstimate dataclass and JupiterTxCostModel for estimating
the full round-trip cost of a Jupiter Perps position, including open/close fees,
price impact, and borrow (funding) costs.
"""
from __future__ import annotations
from dataclasses import dataclass

_FEE_RATE = 0.0006  # 0.06% flat
_IMPACT_K = 0.00001


@dataclass
class JupiterCostEstimate:
    open_fee: float
    close_fee: float
    price_impact: float
    borrow_cost: float
    total: float


class JupiterTxCostModel:
    def __init__(self, fee_rate: float = _FEE_RATE, impact_k: float = _IMPACT_K) -> None:
        self._fee_rate = fee_rate
        self._impact_k = impact_k

    def estimate_borrow(self, size_usd: float, hours: float, rate_hourly: float) -> float:
        return rate_hourly * size_usd * hours

    def estimate_round_trip(self, size_usd: float, hold_hours: float, rate_hourly: float) -> JupiterCostEstimate:
        open_fee = round(self._fee_rate * size_usd, 10)
        close_fee = round(self._fee_rate * size_usd, 10)
        price_impact = self._impact_k * (size_usd ** 2) / 1_000_000
        borrow_cost = self.estimate_borrow(size_usd, hold_hours, rate_hourly)
        total = open_fee + close_fee + price_impact + borrow_cost
        return JupiterCostEstimate(
            open_fee=open_fee,
            close_fee=close_fee,
            price_impact=price_impact,
            borrow_cost=borrow_cost,
            total=total,
        )
