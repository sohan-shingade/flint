"""data.ingest.backfillers — REST + bulk CSV/S3 backfill (§9.1 class 2).

v1 (D28) ships the Hyperliquid workers: a REST provider (candles/funding, paged
around HL's 5000-candle cap) and the free public S3 archive backfiller (l2Book
depth + daily asset_ctxs OI/mark/oracle). data.binance.vision and OKX/Gate/Bybit
archives defer with their venues.
"""

from .hyperliquid import (
    HL_ARCHIVE_BASE,
    HL_INCEPTION_MS,
    HL_INFO_URL,
    HyperliquidRestProvider,
    HyperliquidS3Backfiller,
    coin_of,
    interval_of,
)

__all__ = [
    "HL_ARCHIVE_BASE",
    "HL_INCEPTION_MS",
    "HL_INFO_URL",
    "HyperliquidRestProvider",
    "HyperliquidS3Backfiller",
    "coin_of",
    "interval_of",
]
