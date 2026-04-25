"""Drift-specific simulation extensions.

Adds Drift protocol simulation details:
- Maker/taker fee tiers with volume-based discounts
- Maker rebate tracking
- JIT auction participation modeling
- Margin and leverage simulation
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import Fill


@dataclass
class DriftMarketConfig:
    """Drift perp market simulation parameters."""
    market_index: int
    symbol: str
    maker_fee: float = -0.0002  # negative = rebate
    taker_fee: float = 0.001
    maintenance_margin: float = 0.05  # 5%
    initial_margin: float = 0.10  # 10%
    max_leverage: float = 10.0
    jit_maker_rebate: float = 0.0005  # 5bps rebate for JIT fills


# Pre-configured Drift markets
DRIFT_MARKETS = {
    "SOL-PERP": DriftMarketConfig(market_index=0, symbol="SOL-PERP"),
    "BTC-PERP": DriftMarketConfig(market_index=1, symbol="BTC-PERP",
                                   taker_fee=0.0008),
    "ETH-PERP": DriftMarketConfig(market_index=2, symbol="ETH-PERP",
                                   taker_fee=0.0008),
}


class DriftFeeModelV2:
    """Drift protocol fee model with maker/taker distinction and volume tiers.

    Volume-based fee tiers (30-day volume in USD):
    Tier 0: < $1M   — taker 10bps, maker -2bps
    Tier 1: $1M-5M  — taker 8bps,  maker -2bps
    Tier 2: $5M-25M — taker 6bps,  maker -3bps
    Tier 3: > $25M  — taker 4bps,  maker -4bps
    """

    FEE_TIERS = [
        (0,       0.001,  -0.0002),  # <$1M
        (1e6,     0.0008, -0.0002),  # $1M-$5M
        (5e6,     0.0006, -0.0003),  # $5M-$25M
        (25e6,    0.0004, -0.0004),  # >$25M
    ]

    def __init__(self, thirty_day_volume: float = 0, is_maker: bool = False):
        self.is_maker = is_maker
        self.taker_fee, self.maker_fee = self._get_tier(thirty_day_volume)

    def _get_tier(self, volume: float):
        taker = 0.001
        maker = -0.0002
        for threshold, t_fee, m_fee in self.FEE_TIERS:
            if volume >= threshold:
                taker = t_fee
                maker = m_fee
        return taker, maker

    def compute_fee(self, fill: Fill) -> float:
        notional = abs(fill.size) * fill.price
        rate = self.maker_fee if self.is_maker else self.taker_fee
        return notional * rate


class MevCostModel:
    """Estimates MEV-related costs for Solana transactions.

    Includes:
    - Jito tip (priority fee for bundle inclusion)
    - Base transaction fee (5000 lamports ≈ $0.001)
    - Estimated front-running cost (for large orders)
    """

    def __init__(
        self,
        jito_tip_lamports: int = 10_000,
        sol_price_usd: float = 150.0,
        front_run_bps: float = 0.0,
    ):
        self.jito_tip_lamports = jito_tip_lamports
        self.sol_price_usd = sol_price_usd
        self.front_run_bps = front_run_bps / 10_000

    @property
    def jito_tip_usd(self) -> float:
        return (self.jito_tip_lamports / 1e9) * self.sol_price_usd

    @property
    def base_tx_fee_usd(self) -> float:
        return (5000 / 1e9) * self.sol_price_usd

    def estimate_cost(self, notional_usd: float) -> float:
        """Total MEV-related cost for a trade."""
        tip = self.jito_tip_usd
        base = self.base_tx_fee_usd
        front_run = notional_usd * self.front_run_bps
        return tip + base + front_run
