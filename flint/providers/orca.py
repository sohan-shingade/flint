"""Orca Whirlpool pool and volume provider.

API docs: https://api.mainnet.orca.so/v1
No API key required.
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

import time
from typing import Dict, List, Optional

import httpx

from ..models import DexVolume
from .registry import DataProvider, register

_BASE_URL = "https://api.mainnet.orca.so/v1"


@register
class OrcaProvider(DataProvider):
    """Fetches whirlpool data and volume from the Orca API."""

    name = "orca"
    supported_data_types = ["pool_data", "dex_volume"]

    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._client = client or httpx.Client(timeout=30)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- public API -----------------------------------------------------------

    def fetch_whirlpools(
        self,
        token_mint: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict]:
        """Fetch whirlpools, optionally filtered by a token mint.

        The Orca ``/whirlpool/list`` endpoint returns all whirlpools in one
        response; filtering and limiting is done client-side.
        """
        resp = self._client.get(f"{_BASE_URL}/whirlpool/list")
        resp.raise_for_status()
        body = resp.json()

        pools: List[Dict] = body.get("whirlpools", [])

        if token_mint:
            pools = [
                p for p in pools
                if p.get("tokenA", {}).get("mint") == token_mint
                or p.get("tokenB", {}).get("mint") == token_mint
            ]

        # Sort by TVL descending, then take the top *limit*.
        pools.sort(key=lambda p: float(p.get("tvl", 0)), reverse=True)
        pools = pools[:limit]

        return [self._normalize_pool(p) for p in pools]

    def fetch_pool_by_address(self, address: str) -> Optional[Dict]:
        """Fetch a single whirlpool by its on-chain address.

        Returns a normalised pool dict, or ``None`` if not found.
        """
        resp = self._client.get(f"{_BASE_URL}/whirlpool/{address}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        body = resp.json()
        return self._normalize_pool(body)

    def fetch_top_pools_volume(self, limit: int = 20) -> List[DexVolume]:
        """Return :class:`DexVolume` entries for the top whirlpools by TVL."""
        pools = self.fetch_whirlpools(limit=limit)
        now = int(time.time())
        results: List[DexVolume] = []
        for p in pools:
            market = f"{p['token_a_symbol']}/{p['token_b_symbol']}"
            results.append(
                DexVolume(
                    market=market,
                    dex="orca",
                    ts=now,
                    volume_usd=p["volume_24h"],
                    txn_count=0,
                )
            )
        return results

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _normalize_pool(raw: Dict) -> Dict:
        """Normalise a raw Orca whirlpool JSON object into a flat dict."""
        token_a = raw.get("tokenA", {})
        token_b = raw.get("tokenB", {})
        return {
            "pool_address": raw.get("address", ""),
            "token_a_mint": token_a.get("mint", ""),
            "token_a_symbol": token_a.get("symbol", ""),
            "token_b_mint": token_b.get("mint", ""),
            "token_b_symbol": token_b.get("symbol", ""),
            "tvl": float(raw.get("tvl", 0)),
            "volume_24h": float(raw.get("volume", {}).get("day", 0)),
            "fee_rate": float(raw.get("lpFeeRate", 0)),
            "price": float(raw.get("price", 0)),
            "tick_current": int(raw.get("tickCurrentIndex", 0)),
        }
