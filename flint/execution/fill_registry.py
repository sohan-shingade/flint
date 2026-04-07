"""Registry for creating venue-specific fill models.

Maps venue names to their fill model classes using lazy imports.
Unknown venues fall back to the default ``FillPipeline``.
"""
from __future__ import annotations

import importlib
from typing import Dict

from .fill_models import FillModel, FillPipeline

_VENUE_FILL_CLASSES: Dict[str, str] = {
    "drift": "flint.execution.fill_drift:DriftFillModel",
    "hyperliquid": "flint.execution.fill_hyperliquid:HyperliquidFillModel",
    "jupiter": "flint.execution.fill_jupiter:JupiterFillModel",
    "binance": "flint.execution.fill_cex:BinanceFillModel",
    "okx": "flint.execution.fill_cex:OkxFillModel",
    "bybit": "flint.execution.fill_cex:BybitFillModel",
}


def create_fill_model(venue: str, **kwargs) -> FillModel:
    """Create a fill model for a specific venue.

    Returns the venue-specific model if registered, otherwise ``FillPipeline``.
    """
    entry = _VENUE_FILL_CLASSES.get(venue)
    if entry is None:
        return FillPipeline()
    module_path, class_name = entry.rsplit(":", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls(**kwargs)


def create_venue_fill_models(venues: list, **kwargs) -> Dict[str, FillModel]:
    """Create fill models for a list of venues.

    Returns ``{venue_name: FillModel}`` for each venue in the list.
    """
    return {v: create_fill_model(v, **kwargs) for v in venues}
