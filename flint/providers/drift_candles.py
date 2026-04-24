"""Drift Data API candle provider.

Fetches pre-built OHLCV candles from Drift's Data API.
Much faster than S3 (no trade aggregation needed) and has current data.

Endpoint: GET /market/{symbol}/candles/{resolution}
  resolution: 1, 5, 15, 60, 240, D, W, M (minutes)
  params: startTs, endTs, limit (max 1000)
"""
from __future__ import annotations

# Phase 1 T1.3.a — point-in-time declaration.
# Drift API returns candles with ts = bar START (UTC epoch seconds); this
# provider normalizes to bar CLOSE by adding resolution_s, so downstream
# consumers treat ts as close-time. Funding/orderbook not applicable here.
PIT_METADATA = {
    "candle_ts": "bar-close",
    "funding_ts": "exchange-time",  # unused by this provider
    "orderbook_ts": "exchange-time",  # unused by this provider
    "oi_ts": "exchange-time",  # unused by this provider
    "reviewed": "2026-04-23",
}

import logging
import time
from typing import Callable, List, Optional

import httpx

from ..models import Candle
from .base import CandleProvider

logger = logging.getLogger("flint.providers.drift_candles")

_BASE_URL = "https://data.api.drift.trade"

# Map resolution in seconds to Drift API resolution format
_RESOLUTION_MAP = {
    60: "1",
    300: "5",
    900: "15",
    3600: "60",
    14400: "240",
    86400: "D",
}


class DriftCandleProvider(CandleProvider):
    """Fetches OHLCV candles from Drift's Data API."""

    def __init__(self, client: Optional[httpx.Client] = None) -> None:
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
        on_progress: Optional[Callable] = None,
    ) -> List[Candle]:
        """Fetch candles from Drift Data API.

        Paginates automatically since max 1000 candles per request.
        """
        res_key = _RESOLUTION_MAP.get(resolution_s)
        if res_key is None:
            logger.warning("Unsupported resolution %ds for Drift API, falling back to 60 (1h)", resolution_s)
            res_key = "60"

        url = f"{_BASE_URL}/market/{market}/candles/{res_key}"
        all_candles: List[Candle] = []
        # Drift API uses DESCENDING order: startTs = newest, endTs = oldest
        cursor_end = end_ts
        page_num = 0
        total_pages_est = max(1, (end_ts - start_ts) // (resolution_s * 1000)) + 1

        max_pages = 500  # safety limit to prevent infinite pagination
        while cursor_end > start_ts and page_num < max_pages:
            params = {
                "startTs": cursor_end,     # newer (confusing API naming)
                "endTs": start_ts,         # older
                "limit": 1000,
            }

            try:
                # Retry once on timeout/5xx
                resp = None
                for attempt in range(2):
                    try:
                        resp = self._client.get(url, params=params)
                        if resp.status_code < 500:
                            break
                        logger.warning("Drift API %s returned %d (attempt %d)", url, resp.status_code, attempt + 1)
                        time.sleep(1)
                    except httpx.TimeoutException:
                        logger.warning("Drift API timeout for %s (attempt %d)", market, attempt + 1)
                        if attempt == 1:
                            break
                        time.sleep(1)

                if resp is None or resp.status_code != 200:
                    logger.warning("Drift API %s returned %s", url, resp.status_code if resp else "timeout")
                    break

                data = resp.json()
                records = data.get("records", [])
                if not records:
                    break

                for r in records:
                    ts = int(r.get("ts", 0))
                    if ts < start_ts or ts > end_ts:
                        continue

                    # Use oracle prices (more accurate than fill prices)
                    candle = Candle(
                        ts=ts,
                        open=float(r.get("oracleOpen", r.get("fillOpen", 0))),
                        high=float(r.get("oracleHigh", r.get("fillHigh", 0))),
                        low=float(r.get("oracleLow", r.get("fillLow", 0))),
                        close=float(r.get("oracleClose", r.get("fillClose", 0))),
                        volume=float(r.get("baseVolume", r.get("quoteVolume", 0))),
                        market=market,
                        resolution_s=resolution_s,
                    )
                    all_candles.append(candle)

                # Records come in descending order — move cursor to oldest candle
                oldest_ts = min(int(r.get("ts", 0)) for r in records)
                if oldest_ts >= cursor_end:
                    break  # no progress, avoid infinite loop
                cursor_end = oldest_ts - resolution_s

                page_num += 1
                if on_progress is not None:
                    pct_done = min(page_num, total_pages_est)
                    on_progress(pct_done, total_pages_est, f"page {page_num}")

                # Brief pause to be respectful to the API
                if len(records) == 1000:
                    time.sleep(0.1)

            except Exception as e:
                logger.error("Drift API error: %s", e)
                break

        if on_progress is not None:
            on_progress(total_pages_est, total_pages_est, "done")

        all_candles.sort(key=lambda c: c.ts)

        # Deduplicate by timestamp
        seen = set()
        unique = []
        for c in all_candles:
            if c.ts not in seen:
                seen.add(c.ts)
                unique.append(c)

        return unique
