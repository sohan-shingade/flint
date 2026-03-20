"""Individual data collection tasks.

All tasks are synchronous -- DriftDataProvider uses httpx.Client (sync).
The CollectorService runs them via asyncio.to_thread().
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List

from ..models import OraclePrice
from ..providers.drift_api import DriftDataProvider
from ..providers.drift_s3 import DriftS3Provider
from ..store import FlintStore

MARKET_INDEX = {"SOL-PERP": 0, "BTC-PERP": 1, "ETH-PERP": 2}


@dataclass
class CollectorConfig:
    markets: List[str] = field(default_factory=lambda: ["SOL-PERP", "BTC-PERP", "ETH-PERP"])
    candle_backfill_days: int = 90
    candle_interval_s: int = 3600
    funding_interval_s: int = 3600
    orderbook_interval_s: int = 300
    oracle_interval_s: int = 60


def collect_oracle_prices(store: FlintStore, market: str) -> int:
    provider = DriftDataProvider()
    try:
        price = provider.fetch_mid_price(market)
        ts = int(time.time())
        oracle = OraclePrice(market=market, ts=ts, price=price)
        return store.upsert_oracle_prices([oracle])
    finally:
        provider.close()


def collect_funding_rates(store: FlintStore, market: str, market_index: int) -> int:
    provider = DriftDataProvider()
    try:
        rates = provider.fetch_funding_rates(market_index=market_index, market_name=market)
        if rates:
            return store.upsert_funding_rates(rates)
        return 0
    finally:
        provider.close()


def collect_orderbook(store: FlintStore, market: str) -> int:
    provider = DriftDataProvider()
    try:
        ob = provider.fetch_orderbook(market_name=market, depth=10)
        if ob:
            snapshot = {
                "market": market,
                "ts": int(time.time()),
                "bid_prices": [level.price for level in ob.bids[:10]],
                "bid_sizes": [level.size for level in ob.bids[:10]],
                "ask_prices": [level.price for level in ob.asks[:10]],
                "ask_sizes": [level.size for level in ob.asks[:10]],
            }
            return store.upsert_orderbook_snapshots([snapshot])
        return 0
    finally:
        provider.close()


def collect_candles_backfill(store: FlintStore, market: str, days: int = 90) -> int:
    end_ts = int(time.time())
    start_ts = end_ts - (days * 86400)
    provider = DriftS3Provider()
    try:
        candles = provider.fetch_candles(market, 3600, start_ts, end_ts)
        if candles:
            return store.upsert_candles(candles)
        return 0
    finally:
        provider.close()
