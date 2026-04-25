"""CoinGecko provider — spot OHLCV for BTC, ETH, and other major tokens.

Fills the gap where Drift doesn't have spot candle data for BTC and ETH.
Uses the free CoinGecko API (no key required, 30 req/min rate limit).

Data sources:
- /coins/{id}/ohlc — OHLCV candles (1d/4d granularity depending on range)
- /coins/{id}/market_chart/range — hourly price+volume (2-90 day ranges)

For backtesting, we synthesize hourly OHLCV from market_chart price data
since the OHLC endpoint is too coarse on the free tier.
"""
from __future__ import annotations


# Phase 1 T1.3.a + D-1.3-providers — point-in-time declaration.
# Defaults are conservative — callers should verify against the
# specific source API when using this data in parity/PIT-sensitive
# contexts. Review date: 2026-04-24.
PIT_METADATA = {  # noqa: E402
    "candle_ts": "bar-close",
    "funding_ts": "exchange-time",
    "orderbook_ts": "exchange-time",
    "oi_ts": "exchange-time",
    "reviewed": "2026-04-24",
}

import logging
import time
from typing import Dict, List, Optional

import httpx

from ..models import Candle
from .registry import DataProvider

logger = logging.getLogger(__name__)

_BASE = "https://api.coingecko.com/api/v3"

# Map market symbols to CoinGecko IDs
COINGECKO_IDS: Dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "DOGE": "dogecoin",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "SUI": "sui",
    "ARB": "arbitrum",
    "XRP": "ripple",
    "BNB": "binancecoin",
    "OP": "optimism",
    "INJ": "injective-protocol",
    "TIA": "celestia",
    "SEI": "sei-network",
    "RENDER": "render-token",
    "PYTH": "pyth-network",
    "JUP": "jupiter-exchange-solana",
    "BONK": "bonk",
    "WIF": "dogwifcoin",
    "JTO": "jito-governance-token",
    "DRIFT": "drift-protocol",
    "TAO": "bittensor",
    "APT": "aptos",
    "POPCAT": "popcat",
}


class CoinGeckoProvider(DataProvider):
    """CoinGecko spot price provider for BTC, ETH, and other major tokens."""

    name = "coingecko"
    requires_api_key = False

    def __init__(self, client: Optional[httpx.Client] = None):
        self._client = client or httpx.Client(timeout=30)
        self._owns_client = client is None

    def is_available(self) -> bool:
        return True

    def supported_data_types(self) -> List[str]:
        return ["candles"]

    def resolve_id(self, symbol: str) -> Optional[str]:
        """Map a symbol like 'BTC' to a CoinGecko ID like 'bitcoin'."""
        clean = symbol.replace("-PERP", "").replace("-SPOT", "").upper()
        return COINGECKO_IDS.get(clean)

    def fetch_candles(
        self,
        market: str,
        resolution_s: int,
        start_ts: int,
        end_ts: int,
    ) -> List[Candle]:
        """Fetch OHLCV candles for a token.

        For ranges <= 90 days: uses market_chart/range (hourly price+volume),
        then aggregates into requested resolution.
        For ranges > 90 days: fetches in 90-day chunks.
        """
        coin_id = self.resolve_id(market)
        if not coin_id:
            logger.warning("No CoinGecko ID for %s", market)
            return []

        # Normalize market name
        market_name = market.replace("-PERP", "").replace("-SPOT", "").upper()

        all_candles: List[Candle] = []
        chunk_size = 89 * 86400  # 89 days per chunk (CoinGecko gives hourly for <90d)

        cursor = start_ts
        while cursor < end_ts:
            chunk_end = min(cursor + chunk_size, end_ts)
            chunk_candles = self._fetch_chunk(coin_id, market_name, resolution_s, cursor, chunk_end)
            all_candles.extend(chunk_candles)
            cursor = chunk_end
            if cursor < end_ts:
                time.sleep(1.5)  # Rate limit: 30 req/min on free tier

        # Deduplicate
        seen = set()
        unique = []
        for c in sorted(all_candles, key=lambda c: c.ts):
            if c.ts not in seen:
                seen.add(c.ts)
                unique.append(c)
        return unique

    def _fetch_chunk(
        self,
        coin_id: str,
        market_name: str,
        resolution_s: int,
        start_ts: int,
        end_ts: int,
    ) -> List[Candle]:
        """Fetch one chunk (<=90 days) of data."""
        try:
            url = f"{_BASE}/coins/{coin_id}/market_chart/range"
            params = {"vs_currency": "usd", "from": start_ts, "to": end_ts}

            resp = None
            for attempt in range(3):
                try:
                    resp = self._client.get(url, params=params)
                except Exception as e:
                    logger.warning("CoinGecko request error (attempt %d): %s", attempt + 1, e)
                    time.sleep(2 ** attempt)
                    continue
                if resp.status_code == 429:
                    wait = min(60 * (attempt + 1), 120)
                    logger.warning("CoinGecko rate limited, waiting %ds (attempt %d)...", wait, attempt + 1)
                    time.sleep(wait)
                    continue
                break

            if resp is None or resp.status_code != 200:
                logger.warning("CoinGecko %s for %s", resp.status_code if resp else "no response", coin_id)
                return []

            data = resp.json()
            prices = data.get("prices", [])
            volumes = data.get("total_volumes", [])

            if not prices:
                return []

            # Build volume lookup (ms timestamp → volume)
            vol_map = {int(v[0] / 1000): v[1] for v in volumes}

            # Convert price points to candle-bucket format
            # Each price point is [timestamp_ms, price]
            buckets: Dict[int, List] = {}
            for p in prices:
                ts = int(p[0] / 1000)
                price = p[1]
                bucket = (ts // resolution_s) * resolution_s
                if bucket not in buckets:
                    buckets[bucket] = []
                buckets[bucket].append((ts, price, vol_map.get(ts, 0)))

            candles = []
            for bucket_ts in sorted(buckets.keys()):
                points = buckets[bucket_ts]
                prices_in_bucket = [p[1] for p in points]
                total_vol = sum(p[2] for p in points)
                # Avoid double-counting volume across points in the same bucket
                avg_vol = total_vol / max(len(points), 1)

                candles.append(Candle(
                    market=market_name,
                    resolution_s=resolution_s,
                    ts=bucket_ts,
                    open=prices_in_bucket[0],
                    high=max(prices_in_bucket),
                    low=min(prices_in_bucket),
                    close=prices_in_bucket[-1],
                    volume=avg_vol,
                ))

            return candles

        except Exception as e:
            logger.error("CoinGecko fetch error for %s: %s", coin_id, e)
            return []

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
