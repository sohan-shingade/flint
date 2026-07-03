"""data.ingest.backfillers — REST + bulk CSV/S3 backfill (§9.1 class 2, §10).

v1 (D28) ships the Hyperliquid workers: a REST provider (candles/funding, paged
around HL's 5000-candle cap) and the free public S3 archive backfiller (l2Book
depth + daily asset_ctxs OI/mark/oracle). It also ships the **read-only** CEX
funding lane (§10) — CCXT-based funding-rate (+OI) ingestion for binance/bybit/okx
that feeds the cross-venue funding & basis lab without any execution surface.
data.binance.vision and OKX/Gate/Bybit *depth* archives defer with their venues.
"""

from .cex_funding import (
    CEX_INCEPTION_MS,
    CcxtExchange,
    CcxtExchangeFactory,
    CexFundingProvider,
    LazyCcxtFactory,
    cex_symbol,
    funding_rate_to_hourly,
)
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
    "CEX_INCEPTION_MS",
    "CcxtExchange",
    "CcxtExchangeFactory",
    "CexFundingProvider",
    "LazyCcxtFactory",
    "cex_symbol",
    "funding_rate_to_hourly",
]
