"""venues — one adapter per exchange, keyed by market structure (§7).

Hyperliquid is the only executable v1 venue (D28); Jupiter is v1.x. Slice 3.2
ships the ``MarketStructure`` abstraction and the ``VenueSpec`` (cited hard
numbers, D14); the ``HYPERLIQUID`` spec is the single source of those constants
for the fill engine.
"""

from __future__ import annotations

from .spec import (
    HYPERLIQUID,
    HYPERLIQUID_LIQUIDATION,
    LatencyProfile,
    LiquidationSpec,
    MaintTier,
    VenueSpec,
)
from .structure import MarketStructure

__all__ = [
    "MarketStructure",
    "VenueSpec",
    "LatencyProfile",
    "LiquidationSpec",
    "MaintTier",
    "HYPERLIQUID",
    "HYPERLIQUID_LIQUIDATION",
]
