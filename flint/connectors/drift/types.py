"""Drift-specific types."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DriftPerpMarket:
    market_index: int
    symbol: str  # e.g. "SOL-PERP"
    oracle: str  # oracle pubkey
    base_decimals: int
    quote_decimals: int
    tick_size: float
    min_order_size: float
    maker_fee: float  # negative = rebate
    taker_fee: float
    imf_factor: int  # initial margin fraction
    mmf_factor: int  # maintenance margin fraction


@dataclass
class DriftUserPosition:
    market_index: int
    market_symbol: str
    base_asset_amount: float  # positive = long, negative = short
    quote_entry_amount: float
    quote_break_even_amount: float
    last_cumulative_funding_rate: float
    open_orders: int
    unsettled_pnl: float

    @property
    def entry_price(self) -> float:
        if self.base_asset_amount == 0:
            return 0.0
        return abs(self.quote_entry_amount / self.base_asset_amount)

    @property
    def is_long(self) -> bool:
        return self.base_asset_amount > 0

    @property
    def size(self) -> float:
        return abs(self.base_asset_amount)

    def unrealised_pnl(self, oracle_price: float) -> float:
        return (oracle_price - self.entry_price) * self.base_asset_amount
