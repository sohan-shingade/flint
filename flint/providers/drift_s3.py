"""Drift Protocol S3 historical data provider.

Downloads per-day gzipped trade-record CSVs from the public
``drift-historical-data-v2`` bucket and aggregates them into OHLCV candles.
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

import csv
import gzip
import io
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import httpx

from ..models import Candle, FundingRate
from .base import CandleProvider

_BUCKET = "drift-historical-data-v2"
_REGION = "eu-west-1"
_BASE_URL = f"https://{_BUCKET}.s3.{_REGION}.amazonaws.com"
_PROGRAM = "dRiftyHA39MWEi3m9aunc5MzRF1JYuBsbn6VPcn33UH"


class DriftS3Provider(CandleProvider):
    """Fetches trade records from Drift's public S3 bucket and builds candles."""

    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._client = client or httpx.Client(timeout=30)
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
        on_progress=None,
    ) -> List[Candle]:
        """Fetch candles from Drift S3.

        Args:
            on_progress: Optional callback ``fn(completed, total, date_str)``
                called after each day is downloaded.
        """
        dates = _date_range(start_ts, end_ts)
        total = len(dates)
        all_candles: List[Candle] = []
        for i, d in enumerate(dates):
            if on_progress is not None:
                on_progress(i, total, d)
            raw = self._download_day(market, d)
            if raw is None:
                continue
            candles = self._aggregate_trades(raw, market, resolution_s, start_ts, end_ts)
            all_candles.extend(candles)
        if on_progress is not None:
            on_progress(total, total, "done")
        all_candles.sort(key=lambda c: c.ts)
        return all_candles

    # -- internals ------------------------------------------------------------

    def _s3_key(self, market: str, date_str: str) -> str:
        year = date_str[:4]
        return f"program/{_PROGRAM}/market/{market}/tradeRecords/{year}/{date_str}"

    def _download_day(self, market: str, date_str: str) -> Optional[str]:
        import logging as _log
        _logger = _log.getLogger("flint.providers.drift_s3")
        url = f"{_BASE_URL}/{self._s3_key(market, date_str)}"
        try:
            resp = self._client.get(url)
        except Exception as e:
            _logger.warning("S3 request failed for %s/%s: %s", market, date_str, e)
            return None
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            _logger.warning("S3 %d for %s/%s", resp.status_code, market, date_str)
            return None
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


    def fetch_funding_rates(
        self,
        market: str,
        start_ts: int,
        end_ts: int,
        on_progress=None,
    ) -> List[FundingRate]:
        """Fetch funding rate records from S3 (available 2022 - Jan 2025)."""
        import logging
        logger = logging.getLogger("flint.providers.drift_s3")

        dates = _date_range(start_ts, end_ts)
        all_rates: List[FundingRate] = []

        for i, date_str in enumerate(dates):
            year = date_str[:4]
            key = f"program/{_PROGRAM}/market/{market}/fundingRateRecords/{year}/{date_str}"
            url = f"{_BASE_URL}/{key}"

            try:
                resp = self._client.get(url)
                if resp.status_code == 404:
                    continue
                if resp.status_code != 200:
                    continue

                # Try gzip decompress, fall back to raw text
                try:
                    text = gzip.decompress(resp.content).decode("utf-8")
                except Exception:
                    text = resp.text

                if not text.strip():
                    continue

                reader = csv.DictReader(io.StringIO(text))
                for row in reader:
                    ts = int(row.get("ts", 0))
                    if ts < start_ts or ts > end_ts:
                        continue
                    raw_rate = float(row.get("fundingRate", 0))
                    oracle = float(row.get("oraclePriceTwap", 0))
                    mark = float(row.get("markPriceTwap", 0))

                    # Drift S3 fundingRate is payment-per-base-unit (USDC).
                    # Normalize to a fraction of notional by dividing by oracle price.
                    # This way apply_funding() correctly computes: size * price * rate
                    rate = raw_rate / oracle if oracle > 0 else 0.0

                    all_rates.append(FundingRate(
                        market=market,
                        ts=ts,
                        rate=rate,
                        oracle_price=oracle,
                        mark_price=mark,
                        slot=int(row.get("slot", 0)),
                    ))
            except Exception as e:
                logger.warning("S3 funding fetch error for %s/%s: %s", market, date_str, e)

            if on_progress and len(dates) > 0:
                on_progress(i + 1, len(dates), date_str)

        all_rates.sort(key=lambda r: r.ts)
        return all_rates


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
