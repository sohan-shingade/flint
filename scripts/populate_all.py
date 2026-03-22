#!/usr/bin/env python3
"""Populate DuckDB with historical data for ALL Drift markets.

This script downloads trade records from Drift's public S3 bucket for
every available perp and spot market, aggregates them into 1-hour candles,
and stores them in DuckDB. It also fetches funding rate history.

Designed to run overnight — handles errors gracefully, logs progress,
and can be restarted safely (upserts skip existing data).

Usage:
    python scripts/populate_all.py                    # full run (all markets, 1 year)
    python scripts/populate_all.py --days 30          # last 30 days only
    python scripts/populate_all.py --markets SOL-PERP BTC-PERP  # specific markets
    python scripts/populate_all.py --spot-only        # spot markets only
    python scripts/populate_all.py --perp-only        # perp markets only
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flint.providers.drift_s3 import DriftS3Provider
from flint.providers.drift_api import DriftDataProvider
from flint.store import FlintStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("populate")

# ---------------------------------------------------------------------------
# All Drift perp markets with S3 data (verified 2025-01-01)
# ---------------------------------------------------------------------------
DRIFT_PERP_MARKETS = {
    0: "SOL-PERP",
    1: "BTC-PERP",
    2: "ETH-PERP",
    3: "APT-PERP",
    4: "1MBONK-PERP",
    5: "POL-PERP",
    6: "ARB-PERP",
    7: "DOGE-PERP",
    8: "BNB-PERP",
    9: "SUI-PERP",
    10: "1MPEPE-PERP",
    11: "OP-PERP",
    12: "RENDER-PERP",
    13: "XRP-PERP",
    14: "HNT-PERP",
    15: "INJ-PERP",
    16: "LINK-PERP",
    17: "RLB-PERP",
    18: "PYTH-PERP",
    19: "TIA-PERP",
    20: "JTO-PERP",
    21: "SEI-PERP",
    22: "AVAX-PERP",
    23: "WIF-PERP",
    24: "JUP-PERP",
    25: "DYM-PERP",
    26: "TAO-PERP",
    27: "W-PERP",
    28: "KMNO-PERP",
    29: "TNSR-PERP",
    30: "DRIFT-PERP",
    31: "CLOUD-PERP",
    32: "IO-PERP",
    33: "ZEX-PERP",
    34: "POPCAT-PERP",
    35: "1KWEN-PERP",
}

# Additional perps found on S3 (indices unknown / newer)
DRIFT_PERP_EXTRA = [
    "MOTHER-PERP",
    "LTC-PERP",
    "RAY-PERP",
    "PENGU-PERP",
    "ME-PERP",
    "PNUT-PERP",
]

# Drift spot markets with S3 trade data
DRIFT_SPOT_MARKETS = [
    "SOL",
    "JTO",
    "WIF",
    "JUP",
    "DRIFT",
    "POPCAT",
]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_DAYS = 365
RESOLUTIONS = [3600]  # 1-hour candles. Add 60 for 1-min if you want granular data
BATCH_DAYS = 30       # Process N days at a time to limit memory


def fetch_market_candles(
    provider: DriftS3Provider,
    store: FlintStore,
    market: str,
    days: int,
    resolution_s: int = 3600,
    end_ts: int = 0,
) -> int:
    """Fetch and store candles for a single market. Returns total candle count."""
    if end_ts == 0:
        end_ts = int(time.time())
    total_stored = 0
    # Work backwards from end_ts in BATCH_DAYS chunks
    cursor_end = end_ts

    while cursor_end > end_ts - days * 86400:
        cursor_start = max(cursor_end - BATCH_DAYS * 86400, end_ts - days * 86400)
        start_ts = cursor_start
        batch_end = cursor_end

        start_date = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        batch_end_date = datetime.fromtimestamp(batch_end, tz=timezone.utc).strftime("%Y-%m-%d")

        try:
            candles = provider.fetch_candles(market, resolution_s, start_ts, batch_end)
            if candles:
                count = store.upsert_candles(candles)
                total_stored += count
                log.info("  %s: %s→%s  %d candles", market, start_date, batch_end_date, count)
            else:
                log.debug("  %s: %s→%s  no data", market, start_date, batch_end_date)
        except Exception as e:
            log.warning("  %s: %s→%s  ERROR: %s", market, start_date, batch_end_date, e)

        cursor_end = cursor_start

    return total_stored


def fetch_funding_rates(
    store: FlintStore,
    market: str,
    market_index: int,
) -> int:
    """Fetch recent funding rates for a market."""
    provider = DriftDataProvider()
    try:
        rates = provider.fetch_funding_rates(market_index=market_index, market_name=market, limit=1000)
        if rates:
            count = store.upsert_funding_rates(rates)
            log.info("  %s: %d funding rates", market, count)
            return count
        return 0
    except Exception as e:
        log.warning("  %s: funding rates ERROR: %s", market, e)
        return 0
    finally:
        provider.close()


def main():
    parser = argparse.ArgumentParser(description="Populate Flint DB with Drift historical data")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"Days of history to fetch (default: {DEFAULT_DAYS})")
    parser.add_argument("--end-date", type=str, default=None,
                        help="End date YYYY-MM-DD (default: 2025-12-31, since S3 data lags)")
    parser.add_argument("--markets", nargs="+", default=None,
                        help="Specific markets to fetch (e.g., SOL-PERP BTC-PERP)")
    parser.add_argument("--perp-only", action="store_true",
                        help="Only fetch perp markets")
    parser.add_argument("--spot-only", action="store_true",
                        help="Only fetch spot markets")
    parser.add_argument("--no-funding", action="store_true",
                        help="Skip funding rate collection")
    parser.add_argument("--resolution", type=int, default=3600,
                        help="Candle resolution in seconds (default: 3600)")
    parser.add_argument("--db", type=str, default="./data/flint.duckdb",
                        help="Path to DuckDB file")
    args = parser.parse_args()

    # Build market list
    # perp_indexed: {name: index} for markets with known indices (needed for funding)
    if args.markets:
        perp_indexed = {m: i for i, m in DRIFT_PERP_MARKETS.items() if m in args.markets}
        spot_markets = [m for m in DRIFT_SPOT_MARKETS if m in args.markets]
        extra_perps = [m for m in DRIFT_PERP_EXTRA if m in args.markets]
    elif args.spot_only:
        perp_indexed = {}
        spot_markets = DRIFT_SPOT_MARKETS
        extra_perps = []
    elif args.perp_only:
        perp_indexed = {m: i for i, m in DRIFT_PERP_MARKETS.items()}
        spot_markets = []
        extra_perps = list(DRIFT_PERP_EXTRA)
    else:
        perp_indexed = {m: i for i, m in DRIFT_PERP_MARKETS.items()}
        spot_markets = list(DRIFT_SPOT_MARKETS)
        extra_perps = list(DRIFT_PERP_EXTRA)

    all_perp_names = list(perp_indexed.keys()) + extra_perps
    total_markets = len(all_perp_names) + len(spot_markets)

    log.info("=" * 60)
    log.info("Flint Data Population")
    log.info("=" * 60)
    log.info("Database:    %s", args.db)
    log.info("Days:        %d", args.days)
    log.info("Resolution:  %ds (%s candles)", args.resolution,
             "1-hour" if args.resolution == 3600 else f"{args.resolution}s")
    log.info("Perp markets: %d", len(all_perp_names))
    log.info("Spot markets: %d", len(spot_markets))
    log.info("Total:       %d markets", total_markets)
    log.info("Estimated time: %d-%d minutes",
             total_markets * 1, total_markets * 3)
    log.info("=" * 60)

    # Compute end timestamp
    if args.end_date:
        end_dt = datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_ts = int(end_dt.timestamp())
    else:
        # Default: end of 2025 (S3 data lags, no 2026 data yet)
        end_ts = int(datetime(2025, 12, 31, tzinfo=timezone.utc).timestamp())

    end_date_str = datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    start_date_str = datetime.fromtimestamp(end_ts - args.days * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
    log.info("Date range:  %s → %s", start_date_str, end_date_str)

    # Init store
    store = FlintStore(args.db)
    provider = DriftS3Provider()

    start_time = time.time()
    total_candles = 0
    total_funding = 0
    completed = 0
    errors = 0

    # --- PERP MARKETS ---
    if all_perp_names:
        log.info("")
        log.info("--- PERP MARKETS (%d) ---", len(all_perp_names))
        for market in all_perp_names:
            completed += 1
            pct = (completed / total_markets) * 100
            log.info("[%d/%d %.0f%%] %s", completed, total_markets, pct, market)
            try:
                count = fetch_market_candles(
                    provider, store, market, args.days, args.resolution,
                    end_ts=end_ts,
                )
                total_candles += count
            except Exception as e:
                log.error("  FAILED: %s — %s", market, e)
                errors += 1

        # Funding rates for indexed perps
        if not args.no_funding:
            log.info("")
            log.info("--- FUNDING RATES ---")
            for market, idx in perp_indexed.items():
                try:
                    count = fetch_funding_rates(store, market, idx)
                    total_funding += count
                except Exception as e:
                    log.warning("  %s funding ERROR: %s", market, e)

    # --- SPOT MARKETS ---
    if spot_markets:
        log.info("")
        log.info("--- SPOT MARKETS (%d) ---", len(spot_markets))
        for market in spot_markets:
            completed += 1
            pct = (completed / total_markets) * 100
            log.info("[%d/%d %.0f%%] %s", completed, total_markets, pct, market)
            try:
                count = fetch_market_candles(
                    provider, store, market, args.days, args.resolution,
                    end_ts=end_ts,
                )
                total_candles += count
            except Exception as e:
                log.error("  FAILED: %s — %s", market, e)
                errors += 1

    # --- SUMMARY ---
    elapsed = time.time() - start_time
    provider.close()
    store.close()

    log.info("")
    log.info("=" * 60)
    log.info("COMPLETE")
    log.info("=" * 60)
    log.info("Total candles stored:    %d", total_candles)
    log.info("Total funding rates:     %d", total_funding)
    log.info("Markets processed:       %d", completed)
    log.info("Errors:                  %d", errors)
    log.info("Elapsed:                 %.1f minutes", elapsed / 60)
    log.info("Database:                %s", args.db)

    # Print data inventory
    log.info("")
    log.info("--- DATA INVENTORY ---")
    store2 = FlintStore(args.db)
    try:
        rows = store2._conn.execute(
            "SELECT market, resolution_s, COUNT(*) as cnt, "
            "MIN(ts) as first_ts, MAX(ts) as last_ts "
            "FROM candles GROUP BY market, resolution_s "
            "ORDER BY market"
        ).fetchall()
        for market, res, cnt, first_ts, last_ts in rows:
            first = datetime.fromtimestamp(first_ts, tz=timezone.utc).strftime("%Y-%m-%d")
            last = datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime("%Y-%m-%d")
            log.info("  %-16s %5ds  %6d candles  %s → %s", market, res, cnt, first, last)
    finally:
        store2.close()


if __name__ == "__main__":
    main()
