"""Transaction cost models for Solana and cross-venue trading.

Provides CostEstimate dataclass, TxCostModel ABC, and venue-specific
implementations for Drift (Solana), Hyperliquid, and CEX venues.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# CostEstimate
# ---------------------------------------------------------------------------

@dataclass
class CostEstimate:
    """Breakdown of all costs for a single trade execution."""
    exchange_fee: float   # taker/maker fee paid to the exchange (USD)
    network_fee: float    # gas / priority fee (USD)
    bundle_tip: float     # Jito bundle tip or equivalent (USD)
    impact_est: float     # estimated market impact cost (USD)

    @property
    def total(self) -> float:
        """Sum of all cost components."""
        return self.exchange_fee + self.network_fee + self.bundle_tip + self.impact_est

    def to_dict(self) -> Dict[str, float]:
        return {
            "exchange_fee": self.exchange_fee,
            "network_fee": self.network_fee,
            "bundle_tip": self.bundle_tip,
            "impact_est": self.impact_est,
            "total": self.total,
        }


# ---------------------------------------------------------------------------
# TxCostModel ABC
# ---------------------------------------------------------------------------

class TxCostModel(ABC):
    """Abstract base class for transaction cost models."""

    @abstractmethod
    def estimate(
        self,
        market: str,
        size: float,
        price: float,
        urgency: str = "normal",
    ) -> CostEstimate:
        """Estimate the full cost of executing a trade.

        Args:
            market: Market symbol, e.g. "SOL-PERP".
            size: Base-asset quantity (positive).
            price: Expected fill price in USD.
            urgency: "normal" or "urgent". Urgent may use higher priority fees.

        Returns:
            CostEstimate with per-component breakdown.
        """
        ...

    @property
    @abstractmethod
    def venue(self) -> str:
        """Venue identifier, e.g. "drift", "hyperliquid", "binance"."""
        ...


# ---------------------------------------------------------------------------
# SolanaTxCostModel  (Drift / on-chain Solana)
# ---------------------------------------------------------------------------

_LAMPORTS_PER_SOL = 1_000_000_000


@dataclass
class SolanaTxCostModel(TxCostModel):
    """Cost model for Solana-based venues (e.g. Drift).

    Network costs are computed from lamport amounts and a SOL/USD price.
    Supports urgency levels that select between p50/p90 historical fees.

    Args:
        priority_fee_lamports: Base priority fee in lamports (used for "normal").
        jito_tip_lamports: Jito bundle tip in lamports.
        sol_price_usd: Current SOL price in USD for lamport conversion.
        exchange_fee_bps: Taker fee rate in basis points (default 5 bps = 0.05 %).
        historical_fees: Optional dict with "p50" and "p90" fee levels in lamports.
            When provided, urgency="normal" uses p50 and urgency="urgent" uses p90.
    """
    priority_fee_lamports: int = 5_000
    jito_tip_lamports: int = 10_000
    sol_price_usd: float = 150.0
    exchange_fee_bps: float = 5.0
    historical_fees: Optional[Dict[str, int]] = None

    @property
    def venue(self) -> str:
        return "drift"

    def _lamports_to_usd(self, lamports: int) -> float:
        return (lamports / _LAMPORTS_PER_SOL) * self.sol_price_usd

    def estimate(
        self,
        market: str,
        size: float,
        price: float,
        urgency: str = "normal",
    ) -> CostEstimate:
        notional = size * price

        # Exchange fee
        exchange_fee = notional * (self.exchange_fee_bps / 10_000)

        # Priority fee — use historical percentile if available
        if self.historical_fees:
            key = "p90" if urgency == "urgent" else "p50"
            priority_lamports = self.historical_fees.get(key, self.priority_fee_lamports)
        else:
            priority_lamports = self.priority_fee_lamports

        network_fee = self._lamports_to_usd(priority_lamports)

        # Bundle tip
        bundle_tip = self._lamports_to_usd(self.jito_tip_lamports)

        return CostEstimate(
            exchange_fee=exchange_fee,
            network_fee=network_fee,
            bundle_tip=bundle_tip,
            impact_est=0.0,
        )


# ---------------------------------------------------------------------------
# HyperliquidTxCostModel
# ---------------------------------------------------------------------------

@dataclass
class HyperliquidTxCostModel(TxCostModel):
    """Cost model for Hyperliquid (EVM L1, negligible network costs).

    Args:
        l1_cost_usd: Flat L1 gas cost per transaction in USD.
        exchange_fee_bps: Taker fee in basis points (default 3.5 bps).
    """
    l1_cost_usd: float = 0.001
    exchange_fee_bps: float = 3.5

    @property
    def venue(self) -> str:
        return "hyperliquid"

    def estimate(
        self,
        market: str,
        size: float,
        price: float,
        urgency: str = "normal",
    ) -> CostEstimate:
        notional = size * price
        exchange_fee = notional * (self.exchange_fee_bps / 10_000)
        return CostEstimate(
            exchange_fee=exchange_fee,
            network_fee=self.l1_cost_usd,
            bundle_tip=0.0,
            impact_est=0.0,
        )


# ---------------------------------------------------------------------------
# CexTxCostModel
# ---------------------------------------------------------------------------

@dataclass
class CexTxCostModel(TxCostModel):
    """Cost model for centralised exchanges (no on-chain fees).

    Args:
        exchange_fee_bps: Taker fee in basis points (default 5.0 bps).
        venue_name: Venue identifier string (e.g. "binance", "okx").
    """
    exchange_fee_bps: float = 5.0
    venue_name: str = "cex"

    @property
    def venue(self) -> str:
        return self.venue_name

    def estimate(
        self,
        market: str,
        size: float,
        price: float,
        urgency: str = "normal",
    ) -> CostEstimate:
        notional = size * price
        exchange_fee = notional * (self.exchange_fee_bps / 10_000)
        return CostEstimate(
            exchange_fee=exchange_fee,
            network_fee=0.0,
            bundle_tip=0.0,
            impact_est=0.0,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_tx_cost_model(venue: str) -> TxCostModel:
    """Return the appropriate TxCostModel for a given venue name.

    Args:
        venue: Venue identifier string.

    Returns:
        A TxCostModel instance. Falls back to CexTxCostModel for unknown venues.
    """
    venue_lower = venue.lower()
    if venue_lower == "drift":
        return SolanaTxCostModel()
    if venue_lower == "hyperliquid":
        return HyperliquidTxCostModel()
    return CexTxCostModel(venue_name=venue_lower)
