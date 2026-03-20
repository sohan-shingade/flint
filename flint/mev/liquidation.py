"""Liquidation opportunity scanner for Drift/Mango positions.

Monitors positions approaching their liquidation price and computes
estimated profit from executing the liquidation.
"""
from __future__ import annotations

from typing import Dict, List

from ..models import LiquidationOpportunity, Side


def compute_liquidation_price(
    entry_price: float,
    size: float,
    collateral: float,
    maintenance_margin_ratio: float = 0.05,
    is_long: bool = True,
) -> float:
    """Compute the oracle price at which a position gets liquidated.

    For longs:  liq_price = entry_price - (collateral - mmr * notional) / size
    For shorts: liq_price = entry_price + (collateral - mmr * notional) / size
    """
    if size == 0:
        return 0.0
    abs_size = abs(size)
    notional = entry_price * abs_size
    margin_req = maintenance_margin_ratio * notional
    buffer = collateral - margin_req
    if is_long:
        return entry_price - buffer / abs_size
    else:
        return entry_price + buffer / abs_size


class LiquidationScanner:
    """Scans positions for liquidation opportunities.

    Usage::

        scanner = LiquidationScanner()
        opps = scanner.scan_positions(positions, oracle_prices)
    """

    def __init__(
        self,
        maintenance_margin_ratio: float = 0.05,
        liquidation_fee: float = 0.01,  # 1% liquidation penalty
        min_profit: float = 0.0,
    ) -> None:
        self.maintenance_margin_ratio = maintenance_margin_ratio
        self.liquidation_fee = liquidation_fee
        self.min_profit = min_profit

    def scan_positions(
        self,
        positions: List[Dict],
        oracle_prices: Dict[str, float],
    ) -> List[LiquidationOpportunity]:
        """Check a list of positions against current oracle prices.

        Each position dict should have:
          - user_account: str
          - market: str
          - size: float (positive=long, negative=short)
          - entry_price: float
          - collateral: float
          - protocol: str (optional, default "drift")
        """
        opportunities: List[LiquidationOpportunity] = []
        for pos in positions:
            market = pos["market"]
            oracle_price = oracle_prices.get(market, 0.0)
            if oracle_price == 0:
                continue

            size = pos["size"]
            is_long = size > 0
            entry_price = pos["entry_price"]
            collateral = pos["collateral"]

            liq_price = compute_liquidation_price(
                entry_price, size, collateral,
                self.maintenance_margin_ratio, is_long,
            )

            # Check if position is liquidatable
            liquidatable = (
                (is_long and oracle_price <= liq_price) or
                (not is_long and oracle_price >= liq_price)
            )
            if not liquidatable:
                continue

            # Estimate profit: liquidation fee * notional
            abs_size = abs(size)
            notional = oracle_price * abs_size
            estimated_profit = notional * self.liquidation_fee

            # Margin ratio: how far past liquidation
            margin_ratio = collateral / notional if notional else 0.0

            if estimated_profit >= self.min_profit:
                opportunities.append(LiquidationOpportunity(
                    protocol=pos.get("protocol", "drift"),
                    user_account=pos["user_account"],
                    market=market,
                    side=Side.LONG if is_long else Side.SHORT,
                    position_size=abs_size,
                    entry_price=entry_price,
                    liquidation_price=liq_price,
                    oracle_price=oracle_price,
                    margin_ratio=margin_ratio,
                    estimated_profit=estimated_profit,
                ))

        opportunities.sort(key=lambda o: o.estimated_profit, reverse=True)
        return opportunities
