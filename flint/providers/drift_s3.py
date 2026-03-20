"""Drift Protocol S3 historical data provider.

Downloads per-day gzipped trade-record CSVs from the public
``drift-historical-data-v2`` bucket and aggregates them into OHLCV candles.
"""
from __future__ import annotations

import csv
import gzip
import io
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import httpx

from ..models import Candle
from .base import CandleProvider

_BUCKET = "drift-historical-data-v2"
_REGION = "eu-west-1"
_BASE_URL = f"https://{_BUCKET}.s3.{_REGION}.amazonaws.com"
_PROGRAM = "dRiftyHA39MWEi3m9aunc5MzRF1JYuBsbn6VPcn33UH"


class DriftS3Provider(CandleProvider):
    """Fetches trade records from Drift's public S3 bucket and builds candles."""

    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._client = client or httpx.Client(timeout=60)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- public API -----------------------------------------------------------

    def fetch_candles(
        self,
        market: str,
        resolution_s: int,
        start_ts: int,
        end_ts: int,
    ) -> List[Candle]:
        dates = _date_range(start_ts, end_ts)
        all_candles: List[Candle] = []
        for d in dates:
            raw = self._download_day(market, d)
            if raw is None:
                continue
            candles = self._aggregate_trades(raw, market, resolution_s, start_ts, end_ts)
            all_candles.extend(candles)
        all_candles.sort(key=lambda c: c.ts)
        return all_candles

    # -- internals ------------------------------------------------------------

    def _s3_key(self, market: str, date_str: str) -> str:
        year = date_str[:4]
        return f"program/{_PROGRAM}/market/{market}/tradeRecords/{year}/{date_str}"

    def _download_day(self, market: str, date_str: str) -> Optional[str]:
        url = f"{_BASE_URL}/{self._s3_key(market, date_str)}"
        resp = self._client.get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        try:
            return gzip.decompress(resp.content).decode("utf-8")
        except gzip.BadGzipFile:
            return resp.text

    def _aggregate_trades(
        self,
        csv_text: str,
        market: str,
        resolution_s: int,
        start_ts: int,
        end_ts: int,
    ) -> List[Candle]:
        reader = csv.DictReader(io.StringIO(csv_text))
        buckets: Dict[int, List[float]] = {}  # bucket_ts -> list of (price, volume) pairs
        bucket_volumes: Dict[int, float] = {}
        for row in reader:
            ts = int(row["ts"])
            if ts < start_ts or ts > end_ts:
                continue
            price = float(row["oraclePrice"])
            volume = float(row["baseAssetAmountFilled"])
            bucket = (ts // resolution_s) * resolution_s
            buckets.setdefault(bucket, []).append(price)
            bucket_volumes[bucket] = bucket_volumes.get(bucket, 0.0) + volume

        candles: List[Candle] = []
        for bucket_ts in sorted(buckets):
            prices = buckets[bucket_ts]
            candles.append(
                Candle(
                    ts=bucket_ts,
                    open=prices[0],
                    high=max(prices),
                    low=min(prices),
                    close=prices[-1],
                    volume=bucket_volumes[bucket_ts],
                    market=market,
                    resolution_s=resolution_s,
                )
            )
        return candles


def _date_range(start_ts: int, end_ts: int) -> List[str]:
    """Return list of YYYYMMDD strings covering [start_ts, end_ts]."""
    start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc).date()
    end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc).date()
    dates: List[str] = []
    d = start_dt
    while d <= end_dt:
        dates.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return dates
