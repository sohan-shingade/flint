"""Pluggable price-source layer for the live PnL ticker.

Originally `PriceTicker` polled `dlob.drift.trade` directly. After
the Drift outage in mid-2026 (see CLAUDE.md rules), the layer
generalized to a fallback chain: Hyperliquid → Drift DLOB → DuckDB
last-candle. New sources slot in by implementing `PriceSource` and
registering with the chain.

Public surface:

    from flint.paper.price_sources import (
        PriceSource, PriceQuote, SourceHealth,
        HyperliquidInfoSource, DriftDLOBSource, LocalDuckDBSource,
        default_chain,
    )

`default_chain(store)` returns the recommended priority list:
    [HyperliquidInfoSource, DriftDLOBSource, LocalDuckDBSource(store)]

Drift is included even though it's currently offline — when it comes
back, callers automatically benefit from the more native mid; in the
meantime the chain falls through to HL or the cache without ceremony.
"""
from __future__ import annotations

from .base import PriceQuote, PriceSource, SourceHealth
from .drift_dlob import DriftDLOBSource
from .duckdb_cache import LocalDuckDBSource
from .hyperliquid import HyperliquidInfoSource


def default_chain(store=None):
    """Recommended source priority. Pass `store` to enable the
    DuckDB last-candle fallback (skipped if `store` is None)."""
    chain = [HyperliquidInfoSource(), DriftDLOBSource()]
    if store is not None:
        chain.append(LocalDuckDBSource(store))
    return chain


__all__ = [
    "PriceQuote",
    "PriceSource",
    "SourceHealth",
    "HyperliquidInfoSource",
    "DriftDLOBSource",
    "LocalDuckDBSource",
    "default_chain",
]
