"""HyperliquidCandleProvider — historical candle data from Hyperliquid.

Uses sync HTTP (like HyperliquidFundingProvider) since this is for
batch downloads, not live trading.
"""
from __future__ import annotations

# Phase 1 T1.3.a — point-in-time declaration.
# Hyperliquid /info/candleSnapshot returns objects with t = open time and
# T = close time (ms). This provider uses close time (T/1000) as canonical
# ts → bar-close convention, consistent with Drift.
PIT_METADATA = {  # noqa: E402
    "candle_ts": "bar-close",
    "funding_ts": "exchange-time",
    "orderbook_ts": "exchange-time",
    "oi_ts": "exchange-time",
    "reviewed": "2026-04-23",
}

import logging
import time
from typing import List

import httpx

from ..models import Candle

logger = logging.getLogger("flint.hyperliquid_candles")

_FLINT_TO_HL = {
    "SOL-PERP": "SOL", "BTC-PERP": "BTC", "ETH-PERP": "ETH",
    "DOGE-PERP": "DOGE", "AVAX-PERP": "AVAX", "LINK-PERP": "LINK",
    "ARB-PERP": "ARB", "SUI-PERP": "SUI", "XRP-PERP": "XRP",
    "OP-PERP": "OP", "INJ-PERP": "INJ", "TIA-PERP": "TIA",
    "SEI-PERP": "SEI", "WIF-PERP": "WIF", "JUP-PERP": "JUP",
    "RENDER-PERP": "RENDER", "BNB-PERP": "BNB",
}

_INTERVAL_TO_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400,
}


class HyperliquidCandleProvider:
    """Fetch historical candles from Hyperliquid (always mainnet, free, no key)."""

    BASE_URL = "https://api.hyperliquid.xyz/info"

    def __init__(self):
        self._client = httpx.Client(timeout=15)

    def fetch_candles(self, market: str, start_ts: int, end_ts: int, resolution: str = "1m") -> List[Candle]:
        coin = _FLINT_TO_HL.get(market)
        if coin is None:
            logger.warning("Unknown market for Hyperliquid: %s", market)
            return []

        resolution_s = _INTERVAL_TO_SECONDS.get(resolution, 60)
        all_candles: List[Candle] = []
        cursor_start = start_ts * 1000

        for _ in range(200):
            try:
                resp = self._client.post(self.BASE_URL, json={
                    "type": "candleSnapshot",
                    "req": {"coin": coin, "interval": resolution, "startTime": cursor_start, "endTime": end_ts * 1000},
                })
                if resp.status_code != 200:
                    logger.warning("Hyperliquid candle API returned %d", resp.status_code)
                    break

                records = resp.json()
                if not isinstance(records, list) or not records:
                    break

                for r in records:
                    ts = r.get("t", 0) // 1000
                    all_candles.append(Candle(
                        ts=ts, open=float(r.get("o", 0)), high=float(r.get("h", 0)),
                        low=float(r.get("l", 0)), close=float(r.get("c", 0)),
                        volume=float(r.get("v", 0)), market=market,
                        resolution_s=resolution_s, venue="hyperliquid",
                    ))

                if len(records) < 5000:
                    break

                last_t = max(r.get("t", 0) for r in records)
                if last_t <= cursor_start:
                    break
                cursor_start = last_t + 1
                time.sleep(0.2)

            except Exception as e:
                logger.error("Hyperliquid candle fetch error: %s", e)
                break

        all_candles.sort(key=lambda c: c.ts)
        return all_candles

    def close(self) -> None:
        self._client.close()
