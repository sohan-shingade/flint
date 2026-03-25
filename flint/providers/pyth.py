"""Pyth Network oracle price provider.

Fetches real-time oracle prices from the Pyth Hermes API.
No API key required.

Endpoint: https://hermes.pyth.network
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

import httpx

from ..models import OraclePrice
from .registry import DataProvider, register

_HERMES_API = "https://hermes.pyth.network"

# Pyth price feed IDs for common Solana-ecosystem pairs.
FEED_IDS: Dict[str, str] = {
    "SOL/USD":    "0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
    "BTC/USD":    "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "ETH/USD":    "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
    "BONK/USD":   "0x72b021217ca3fe68922a19aaf990109cb9d84e9ad004b4d2025ad6f529314419",
    "JUP/USD":    "0x0a0408d619e9380abad35060f9192039ed5042fa6f82301d0e48bb52be830996",
    "WIF/USD":    "0x4ca4beeca86f0d164160323817a4e42b10010a724c2217c6ee41b54cd4cc61fc",
    "PYTH/USD":   "0x0bbf28e9a841a1cc788f6a361b17ca072d0ea3098a1e5df1c3922d06719579ff",
    "DOGE/USD":   "0xdcef50dd0a4cd2dcc17e45df1676dcb336a11a61c69df7a0299b0150c672d25c",
    "AVAX/USD":   "0x93da3352f9f1d105fdfe4971cfa80e9dd777bfc5d0f683ebb6e1294b92137bb7",
    "LINK/USD":   "0x8ac0c70fff57e9aefdf5edf44b51d62c2d433653cbb2cf5cc06bb115af04d221",
    "SUI/USD":    "0x23d7315113f5b1d3ba7a83604c44b94d79f4fd69af77f804fc7f920a6dc65744",
    "ARB/USD":    "0x3fa4252848f9f0a1480be62745a4629d9eb1322aebab8a791e344b3b9c1adcf5",
    "XRP/USD":    "0xec5d399846a9209f3fe5881d70aae9268c94339ff9817e8d18ff19fa05eea1c8",
    "RENDER/USD": "0xab2f44d75fed4b315fc0b4e95e22c3a5b91b8dee6170023a6f573e7e63c4350e",
    "INJ/USD":    "0x7a5bc1d2b56ad029048cd63964b3ad2776eadf812edc1a43a31406cb54bff592",
    "OP/USD":     "0x385f64d993f7b77d8182ed5003d97c60aa3361f3cecfe711544d2d59165e9bdf",
    "TIA/USD":    "0x09f7c1d7dfbb7df2b8fe3d3d87ee94a2259d212da4f30c1f0540d066dfa44723",
    "SEI/USD":    "0x53614f1cb0c031d4af66c04cb9c756234adad0e1cee85303795091499a4084eb",
    "BNB/USD":    "0x2f95862b045670cd22bee3114c39763a4a08beeb663b145d283c31d7d1101c4f",
    "DRIFT/USD":  "0x5c1690b27bb02d08c53ba6c41ee7a05e1f2fa4e2c849470891aa4e6e4bfda004",
}


@register
class PythProvider(DataProvider):
    """Fetches oracle prices from the Pyth Hermes REST API."""

    name = "pyth"
    supported_data_types = ["oracle_prices"]

    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._client = client or httpx.Client(timeout=30)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- feed resolution ------------------------------------------------------

    @staticmethod
    def resolve_feed(pair: str) -> str:
        """Return the Pyth feed ID hex string for a given pair.

        Raises ``KeyError`` if the pair is not in the built-in map.
        """
        return FEED_IDS[pair]

    # -- raw fetch ------------------------------------------------------------

    def _fetch_latest(self, feed_ids: List[str]) -> dict:
        """Fetch latest price updates from Hermes for one or more feed IDs.

        GET /v2/updates/price/latest?ids[]=<id1>&ids[]=<id2>...
        """
        params = [("ids[]", fid) for fid in feed_ids]
        resp = self._client.get(
            f"{_HERMES_API}/v2/updates/price/latest",
            params=params,
        )
        if resp.status_code != 200:
            raise ValueError(f"Pyth Hermes API returned {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    # -- single price ---------------------------------------------------------

    def fetch_price(self, pair: str) -> dict:
        """Fetch the latest price for a single pair.

        Returns a dict with keys: pair, price, confidence, ema_price, ts, expo.
        Price conversion: ``price_raw * 10**expo`` where *expo* is typically
        negative (e.g. -8).
        """
        feed_id = self.resolve_feed(pair)
        data = self._fetch_latest([feed_id])
        parsed = data.get("parsed", [])
        if not parsed:
            raise ValueError(f"No price data returned for {pair}")

        entry = parsed[0]
        price_data = entry["price"]
        ema_data = entry["ema_price"]
        expo = int(price_data["expo"])
        factor = 10 ** expo

        return {
            "pair": pair,
            "price": int(price_data["price"]) * factor,
            "confidence": int(price_data["conf"]) * factor,
            "ema_price": int(ema_data["price"]) * factor,
            "ts": int(price_data["publish_time"]),
            "expo": expo,
        }

    # -- batch prices ---------------------------------------------------------

    def fetch_prices(self, pairs: List[str]) -> List[dict]:
        """Fetch latest prices for multiple pairs in a single HTTP request.

        Returns a list of dicts (same shape as :meth:`fetch_price`).
        """
        feed_ids = [self.resolve_feed(p) for p in pairs]
        # Build reverse map: feed_id -> pair for labelling results
        id_to_pair: Dict[str, str] = {
            self.resolve_feed(p): p for p in pairs
        }

        data = self._fetch_latest(feed_ids)
        parsed = data.get("parsed", [])

        results: List[dict] = []
        for entry in parsed:
            fid = "0x" + entry["id"]
            pair = id_to_pair.get(fid, fid)
            price_data = entry["price"]
            ema_data = entry["ema_price"]
            expo = int(price_data["expo"])
            factor = 10 ** expo

            results.append({
                "pair": pair,
                "price": int(price_data["price"]) * factor,
                "confidence": int(price_data["conf"]) * factor,
                "ema_price": int(ema_data["price"]) * factor,
                "ts": int(price_data["publish_time"]),
                "expo": expo,
            })
        return results

    # -- storage-ready models -------------------------------------------------

    def fetch_oracle_prices(self, pairs: List[str]) -> List[OraclePrice]:
        """Fetch latest prices and return :class:`OraclePrice` models ready
        for persistence via the store layer.
        """
        price_dicts = self.fetch_prices(pairs)
        return [
            OraclePrice(
                market=d["pair"],
                ts=d["ts"],
                price=d["price"],
            )
            for d in price_dicts
        ]
