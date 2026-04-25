"""Raydium AMM pool and volume provider.

API docs: https://api-v3.raydium.io
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

_BASE_URL = "https://api-v3.raydium.io"


@register
class RaydiumProvider(DataProvider):
    """Fetches pool data and volume from the Raydium V3 API."""

    name = "raydium"
    supported_data_types = ["pool_data", "dex_volume"]

    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._client = client or httpx.Client(timeout=30)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- public API -----------------------------------------------------------

    def fetch_pools(
        self,
        token_mint: Optional[str] = None,
        pool_type: str = "all",
        sort_by: str = "liquidity",
        limit: int = 20,
    ) -> List[Dict]:
        """Fetch a page of Raydium pools, optionally filtered by token mint.

        Returns a list of normalised pool dicts.
        """
        params: Dict[str, str] = {
            "poolType": pool_type,
            "sortField": sort_by,
            "sortType": "desc",
            "pageSize": str(limit),
            "page": "1",
        }
        if token_mint:
            params["mint1"] = token_mint

        resp = self._client.get(f"{_BASE_URL}/pools/info/list", params=params)
        resp.raise_for_status()
        body = resp.json()

        rows = body.get("data", {}).get("data", [])
        return [self._normalize_pool(r) for r in rows]

    def fetch_pool_by_id(self, pool_id: str) -> Optional[Dict]:
        """Fetch a single pool by its on-chain address.

        Returns a normalised pool dict, or ``None`` if not found.
        """
        try:
            resp = self._client.get(
                f"{_BASE_URL}/pools/info/ids",
                params={"ids": pool_id},
            )
            if resp.status_code != 200:
                return None
            body = resp.json()
        except httpx.RequestError:
            return None

        rows = body.get("data", [])
        if not rows:
            return None
        return self._normalize_pool(rows[0])

    def fetch_pool_volume(self, pool_id: str) -> DexVolume:
        """Return a :class:`DexVolume` for a single pool."""
        pool = self.fetch_pool_by_id(pool_id)
        if pool is None:
            return DexVolume(
                market=pool_id,
                dex="raydium",
                ts=int(time.time()),
                volume_usd=0.0,
                txn_count=0,
            )
        market = f"{pool['token_a_symbol']}/{pool['token_b_symbol']}"
        return DexVolume(
            market=market,
            dex="raydium",
            ts=int(time.time()),
            volume_usd=pool["volume_24h"],
            txn_count=0,
        )

    def fetch_top_pools_volume(self, limit: int = 20) -> List[DexVolume]:
        """Return :class:`DexVolume` entries for the top pools by volume."""
        pools = self.fetch_pools(sort_by="volume", limit=limit)
        now = int(time.time())
        results: List[DexVolume] = []
        for p in pools:
            market = f"{p['token_a_symbol']}/{p['token_b_symbol']}"
            results.append(
                DexVolume(
                    market=market,
                    dex="raydium",
                    ts=now,
                    volume_usd=p["volume_24h"],
                    txn_count=0,
                )
            )
        return results

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _normalize_pool(raw: Dict) -> Dict:
        """Normalise a raw Raydium pool JSON object into a flat dict."""
        mint_a = raw.get("mintA", {})
        mint_b = raw.get("mintB", {})
        return {
            "pool_id": raw.get("id", ""),
            "pool_type": raw.get("type", ""),
            "token_a_mint": mint_a.get("address", ""),
            "token_a_symbol": mint_a.get("symbol", ""),
            "token_b_mint": mint_b.get("address", ""),
            "token_b_symbol": mint_b.get("symbol", ""),
            "tvl": float(raw.get("tvl", 0)),
            "volume_24h": float(raw.get("day", {}).get("volume", 0)),
            "fee_24h": float(raw.get("day", {}).get("volumeFee", 0)),
            "fee_rate": float(raw.get("feeRate", 0)),
            "lp_mint": raw.get("lpMint", {}).get("address", ""),
        }
