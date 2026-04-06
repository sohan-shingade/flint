"""Auto-migration for Pyth price data.

On first run after upgrade, detects markets with existing venue candles
but no Pyth candles, and backfills from Pyth Benchmarks API.
"""
from __future__ import annotations
import logging

from .providers.pyth_candles import PythCandleProvider

logger = logging.getLogger(__name__)


def run_pyth_migration(store) -> dict:
    """Migrate existing markets to Pyth candle data.

    Returns summary: {markets_migrated: [...], candles_downloaded: N, errors: [...]}
    Idempotent — safe to run multiple times.
    """
    result = {"markets_migrated": [], "candles_downloaded": 0, "errors": []}

    markets = store.get_markets_needing_pyth_migration()
    if not markets:
        return result

    logger.info(f"Pyth migration: {len(markets)} markets need migration: {markets}")

    provider = PythCandleProvider()
    try:
        for market in markets:
            date_range = store.get_market_date_range(market)
            if not date_range:
                continue
            start_ts, end_ts = date_range
            logger.info(f"Migrating {market}: {start_ts} -> {end_ts}")
            try:
                candles = provider.fetch_candles(market, 3600, start_ts, end_ts)
                if candles:
                    count = store.upsert_candles(candles)
                    result["markets_migrated"].append(market)
                    result["candles_downloaded"] += count
                    logger.info(f"Migrated {market}: {count} Pyth candles")
                else:
                    result["errors"].append(f"{market}: no Pyth data available")
            except Exception as e:
                result["errors"].append(f"{market}: {e}")
                logger.warning(f"Pyth migration failed for {market}: {e}")
    finally:
        provider.close()

    return result
