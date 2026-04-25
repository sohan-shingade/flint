"""Data service — store-backed read APIs.

Wraps the OHLCV / funding / borrow / market metadata queries used by
both the FastAPI data routes and the MCP data tools. Returns plain dicts
so callers don't need to know about Candle/FundingRate/BorrowSnapshot
dataclasses.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ..store import FlintStore


def get_ohlcv(
    store: FlintStore,
    market: str,
    resolution_s: int = 3600,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    limit: int = 1000,
    venue: Optional[str] = None,
) -> Dict[str, Any]:
    """Return OHLCV candles. When `venue` is None, deduplicates across
    venues by timestamp (pyth wins on price, max-volume wins otherwise)."""
    candles = store.query_candles(market, resolution_s, start_ts, end_ts, limit=limit, venue=venue)

    if venue is None:
        by_ts: Dict[int, Any] = {}
        for c in candles:
            existing = by_ts.get(c.ts)
            if existing is None:
                by_ts[c.ts] = c
            elif c.venue == "pyth":
                vol = max(c.volume, existing.volume)
                by_ts[c.ts] = type(c)(
                    ts=c.ts, open=c.open, high=c.high, low=c.low,
                    close=c.close, volume=vol, market=c.market,
                    resolution_s=c.resolution_s, venue=c.venue,
                )
            elif existing.venue != "pyth" and c.volume > existing.volume:
                by_ts[c.ts] = c
        candles = sorted(by_ts.values(), key=lambda c: c.ts)

    return {
        "market": market,
        "resolution_s": resolution_s,
        "venue": venue or "all",
        "count": len(candles),
        "candles": [
            {"ts": c.ts, "open": c.open, "high": c.high,
             "low": c.low, "close": c.close, "volume": c.volume,
             "venue": getattr(c, "venue", "pyth")}
            for c in candles
        ],
    }


def get_funding(
    store: FlintStore,
    market: str,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
) -> Dict[str, Any]:
    """Return funding rates grouped by venue. Defaults to last 30 days."""
    if end_ts is None:
        end_ts = int(time.time())
    if start_ts is None:
        start_ts = end_ts - 30 * 86400

    by_venue = store.query_funding_by_venue(market, start_ts, end_ts)
    total = sum(len(v) for v in by_venue.values())
    return {"market": market, "venues": by_venue, "count": total}


def get_borrow_rates(
    store: FlintStore,
    market: str,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
) -> Dict[str, Any]:
    start = start_ts or 0
    end = end_ts or int(time.time())
    snapshots = store.query_borrow_rates(market, start, end)
    rates = [
        {"ts": s.ts, "rate_hourly": s.rate_hourly, "utilization": s.utilization,
         "cumulative_rate": s.cumulative_rate, "source": s.source}
        for s in snapshots
    ]
    return {"market": market, "rates": rates, "count": len(rates)}


def list_markets(store: FlintStore) -> List[Dict[str, Any]]:
    return store.list_markets_with_data()


def delete_market_data(store: FlintStore, market: str) -> Dict[str, Any]:
    deleted = store.delete_market_data(market)
    return {"market": market, "deleted": deleted, "total_records": sum(deleted.values())}
