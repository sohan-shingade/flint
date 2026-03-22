"""Data query API — candles, funding rates. Thread-safe via shared store."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

from ...store import FlintStore

router = APIRouter()


def _get_store(request: Request) -> Optional[FlintStore]:
    """Get the shared store from app state. Never create a new one."""
    return getattr(request.app.state, "store", None)


@router.get("/ohlcv")
def get_ohlcv(
    request: Request,
    market: str = Query(..., description="Market symbol, e.g. SOL-PERP"),
    resolution_s: int = Query(3600, description="Candle width in seconds"),
    start_ts: Optional[int] = Query(None),
    end_ts: Optional[int] = Query(None),
    limit: int = Query(1000, le=10000),
):
    store = _get_store(request)
    if store is None:
        return {"market": market, "resolution_s": resolution_s, "count": 0, "candles": []}
    try:
        candles = store.query_candles(market, resolution_s, start_ts, end_ts, limit=limit)
        return {
            "market": market,
            "resolution_s": resolution_s,
            "count": len(candles),
            "candles": [
                {"ts": c.ts, "open": c.open, "high": c.high,
                 "low": c.low, "close": c.close, "volume": c.volume}
                for c in candles
            ],
        }
    except Exception as e:
        return {"market": market, "resolution_s": resolution_s, "count": 0, "candles": [], "error": str(e)}


@router.get("/funding")
def get_funding(
    request: Request,
    market: str = Query("SOL-PERP"),
    start_ts: Optional[int] = Query(None),
    end_ts: Optional[int] = Query(None),
    limit: int = Query(500, le=5000),
):
    store = _get_store(request)
    if store is None:
        return {"market": market, "count": 0, "rates": []}
    try:
        rates = store.query_funding_rates(market, start_ts, end_ts)
        rates = rates[:limit]
        return {
            "market": market,
            "count": len(rates),
            "rates": [
                {"ts": r.ts, "rate": r.rate,
                 "oracle_price": r.oracle_price, "mark_price": r.mark_price}
                for r in rates
            ],
        }
    except Exception as e:
        return {"market": market, "count": 0, "rates": [], "error": str(e)}


@router.get("/markets")
def list_markets(request: Request):
    """List markets with data in the store."""
    store = _get_store(request)
    if store is None:
        return {"markets": []}
    try:
        with store._lock:
            rows = store._conn.execute(
                "SELECT DISTINCT market, resolution_s, COUNT(*) as candle_count, "
                "MIN(ts) as first_ts, MAX(ts) as last_ts "
                "FROM candles GROUP BY market, resolution_s ORDER BY market"
            ).fetchall()
        return {
            "markets": [
                {"market": r[0], "resolution_s": r[1],
                 "candle_count": r[2], "first_ts": r[3], "last_ts": r[4]}
                for r in rows
            ]
        }
    except Exception as e:
        return {"markets": [], "error": str(e)}


@router.get("/check")
def check_data(
    request: Request,
    market: str = Query(...),
    resolution_s: int = Query(3600),
    start_ts: int = Query(...),
    end_ts: int = Query(...),
):
    """Check if data exists for a given market/timeframe/date range."""
    if start_ts < 0 or end_ts < 0 or start_ts >= end_ts:
        return {"market": market, "resolution_s": resolution_s, "has_data": False,
                "covers_range": False, "will_download": True, "candle_count": 0,
                "total_in_db": 0, "first_ts": None, "last_ts": None}
    store = _get_store(request)
    if store is None:
        return {
            "market": market, "resolution_s": resolution_s,
            "has_data": False, "candle_count": 0, "expected_count": 0,
            "coverage_pct": 0, "total_in_db": 0, "will_backfill": True,
            "first_ts": None, "last_ts": None,
        }
    try:
        # Check requested range
        candles = store.query_candles(market, resolution_s, start_ts, end_ts)
        has_data = len(candles) > 0

        total_in_db = store.count_candles(market, resolution_s)

        # Check if local data covers the full requested range
        covers_range = False
        if candles:
            covers_range = candles[-1].ts >= end_ts - 86400  # within 1 day of end

        will_download = not covers_range

        return {
            "market": market, "resolution_s": resolution_s,
            "has_data": has_data,
            "covers_range": covers_range,
            "will_download": will_download,
            "candle_count": len(candles),
            "total_in_db": total_in_db,
            "first_ts": candles[0].ts if candles else None,
            "last_ts": candles[-1].ts if candles else None,
        }
    except Exception as e:
        return {
            "market": market, "resolution_s": resolution_s,
            "has_data": False, "candle_count": 0, "expected_count": 0,
            "coverage_pct": 0, "total_in_db": 0, "will_backfill": True,
            "first_ts": None, "last_ts": None, "error": str(e),
        }


@router.get("/venues")
def list_venues(request: Request, market: Optional[str] = Query(None)):
    """List venues with funding rate data."""
    store = _get_store(request)
    if store is None:
        return {"venues": []}
    try:
        return {"venues": store.list_venues(market)}
    except Exception:
        return {"venues": []}
