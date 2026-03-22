"""Drift open interest provider.

Fetches current and historical open interest data from the Drift
data API.

Endpoint: https://data.api.drift.trade/market/{symbol}/openInterest
No API key required.
"""
from __future__ import annotations

import time
from typing import List, Optional

import httpx

from ..models import OpenInterest
from .registry import DataProvider, register

_DATA_API = "https://data.api.drift.trade"

# Drift stores OI values in BASE_PRECISION (1e9).
_BASE_PRECISION = 1e9


@register
class DriftOpenInterestProvider(DataProvider):
    """Fetches open interest snapshots from the Drift data API."""

    name = "drift_open_interest"
    supported_data_types = ["open_interest"]

    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._client = client or httpx.Client(timeout=30)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- historical OI --------------------------------------------------------

    def fetch_open_interest(
        self,
        market: str,
        start_ts: int,
        end_ts: int,
    ) -> List[OpenInterest]:
        """Fetch historical open interest snapshots for *market* between
        *start_ts* and *end_ts* (unix seconds).

        The Drift API returns an array of snapshots with ``longOI``,
        ``shortOI``, and ``ts`` fields.  Values are in BASE_PRECISION (1e9);
        we divide to get human-readable floats.
        """
        resp = self._client.get(
            f"{_DATA_API}/market/{market}/openInterest",
            params={"start": start_ts, "end": end_ts},
        )
        resp.raise_for_status()
        data = resp.json()

        records = data if isinstance(data, list) else data.get("data", [])
        results: List[OpenInterest] = []
        for rec in records:
            results.append(
                OpenInterest(
                    market=market,
                    ts=int(rec["ts"]),
                    long_oi=int(rec["longOI"]) / _BASE_PRECISION,
                    short_oi=int(rec["shortOI"]) / _BASE_PRECISION,
                )
            )
        return results

    # -- current OI -----------------------------------------------------------

    def fetch_current_oi(self, market: str) -> Optional[OpenInterest]:
        """Fetch the most recent open interest snapshot for *market*.

        Queries the last 60 seconds and returns the latest record, or
        ``None`` if no data is available.
        """
        now = int(time.time())
        resp = self._client.get(
            f"{_DATA_API}/market/{market}/openInterest",
            params={"start": now - 60, "end": now},
        )
        resp.raise_for_status()
        data = resp.json()

        records = data if isinstance(data, list) else data.get("data", [])
        if not records:
            return None

        # Take the most recent record.
        rec = records[-1]
        return OpenInterest(
            market=market,
            ts=int(rec["ts"]),
            long_oi=int(rec["longOI"]) / _BASE_PRECISION,
            short_oi=int(rec["shortOI"]) / _BASE_PRECISION,
        )
