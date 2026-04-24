"""Birdeye provider — OHLCV for any Solana SPL token.

API docs: https://public-api.birdeye.so
Free tier: 50 requests/minute, no credit card required.
"""
from __future__ import annotations


# Phase 1 T1.3.a + D-1.3-providers — point-in-time declaration.
# Defaults are conservative — callers should verify against the
# specific source API when using this data in parity/PIT-sensitive
# contexts. Review date: 2026-04-24.
PIT_METADATA = {
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

_BASE = "https://public-api.birdeye.so"

# Map resolution_s -> Birdeye timeframe string
_TIMEFRAMES: Dict[int, str] = {
    60: "1m",
    300: "5m",
    900: "15m",
    1800: "30m",
    3600: "1H",
    14400: "4H",
    86400: "1D",
    604800: "1W",
}

# Well-known Solana token mint addresses
KNOWN_TOKENS: Dict[str, str] = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "WIF": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "DRIFT": "DriFtupJYLTosbwoN8koMbEYSx54aFAVLddWsbksjwg7",
    "RAY": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
    "PYTH": "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
    "RENDER": "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof",
}


class BirdeyeProvider(DataProvider):
    """Fetch OHLCV candles and token data from Birdeye's public API."""

    name = "birdeye"
    requires_api_key = True

    def __init__(
        self,
        api_key: str = "",
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=30)
        self._owns_client = client is None

    def is_available(self) -> bool:
        return bool(self._api_key)

    def supported_data_types(self) -> List[str]:
        return ["candles", "token_metadata", "price"]

    def _headers(self) -> dict:
        return {"X-API-KEY": self._api_key, "x-chain": "solana"}

    def fetch_candles(
        self,
        token_address: str,
        resolution_s: int,
        start_ts: int,
        end_ts: int,
    ) -> List[Candle]:
        """Fetch OHLCV candles for a Solana token.

        Args:
            token_address: SPL token mint address.
            resolution_s: Candle width in seconds (60, 300, 900, 1800, 3600,
                14400, 86400, 604800).
            start_ts: Start unix timestamp (inclusive).
            end_ts: End unix timestamp (inclusive).

        Returns:
            Sorted, deduplicated list of Candle objects.
        """
        tf = _TIMEFRAMES.get(resolution_s)
        if not tf:
            closest = min(_TIMEFRAMES.keys(), key=lambda k: abs(k - resolution_s))
            tf = _TIMEFRAMES[closest]
            logger.warning(
                "Resolution %ds not supported, using %s (%ds)",
                resolution_s, tf, closest,
            )
            resolution_s = closest

        candles: List[Candle] = []
        cursor = start_ts

        while cursor < end_ts:
            params = {
                "address": token_address,
                "type": tf,
                "time_from": cursor,
                "time_to": min(cursor + 1000 * resolution_s, end_ts),
            }
            try:
                resp = self._client.get(
                    f"{_BASE}/defi/ohlcv",
                    params=params,
                    headers=self._headers(),
                )
                if resp.status_code != 200:
                    logger.warning(
                        "Birdeye OHLCV %d: %s",
                        resp.status_code,
                        resp.text[:200] if hasattr(resp, "text") else "",
                    )
                    break
                data = resp.json()
                items = data.get("data", {}).get("items", [])
                if not items:
                    break
                for item in items:
                    ts = item.get("unixTime", 0)
                    if ts < start_ts or ts > end_ts:
                        continue
                    candles.append(Candle(
                        market=token_address,
                        resolution_s=resolution_s,
                        ts=ts,
                        open=float(item.get("o", 0)),
                        high=float(item.get("h", 0)),
                        low=float(item.get("l", 0)),
                        close=float(item.get("c", 0)),
                        volume=float(item.get("v", 0)),
                    ))
                last_ts = items[-1].get("unixTime", 0)
                if last_ts <= cursor:
                    break
                cursor = last_ts + 1
                time.sleep(0.05)
            except Exception as e:
                logger.error("Birdeye fetch error: %s", e)
                break

        # Deduplicate and sort
        seen: set = set()
        unique: List[Candle] = []
        for c in sorted(candles, key=lambda c: c.ts):
            if c.ts not in seen:
                seen.add(c.ts)
                unique.append(c)
        return unique

    def fetch_token_metadata(self, token_address: str) -> dict:
        """Fetch token metadata (symbol, name, decimals, market cap)."""
        try:
            resp = self._client.get(
                f"{_BASE}/defi/token_overview",
                params={"address": token_address},
                headers=self._headers(),
            )
            if resp.status_code == 200:
                return resp.json().get("data", {})
        except Exception as e:
            logger.error("Birdeye metadata error: %s", e)
        return {}

    def fetch_price(self, token_address: str) -> Optional[float]:
        """Fetch current price of a token."""
        try:
            resp = self._client.get(
                f"{_BASE}/defi/price",
                params={"address": token_address},
                headers=self._headers(),
            )
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("value")
        except Exception as e:
            logger.error("Birdeye price error: %s", e)
        return None

    def resolve_token(self, symbol: str) -> Optional[str]:
        """Resolve a symbol like 'SOL' to its mint address."""
        return KNOWN_TOKENS.get(symbol.upper())

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
