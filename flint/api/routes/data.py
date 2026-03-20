"""Data query API — candles, funding rates."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

from ...store import FlintStore

router = APIRouter()


def _get_store(request: Request) -> FlintStore:
    store = getattr(request.app.state, "store", None)
    if store is not None:
        return store
    try:
        return FlintStore("./data/flint.duckdb")
    except Exception:
        return FlintStore(":memory:")


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
    candles = store.query_candles(market, resolution_s, start_ts, end_ts)
    candles = candles[:limit]
    return {
        "market": market,
        "resolution_s": resolution_s,
        "count": len(candles),
        "candles": [
            {
                "ts": c.ts, "open": c.open, "high": c.high,
                "low": c.low, "close": c.close, "volume": c.volume,
            }
            for c in candles
        ],
    }


@router.get("/funding")
def get_funding(
    request: Request,
    market: str = Query("SOL-PERP"),
    start_ts: Optional[int] = Query(None),
    end_ts: Optional[int] = Query(None),
    limit: int = Query(500, le=5000),
):
    store = _get_store(request)
    rates = store.query_funding_rates(market, start_ts, end_ts)
    rates = rates[:limit]
    return {
        "market": market,
        "count": len(rates),
        "rates": [
            {
                "ts": r.ts, "rate": r.rate,
                "oracle_price": r.oracle_price, "mark_price": r.mark_price,
            }
            for r in rates
        ],
    }


@router.get("/markets")
def list_markets(request: Request):
    """List markets with data in the store."""
    store = _get_store(request)
    rows = store._conn.execute(
        "SELECT DISTINCT market, resolution_s, COUNT(*) as candle_count, MIN(ts) as first_ts, MAX(ts) as last_ts "
        "FROM candles GROUP BY market, resolution_s ORDER BY market"
    ).fetchall()
    return {
        "markets": [
            {
                "market": r[0], "resolution_s": r[1],
                "candle_count": r[2], "first_ts": r[3], "last_ts": r[4],
            }
            for r in rows
        ]
    }
