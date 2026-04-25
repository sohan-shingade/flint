"""GeckoTerminal OHLCV provider."""
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
from typing import List, Optional

import httpx

logger = logging.getLogger("flint.providers.gecko")

from ..models import Candle
from .base import CandleProvider

_BASE_URL = "https://api.geckoterminal.com/api/v2"
_MAX_LIMIT = 1000  # GeckoTerminal max per request


def _resolution_to_timeframe(resolution_s: int) -> str:
    """Map resolution in seconds to GeckoTerminal timeframe string."""
    if resolution_s <= 60:
        return "minute"
    if resolution_s <= 300:
        return "minute"
    if resolution_s <= 900:
        return "minute"
    if resolution_s <= 3600:
        return "hour"
    return "day"


def _resolution_to_aggregate(resolution_s: int) -> int:
    """Return the aggregate multiplier for the timeframe."""
    if resolution_s <= 60:
        return 1
    if resolution_s <= 300:
        return 5
    if resolution_s <= 900:
        return 15
    if resolution_s <= 3600:
        return 1
    return 1


class GeckoProvider(CandleProvider):
    """Fetch OHLCV candles from GeckoTerminal's public API."""

    def __init__(
        self,
        network: str = "solana",
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._network = network
        self._client = client or httpx.Client(timeout=30)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch_candles(
        self,
        market: str,
        resolution_s: int,
        start_ts: int,
        end_ts: int,
    ) -> List[Candle]:
        """Fetch OHLCV data.

        ``market`` should be the pool address on GeckoTerminal
        (e.g. ``Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE`` for SOL/USDC).
        """
        timeframe = _resolution_to_timeframe(resolution_s)
        aggregate = _resolution_to_aggregate(resolution_s)

        all_candles: List[Candle] = []
        before_ts = end_ts + 1

        while True:
            url = (
                f"{_BASE_URL}/networks/{self._network}/pools/{market}"
                f"/ohlcv/{timeframe}"
            )
            params = {
                "aggregate": str(aggregate),
                "before_timestamp": str(before_ts),
                "limit": str(min(_MAX_LIMIT, 1000)),
                "currency": "usd",
            }
            resp = self._client.get(url, params=params)
            if resp.status_code != 200:
                logger.warning("GeckoTerminal API returned %d", resp.status_code)
                break
            data = resp.json()
            ohlcv_list = data.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
            if not ohlcv_list:
                break

            for row in ohlcv_list:
                ts, o, h, l, c, v = row  # noqa: E741
                ts = int(ts)
                if ts < start_ts or ts > end_ts:
                    continue
                all_candles.append(
                    Candle(
                        ts=ts,
                        open=float(o),
                        high=float(h),
                        low=float(l),
                        close=float(c),
                        volume=float(v),
                        market=market,
                        resolution_s=resolution_s,
                    )
                )

            earliest = min(int(r[0]) for r in ohlcv_list)
            if earliest <= start_ts:
                break
            before_ts = earliest
            time.sleep(2)  # GeckoTerminal rate limit: 30 req/min on free tier

        all_candles.sort(key=lambda c: c.ts)
        return all_candles
