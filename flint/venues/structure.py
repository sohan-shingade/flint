"""Market structure — how a venue actually matches trades (§2.6, §7).

The fill model a venue needs is a function of its **market structure**, not its
name. A central-limit-order-book venue (Hyperliquid, Binance) fills by crossing
the spread and walking depth; an oracle-pool venue (Jupiter, GMX) fills at an
oracle price plus a pool-impact fee. Keying the fill model off this enum — rather
than off ``venue == "hyperliquid"`` — is what lets a second CLOB venue reuse the
whole CLOB fill path (§7).

v1 implements ``CLOB`` (Hyperliquid, the only executable venue — D28). The
``ORACLE_POOL`` value exists so the ``FillModel`` seam is designed against both
structures now; its fill model is an interface stub until Jupiter lands in v1.x.
"""

from __future__ import annotations

from enum import StrEnum


class MarketStructure(StrEnum):
    """How a venue matches orders — selects the fill model (§2.6, §6.3)."""

    CLOB = "clob"  # central limit order book: cross spread, walk depth
    ORACLE_POOL = "oracle_pool"  # oracle-priced pool + impact fee (Jupiter/GMX — v1.x)
