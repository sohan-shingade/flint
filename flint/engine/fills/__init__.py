"""engine.fills — FillModel interface plus CLOB and oracle-pool fill models (§6.3).

The fill model is chosen by the venue's ``MarketStructure`` (§2.6): ``CLOB`` →
``ClobFillModel`` (implemented), ``ORACLE_POOL`` → ``OraclePoolFillModel`` (stub,
D28). ``fill_model_for`` is that selection.
"""

from __future__ import annotations

from flint.venues import MarketStructure

from .base import FillContext, FillModel, FillResult, TradePrint
from .clob import ClobFillModel
from .latency import LatencyModel
from .naive import NaiveFillModel
from .oracle_pool import OraclePoolFillModel


def fill_model_for(structure: MarketStructure) -> FillModel:
    """Return the fill model a venue of this ``structure`` uses (§6.3)."""
    if structure is MarketStructure.CLOB:
        return ClobFillModel()
    if structure is MarketStructure.ORACLE_POOL:
        return OraclePoolFillModel()
    raise ValueError(f"no fill model for market structure {structure!r}")


__all__ = [
    "FillModel",
    "FillContext",
    "FillResult",
    "TradePrint",
    "NaiveFillModel",
    "ClobFillModel",
    "OraclePoolFillModel",
    "LatencyModel",
    "fill_model_for",
]
