"""Data query API — candles, funding rates, OI, liquidations, whale transfers,
DEX volume, freshness, correlation.  Thread-safe via shared store."""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Query, Request

from ...store import FlintStore
from ...providers import list_providers, get_provider_class
from ...analytics.correlation import compute_correlation_matrix

router = APIRouter()

_ccxt_warned: set = set()


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
    venue: Optional[str] = Query(None, description="Filter by venue"),
):
    store = _get_store(request)
    if store is None:
        return {"market": market, "resolution_s": resolution_s, "count": 0, "candles": []}
    try:
        candles = store.query_candles(market, resolution_s, start_ts, end_ts, limit=limit, venue=venue)

        # When querying all venues, deduplicate by timestamp.
        # Prefer pyth (best prices), take highest volume across venues.
        if venue is None:
            by_ts: dict = {}
            for c in candles:
                existing = by_ts.get(c.ts)
                if existing is None:
                    by_ts[c.ts] = c
                elif c.venue == "pyth":
                    # Pyth has best prices — use it, but keep higher volume
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
                 "venue": getattr(c, 'venue', 'default')}
                for c in candles
            ],
        }
    except Exception as e:
        return {"market": market, "resolution_s": resolution_s, "count": 0, "candles": [], "error": str(e)}


@router.get("/volume")
def get_volume(
    request: Request,
    market: str = Query("SOL-PERP"),
    resolution_s: int = Query(3600),
    start_ts: Optional[int] = Query(None),
    end_ts: Optional[int] = Query(None),
):
    """Get per-venue volume data for a market."""
    store = _get_store(request)
    if store is None:
        return {"market": market, "venues": {}, "count": 0}
    try:
        # Get all venues that have candle data for this market (exclude pyth — no volume)
        result: Dict[str, list] = {}
        with store._lock:
            venues_rows = store._conn.execute(
                "SELECT DISTINCT venue FROM candles WHERE market = ? AND venue != 'pyth'",
                [market]
            ).fetchall()

        for (venue_name,) in venues_rows:
            candles = store.query_candles(market, resolution_s, start_ts, end_ts, venue=venue_name)
            result[venue_name] = [{"ts": c.ts, "volume": c.volume} for c in candles]

        total = sum(len(v) for v in result.values())
        return {"market": market, "venues": result, "count": total}
    except Exception as e:
        return {"market": market, "venues": {}, "count": 0, "error": str(e)}


@router.get("/funding")
def get_funding(
    request: Request,
    market: str = Query("SOL-PERP"),
    start_ts: Optional[int] = Query(None),
    end_ts: Optional[int] = Query(None),
):
    """Get funding rates for a market, grouped by venue."""
    import time as _time
    if end_ts is None:
        end_ts = int(_time.time())
    if start_ts is None:
        start_ts = end_ts - 30 * 86400
    store = _get_store(request)
    if store is None:
        return {"market": market, "venues": {}, "count": 0}
    try:
        by_venue = store.query_funding_by_venue(market, start_ts, end_ts)
        total = sum(len(v) for v in by_venue.values())
        return {"market": market, "venues": by_venue, "count": total}
    except Exception as e:
        return {"market": market, "venues": {}, "count": 0, "error": str(e)}


@router.get("/borrow-rates")
def get_borrow_rates(
    request: Request,
    market: str = Query("SOL-PERP"),
    start_ts: Optional[int] = Query(None),
    end_ts: Optional[int] = Query(None),
):
    """Get Jupiter borrow rate history for a market."""
    store = _get_store(request)
    if store is None:
        return {"market": market, "rates": [], "count": 0}
    try:
        import time as _time
        start = start_ts or 0
        end = end_ts or int(_time.time())
        snapshots = store.query_borrow_rates(market, start, end)
        rates = [
            {"ts": s.ts, "rate_hourly": s.rate_hourly, "utilization": s.utilization,
             "cumulative_rate": s.cumulative_rate, "source": s.source}
            for s in snapshots
        ]
        return {"market": market, "rates": rates, "count": len(rates)}
    except Exception as e:
        return {"market": market, "rates": [], "count": 0, "error": str(e)}


@router.delete("/market/{market}")
def delete_market_data(market: str, request: Request):
    """Delete all data for a specific market (candles, funding, OI, etc.).

    Use this to purge corrupted data and re-download fresh.
    """
    store = _get_store(request)
    if store is None:
        from fastapi import HTTPException
        raise HTTPException(500, "Store not available")

    deleted = {}
    tables = [
        ("candles", "market"),
        ("venue_funding_rates", "market"),
        ("oracle_prices", "market"),
        ("orderbook_snapshots", "market"),
        ("open_interest", "market"),
        ("liquidations", "market"),
    ]
    with store._lock:
        for table, col in tables:
            try:
                before = store._conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", [market]).fetchone()[0]
                if before > 0:
                    store._conn.execute(f"DELETE FROM {table} WHERE {col} = ?", [market])
                    deleted[table] = before
            except Exception:
                pass
        # Also clean sync metadata
        try:
            store._conn.execute("DELETE FROM sync_metadata WHERE market = ?", [market])
        except Exception:
            pass

    return {"market": market, "deleted": deleted, "total_records": sum(deleted.values())}


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


def _check_single_market(
    store: Optional[FlintStore],
    market: str,
    resolution_s: int,
    start_ts: int,
    end_ts: int,
) -> dict:
    """Check data availability for a single market. Returns a dict."""
    if start_ts < 0 or end_ts < 0 or start_ts >= end_ts:
        return {"market": market, "resolution_s": resolution_s, "has_data": False,
                "covers_range": False, "will_download": True, "candle_count": 0,
                "total_in_db": 0, "first_ts": None, "last_ts": None}
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

        # Expected candle count for coverage calculation
        expected_count = max(1, (end_ts - start_ts) // resolution_s)
        coverage_pct = min(round(len(candles) / expected_count * 100, 1), 100.0) if expected_count else 0

        # Check if local data covers the full requested range
        # Must check BOTH ends: first candle near start AND last candle near end
        # AND coverage must be at least 80%
        covers_range = False
        if candles:
            end_ok = candles[-1].ts >= end_ts - 86400  # within 1 day of end
            start_ok = candles[0].ts <= start_ts + 86400  # within 1 day of start
            covers_range = end_ok and start_ok and coverage_pct >= 80

        will_download = not covers_range

        # --- Funding rates ---
        funding_info = {"available": False, "count": 0}
        try:
            with store._lock:
                fr_row = store._conn.execute(
                    "SELECT COUNT(*) FROM venue_funding_rates "
                    "WHERE market = ? AND ts >= ? AND ts <= ?",
                    [market, start_ts, end_ts],
                ).fetchone()
            fr_count = fr_row[0] if fr_row else 0
            funding_info = {"available": fr_count > 0, "count": fr_count}
        except Exception:
            pass

        # --- Orderbook snapshots ---
        orderbook_info = {"available": False, "count": 0}
        try:
            with store._lock:
                ob_row = store._conn.execute(
                    "SELECT COUNT(*) FROM orderbook_snapshots "
                    "WHERE market = ? AND ts >= ? AND ts <= ?",
                    [market, start_ts, end_ts],
                ).fetchone()
            ob_count = ob_row[0] if ob_row else 0
            orderbook_info = {"available": ob_count > 0, "count": ob_count}
        except Exception:
            pass

        # --- Open interest ---
        oi_info = {"available": False, "count": 0}
        try:
            with store._lock:
                oi_row = store._conn.execute(
                    "SELECT COUNT(*) FROM open_interest "
                    "WHERE market = ? AND ts >= ? AND ts <= ?",
                    [market, start_ts, end_ts],
                ).fetchone()
            oi_count = oi_row[0] if oi_row else 0
            oi_info = {"available": oi_count > 0, "count": oi_count}
        except Exception:
            pass

        return {
            "market": market, "resolution_s": resolution_s,
            "has_data": has_data,
            "covers_range": covers_range,
            "will_download": will_download,
            "candle_count": len(candles),
            "coverage_pct": coverage_pct,
            "total_in_db": total_in_db,
            "first_ts": candles[0].ts if candles else None,
            "last_ts": candles[-1].ts if candles else None,
            "funding_rates": funding_info,
            "orderbook_snapshots": orderbook_info,
            "open_interest": oi_info,
        }
    except Exception as e:
        return {
            "market": market, "resolution_s": resolution_s,
            "has_data": False, "candle_count": 0, "expected_count": 0,
            "coverage_pct": 0, "total_in_db": 0, "will_backfill": True,
            "first_ts": None, "last_ts": None,
            "funding_rates": {"available": False, "count": 0},
            "orderbook_snapshots": {"available": False, "count": 0},
            "open_interest": {"available": False, "count": 0},
            "error": str(e),
        }


@router.get("/check")
def check_data(
    request: Request,
    market: Optional[str] = Query(None),
    markets: Optional[str] = Query(None),
    resolution_s: int = Query(3600),
    start_ts: int = Query(...),
    end_ts: int = Query(...),
):
    """Check if data exists for a given market/timeframe/date range.

    Accepts either ``market`` (single) or ``markets`` (comma-separated).
    Single-market requests return the flat dict (backward compatible).
    Multi-market requests return ``{"results": [...]}``.
    """
    # Build market list from either param
    if markets:
        market_list = [m.strip() for m in markets.split(",") if m.strip()]
    elif market:
        market_list = [market]
    else:
        from fastapi import HTTPException
        raise HTTPException(400, "Provide 'market' or 'markets' query parameter")

    store = _get_store(request)

    # Single market — preserve original response shape
    if len(market_list) == 1:
        return _check_single_market(store, market_list[0], resolution_s, start_ts, end_ts)

    # Multiple markets — wrap in results list
    return {
        "results": [
            _check_single_market(store, m, resolution_s, start_ts, end_ts)
            for m in market_list
        ]
    }


@router.post("/check-markets")
def check_markets_data(
    request: Request,
    body: dict,
):
    """Check data availability for multiple markets at once.

    Body: { markets: ["SOL-PERP", "BTC-PERP"], resolution_s: 3600, start_ts: ..., end_ts: ... }
    Returns per-market availability + overall readiness.
    """
    markets = body.get("markets", [])
    resolution_s = body.get("resolution_s", 3600)
    start_ts = body.get("start_ts", 0)
    end_ts = body.get("end_ts", 0)

    if not markets or start_ts >= end_ts:
        return {"ready": False, "markets": {}, "missing": markets}

    store = _get_store(request)
    if store is None:
        return {"ready": False, "markets": {m: {"has_data": False, "candle_count": 0} for m in markets}, "missing": markets}

    result = {}
    missing = []
    for m in markets:
        try:
            candles = store.query_candles(m, resolution_s, start_ts, end_ts)
            has_data = len(candles) > 0
            if candles:
                expected = max(1, (end_ts - start_ts) // resolution_s)
                cov = len(candles) / expected * 100
                covers = (candles[-1].ts >= end_ts - 86400 and
                          candles[0].ts <= start_ts + 86400 and
                          cov >= 80)
            else:
                covers = False
            result[m] = {
                "has_data": has_data,
                "covers_range": covers,
                "candle_count": len(candles),
                "first_ts": candles[0].ts if candles else None,
                "last_ts": candles[-1].ts if candles else None,
            }
            if not covers:
                missing.append(m)
        except Exception:
            result[m] = {"has_data": False, "covers_range": False, "candle_count": 0}
            missing.append(m)

    return {"ready": len(missing) == 0, "markets": result, "missing": missing}


# ── New data endpoints ────────────────────────────────────────────────


@router.get("/providers")
def list_provider_status():
    """List all registered providers and their availability status."""
    providers_info = []
    for name in list_providers():
        cls = get_provider_class(name)
        if cls is None:
            continue
        try:
            instance = cls()
            available = instance.is_available()
        except Exception:
            available = False
        providers_info.append({
            "name": name,
            "requires_api_key": getattr(cls, "requires_api_key", False),
            "available": available,
        })
    return {"providers": providers_info}


@router.get("/open-interest/{market}")
def get_open_interest(
    request: Request,
    market: str,
    start_ts: Optional[int] = Query(None),
    end_ts: Optional[int] = Query(None),
    limit: int = Query(1000, le=10000),
):
    """Query open interest snapshots for a market."""
    store = _get_store(request)
    if store is None:
        return {"market": market, "count": 0, "records": []}
    try:
        records = store.query_open_interest(market, start_ts, end_ts)
        records = records[:limit]
        return {
            "market": market,
            "count": len(records),
            "records": [
                {"ts": r.ts, "long_oi": r.long_oi, "short_oi": r.short_oi,
                 "net_oi": r.net_oi, "total_oi": r.total_oi}
                for r in records
            ],
        }
    except Exception as e:
        return {"market": market, "count": 0, "records": [], "error": str(e)}


@router.get("/liquidations/{market}")
def get_liquidations(
    request: Request,
    market: str,
    start_ts: Optional[int] = Query(None),
    end_ts: Optional[int] = Query(None),
    limit: int = Query(500, le=5000),
):
    """Query liquidation events for a market."""
    store = _get_store(request)
    if store is None:
        return {"market": market, "count": 0, "records": []}
    try:
        records = store.query_liquidations(market, start_ts, end_ts)
        records = records[:limit]
        return {
            "market": market,
            "count": len(records),
            "records": [
                {"ts": r.ts, "side": r.side, "size": r.size,
                 "price": r.price, "tx_sig": r.tx_sig}
                for r in records
            ],
        }
    except Exception as e:
        return {"market": market, "count": 0, "records": [], "error": str(e)}


@router.get("/whale-transfers")
def get_whale_transfers(
    request: Request,
    token: Optional[str] = Query(None, description="Token mint address"),
    wallet: Optional[str] = Query(None, description="Wallet address"),
    start_ts: Optional[int] = Query(None),
    end_ts: Optional[int] = Query(None),
    limit: int = Query(500, le=5000),
):
    """Query whale transfer events, filterable by token mint and/or wallet."""
    store = _get_store(request)
    if store is None:
        return {"count": 0, "records": []}
    try:
        records = store.query_whale_transfers(
            token_mint=token, wallet=wallet, start_ts=start_ts, end_ts=end_ts,
        )
        records = records[:limit]
        return {
            "count": len(records),
            "records": [
                {"wallet": r.wallet, "token_mint": r.token_mint,
                 "amount": r.amount, "ts": r.ts, "direction": r.direction,
                 "tx_sig": r.tx_sig}
                for r in records
            ],
        }
    except Exception as e:
        return {"count": 0, "records": [], "error": str(e)}


@router.get("/dex-volume/{market}")
def get_dex_volume(
    request: Request,
    market: str,
    dex: Optional[str] = Query(None, description="Filter by DEX name"),
    start_ts: Optional[int] = Query(None),
    end_ts: Optional[int] = Query(None),
    limit: int = Query(1000, le=10000),
):
    """Query DEX volume snapshots for a market."""
    store = _get_store(request)
    if store is None:
        return {"market": market, "count": 0, "records": []}
    try:
        records = store.query_dex_volume(market, dex=dex, start_ts=start_ts, end_ts=end_ts)
        records = records[:limit]
        return {
            "market": market,
            "count": len(records),
            "records": [
                {"dex": r.dex, "ts": r.ts, "volume_usd": r.volume_usd,
                 "txn_count": r.txn_count}
                for r in records
            ],
        }
    except Exception as e:
        return {"market": market, "count": 0, "records": [], "error": str(e)}


@router.get("/freshness")
def get_data_freshness(request: Request):
    """Data freshness report across all providers and markets."""
    store = _get_store(request)
    if store is None:
        return {"freshness": []}
    try:
        return {"freshness": store.get_data_freshness()}
    except Exception as e:
        return {"freshness": [], "error": str(e)}


@router.get("/correlation")
def get_correlation(
    request: Request,
    markets: str = Query(
        ..., description="Comma-separated market symbols, e.g. SOL-PERP,BTC-PERP,ETH-PERP"
    ),
    resolution_s: int = Query(3600, description="Candle resolution in seconds"),
    start_ts: Optional[int] = Query(None),
    end_ts: Optional[int] = Query(None),
):
    """Compute pairwise correlation matrix for the requested markets."""
    store = _get_store(request)
    market_list = [m.strip() for m in markets.split(",") if m.strip()]
    if not market_list:
        return {"error": "No markets provided", "matrix": {}}
    if store is None:
        return {"markets": market_list, "matrix": {}, "error": "Store unavailable"}
    try:
        candles_by_market = {}
        for m in market_list:
            candles = store.query_candles(m, resolution_s, start_ts, end_ts)
            if candles:
                candles_by_market[m] = candles

        if len(candles_by_market) < 2:
            return {
                "markets": market_list,
                "matrix": {},
                "error": "Need candle data for at least 2 markets",
            }

        matrix = compute_correlation_matrix(candles_by_market)
        return {"markets": list(candles_by_market.keys()), "matrix": matrix}
    except Exception as e:
        return {"markets": market_list, "matrix": {}, "error": str(e)}


@router.post("/download")
def download_market_data(request: Request, body: dict):
    """Download market data for a specific market and date range.

    Body: { "market": "SOL-PERP", "resolution_s": 3600, "start_ts": ..., "end_ts": ..., "venue": "drift" }
    venue: optional — "drift" (default), "hyperliquid", "binance", "okx", "bybit"
    Returns: { "market": ..., "downloaded": N, "cached": N, "source": "drift_api" | "drift_s3" | venue }
    """
    import logging
    logger = logging.getLogger("flint.api.data")

    market = body.get("market", "SOL-PERP")
    resolution_s = body.get("resolution_s", 3600)
    start_ts = body.get("start_ts")
    end_ts = body.get("end_ts")
    # execution_venues controls which venues supply supplementary data (funding, borrow rates).
    # funding_venues is accepted as an alias for backward compatibility.
    execution_venues = body.get("execution_venues") or body.get("funding_venues")
    venue = body.get("venue", "")  # deprecated — candles always come from Pyth now

    # Convenience: accept 'days' as alternative to start_ts/end_ts
    days = body.get("days")
    if days and (not start_ts or not end_ts):
        import time as _time
        end_ts = int(_time.time())
        start_ts = end_ts - int(days) * 86400

    if not start_ts or not end_ts or start_ts >= end_ts:
        from fastapi import HTTPException
        raise HTTPException(400, "Invalid date range — provide days (e.g. 90) or start_ts + end_ts with start < end")

    store = _get_store(request)
    if store is None:
        from fastapi import HTTPException
        raise HTTPException(500, "Store not available")

    download_warnings: list = []

    try:
        # Check what we already have
        existing = store.query_candles(market, resolution_s, start_ts, end_ts)
        existing_count = len(existing)

        # Determine what ranges we still need to download
        gaps = []
        if not existing:
            gaps.append((start_ts, end_ts))
        else:
            first_ts = existing[0].ts
            last_ts = existing[-1].ts
            if first_ts > start_ts + resolution_s:
                gaps.append((start_ts, first_ts))
            if last_ts < end_ts - resolution_s:
                gaps.append((last_ts, end_ts))

        if not gaps:
            funding_fetched = 0
            if "-PERP" in market:
                try:
                    funding_fetched = _download_funding_all_venues(
                        store, market, start_ts, end_ts, logger,
                        venues=execution_venues, warnings=download_warnings,
                    )
                except Exception as e:
                    msg = f"Funding sync failed: {e}"
                    logger.warning("Funding sync failed for %s: %s", market, e)
                    download_warnings.append(msg)

            # Download per-venue candles (volume data) if not already populated
            volume_merged = 0
            has_venue_data = False
            try:
                with store._lock:
                    vc = store._conn.execute(
                        "SELECT COUNT(*) FROM candles WHERE market = ? AND venue NOT IN ('pyth', 'default') "
                        "AND ts >= ? AND ts <= ?",
                        [market, start_ts, end_ts],
                    ).fetchone()
                has_venue_data = vc and vc[0] > 100
            except Exception:
                pass
            if not has_venue_data:
                volume_merged = _download_venue_volume(
                    store, market, resolution_s, start_ts, end_ts, logger, download_warnings
                )

            # Update sync_metadata for freshness tracking
            try:
                from ...models import SyncMetadata
                import time as _time
                store.upsert_sync_metadata(SyncMetadata(
                    provider="local",
                    market=market,
                    data_type="candles",
                    last_sync_ts=int(_time.time()),
                    record_count=existing_count,
                    status="ok",
                    error_msg="",
                ))
                if funding_fetched > 0:
                    store.upsert_sync_metadata(SyncMetadata(
                        provider="multi_venue",
                        market=market,
                        data_type="funding_rates",
                        last_sync_ts=int(_time.time()),
                        record_count=funding_fetched,
                        status="ok",
                        error_msg="",
                    ))
            except Exception as e:
                logger.warning("Failed to update sync_metadata: %s", e)
            return {
                "market": market,
                "resolution_s": resolution_s,
                "downloaded": 0,
                "cached": existing_count,
                "existing": existing_count,
                "total": existing_count,
                "funding_fetched": funding_fetched,
                "volume_merged": volume_merged,
                "source": "local",
                "skipped": True,
                "message": "All candles already cached for this range",
                "warnings": download_warnings,
            }

        # Download only the missing gaps
        total_fetched = 0
        total_cached = 0
        source = "none"
        errors: list = []

        if venue:
            logger.warning(
                "download: 'venue' param is deprecated for candle source selection; "
                "candles now always come from Pyth. Use 'execution_venues' to control "
                "which venues supply supplementary data (funding, borrow rates)."
            )

        for gap_start, gap_end in gaps:
            fetched, err = _download_pyth_candles(market, resolution_s, gap_start, gap_end)
            source = "pyth"
            if fetched:
                total_fetched += len(fetched)
                total_cached += store.upsert_candles(fetched)
            if err:
                errors.append(err)
                download_warnings.append(f"Candle download ({source}): {err}")

        # Merge volume from Hyperliquid into Pyth candles (Pyth has price-only, volume=0)
        venue_candle_count = _download_venue_volume(
            store, market, resolution_s, start_ts, end_ts, logger, download_warnings
        )

        # Also download venue candles if explicitly requested
        for ev in (execution_venues or []):
            if ev in ("jupiter",):
                continue
            try:
                venue_candles, _ = _download_range_for_venue(market, resolution_s, start_ts, end_ts, ev, logger)
                if venue_candles:
                    store.upsert_candles(venue_candles)
                    venue_candle_count += len(venue_candles)
            except Exception as e:
                download_warnings.append(f"{ev} volume: {e}")

        # Re-count total
        final_count = len(store.query_candles(market, resolution_s, start_ts, end_ts))

        # Also fetch funding rates for perp markets
        funding_fetched = 0
        if "-PERP" in market:
            try:
                funding_fetched = _download_funding_all_venues(
                    store, market, start_ts, end_ts, logger,
                    venues=execution_venues, warnings=download_warnings,
                )
            except Exception as e:
                msg = f"Funding sync failed: {e}"
                logger.warning("Funding sync failed for %s: %s", market, e)
                download_warnings.append(msg)

        # Download Jupiter borrow rates if Jupiter is in execution_venues
        if execution_venues and "jupiter" in execution_venues and "-PERP" in market:
            try:
                from ...models import BorrowSnapshot
                _JUPITER_MINTS = {
                    "SOL-PERP": "So11111111111111111111111111111111111111112",
                    "ETH-PERP": "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",
                    "BTC-PERP": "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh",
                }
                mint = _JUPITER_MINTS.get(market)
                if mint:
                    import httpx as _httpx
                    resp = _httpx.get(
                        f"https://perps-api.jup.ag/v1/pool-info?mint={mint}",
                        timeout=15,
                    )
                    resp.raise_for_status()
                    pool = resp.json()
                    # Average long/short borrow rate as hourly rate
                    long_rate = float(pool.get("longBorrowRatePercent", 0)) / 100
                    short_rate = float(pool.get("shortBorrowRatePercent", 0)) / 100
                    avg_rate = (long_rate + short_rate) / 2
                    utilization = (
                        float(pool.get("longUtilizationPercent", 0))
                        + float(pool.get("shortUtilizationPercent", 0))
                    ) / 200  # average of both sides, normalized to 0-1
                    now_ts = int(time.time())
                    snapshot = BorrowSnapshot(
                        market=market, ts=now_ts, rate_hourly=avg_rate,
                        utilization=utilization, cumulative_rate=0.0, source="jupiter_api",
                    )
                    store.upsert_borrow_rates([snapshot])
                    logger.info("Jupiter borrow rate for %s: %.6f hourly (util %.1f%%)",
                                market, avg_rate, utilization * 100)
                else:
                    download_warnings.append(f"Jupiter borrow rates: {market} not supported (only SOL/ETH/BTC)")
            except Exception as e:
                logger.warning("Jupiter borrow rate failed for %s: %s", market, e)
                download_warnings.append(f"Jupiter borrow rates: {e}")

        # Update sync_metadata for freshness tracking
        try:
            from ...models import SyncMetadata
            import time as _time
            store.upsert_sync_metadata(SyncMetadata(
                provider=source or "drift_api",
                market=market,
                data_type="candles",
                last_sync_ts=int(_time.time()),
                record_count=final_count,
                status="ok",
                error_msg="",
            ))
            if funding_fetched > 0:
                store.upsert_sync_metadata(SyncMetadata(
                    provider="multi_venue",
                    market=market,
                    data_type="funding_rates",
                    last_sync_ts=int(_time.time()),
                    record_count=funding_fetched,
                    status="ok",
                    error_msg="",
                ))
        except Exception as e:
            logger.warning("Failed to update sync_metadata: %s", e)

        result = {
            "market": market,
            "resolution_s": resolution_s,
            "downloaded": total_fetched,
            "cached": total_cached,
            "existing": existing_count,
            "total": final_count,
            "funding_fetched": funding_fetched,
            "venue_candle_count": venue_candle_count,
            "source": source,
            "warnings": download_warnings,
        }
        if errors:
            result["error"] = "; ".join(errors)
        return result

    except Exception as e:
        logger.error("Download failed for %s: %s", market, e)
        download_warnings.append(f"Fatal error: {e}")
        return {
            "market": market,
            "resolution_s": resolution_s,
            "downloaded": 0,
            "cached": 0,
            "existing": 0,
            "total": 0,
            "error": str(e),
            "warnings": download_warnings,
        }


# Venues to download volume from, in priority order.
# All free, no key needed. Each stores candles under its own venue tag.
_VOLUME_VENUES = [
    ("hyperliquid", "native"),  # native = use HyperliquidCandleProvider directly
    ("okx", "ccxt"),
    ("gate", "ccxt"),
    ("binanceus", "ccxt"),
]


def _download_venue_volume(store, market, resolution_s, start_ts, end_ts, logger, warnings):
    """Download candles from multiple venues and store per-venue.

    Like funding rates, volume is stored per-venue so you can compare
    Drift vs Hyperliquid vs OKX volume. Also merges the best available
    volume into zero-volume Pyth candles.

    Returns total number of venue candles stored.
    """
    from ...models import Candle as CandleModel
    total_stored = 0

    for venue_name, provider_type in _VOLUME_VENUES:
        try:
            venue_candles = []

            if provider_type == "native" and venue_name == "hyperliquid":
                from ...providers.hyperliquid_candles import HyperliquidCandleProvider, _FLINT_TO_HL
                if market not in _FLINT_TO_HL:
                    continue
                hl = HyperliquidCandleProvider()
                try:
                    interval_map = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}
                    interval = interval_map.get(resolution_s, "1h")
                    venue_candles = hl.fetch_candles(market, start_ts, end_ts, resolution=interval)
                finally:
                    hl._client.close()

            elif provider_type == "ccxt":
                from ...providers.ccxt_provider import CCXTProvider
                provider = CCXTProvider(exchange=venue_name)
                try:
                    venue_candles = provider.fetch_candles(market, resolution_s, start_ts, end_ts)
                finally:
                    provider.close()

            if venue_candles:
                # Tag with venue name and store (primary key includes venue)
                tagged = [
                    CandleModel(
                        ts=c.ts, open=c.open, high=c.high, low=c.low,
                        close=c.close, volume=c.volume, market=c.market,
                        resolution_s=c.resolution_s, venue=venue_name,
                    )
                    for c in venue_candles if c.volume > 0
                ]
                if tagged:
                    stored = store.upsert_candles(tagged)
                    total_stored += stored
                    logger.info("%s: stored %d candles for %s", venue_name, stored, market)

        except Exception as e:
            logger.debug("%s volume download failed for %s: %s", venue_name, market, e)

    # Merge best volume into zero-volume Pyth candles
    # Priority: use highest-volume venue per timestamp
    pyth_candles = store.query_candles(market, resolution_s, start_ts, end_ts, venue="pyth")
    if not pyth_candles:
        # Fall back to default venue
        pyth_candles = store.query_candles(market, resolution_s, start_ts, end_ts)

    zero_vol = [c for c in pyth_candles if c.volume == 0]
    if zero_vol:
        # Collect volume from all venues
        vol_by_ts: dict = {}  # ts → (volume, venue)
        for venue_name, _ in _VOLUME_VENUES:
            venue_data = store.query_candles(market, resolution_s, start_ts, end_ts, venue=venue_name)
            for c in venue_data:
                if c.volume > 0:
                    existing = vol_by_ts.get(c.ts)
                    if existing is None or c.volume > existing[0]:
                        vol_by_ts[c.ts] = (c.volume, venue_name)

        updated = []
        for c in zero_vol:
            entry = vol_by_ts.get(c.ts)
            if entry:
                vol, _ = entry
                updated.append(CandleModel(
                    ts=c.ts, open=c.open, high=c.high, low=c.low,
                    close=c.close, volume=vol, market=c.market,
                    resolution_s=c.resolution_s, venue=c.venue,
                ))
        if updated:
            store.upsert_candles(updated)
            logger.info("Merged volume into %d Pyth candles for %s", len(updated), market)
            total_stored += len(updated)

    return total_stored


def _download_pyth_candles(market: str, resolution_s: int, start_ts: int, end_ts: int):
    """Download candles from Pyth Benchmarks API.

    Returns (List[Candle], Optional[error_msg])
    """
    from ...providers.pyth_candles import PythCandleProvider
    provider = PythCandleProvider()
    try:
        candles = provider.fetch_candles(market, resolution_s, start_ts, end_ts)
        return candles, None
    except Exception as e:
        return [], str(e)
    finally:
        provider.close()


def _download_range(market: str, resolution_s: int, start_ts: int, end_ts: int, logger):
    """Try all providers in order for a specific time range.

    Returns (candles, error_message) tuple.
    """
    errors = []

    # Try Drift Data API
    try:
        from ...providers.drift_candles import DriftCandleProvider
        provider = DriftCandleProvider()
        try:
            fetched = provider.fetch_candles(market, resolution_s, start_ts, end_ts)
        finally:
            provider.close()
        if fetched:
            return fetched, None
    except Exception as e:
        errors.append(f"Drift API: {e}")
        logger.warning("Drift API failed for %s: %s", market, e)

    # Fallback to S3
    try:
        from ...providers.drift_s3 import DriftS3Provider
        provider = DriftS3Provider()
        try:
            fetched = provider.fetch_candles(market, resolution_s, start_ts, end_ts)
        finally:
            provider.close()
        if fetched:
            return fetched, None
    except Exception as e:
        errors.append(f"S3: {e}")
        logger.warning("Drift S3 failed for %s: %s", market, e)

    # Fallback to CoinGecko
    try:
        from ...providers.coingecko import CoinGeckoProvider
        cg = CoinGeckoProvider()
        try:
            if cg.resolve_id(market):
                fetched = cg.fetch_candles(market, resolution_s, start_ts, end_ts)
                if fetched:
                    return fetched, None
        finally:
            cg.close()
    except Exception as e:
        errors.append(f"CoinGecko: {e}")
        logger.warning("CoinGecko failed for %s: %s", market, e)

    # Fallback to Hyperliquid (if market is a known Hyperliquid market)
    try:
        from ...providers.hyperliquid_candles import HyperliquidCandleProvider, _FLINT_TO_HL
        if market in _FLINT_TO_HL:
            provider = HyperliquidCandleProvider()
            try:
                _SEC_TO_INTERVAL = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}
                interval = _SEC_TO_INTERVAL.get(resolution_s, "1h")
                fetched = provider.fetch_candles(market, start_ts, end_ts, resolution=interval)
            finally:
                provider.close()
            if fetched:
                return fetched, None
    except Exception as e:
        errors.append(f"Hyperliquid: {e}")
        logger.warning("Hyperliquid failed for %s: %s", market, e)

    # Fallback to CCXT (works for spot AND perp on any exchange)
    try:
        from ...providers.ccxt_provider import CCXTProvider
        # For spot markets (SOL-SPOT, BTC-SPOT) use Binance spot
        # For perp markets, try OKX (no geo-block)
        if market.endswith("-SPOT"):
            base = market.replace("-SPOT", "")
            ccxt_symbol = f"{base}/USDT"
            exchange = "okx"  # OKX has no geo-block, Binance does for US
        else:
            ccxt_symbol = market
            exchange = "okx"

        provider = CCXTProvider(exchange)
        try:
            fetched = provider.fetch_candles(ccxt_symbol, resolution_s, start_ts, end_ts)
            # Re-tag with the original market name for storage
            if fetched:
                from ...models import Candle
                fetched = [
                    Candle(market=market, ts=c.ts, open=c.open, high=c.high,
                           low=c.low, close=c.close, volume=c.volume,
                           resolution_s=c.resolution_s)
                    for c in fetched
                ]
                return fetched, None
        finally:
            provider.close()
    except Exception as e:
        errors.append(f"CCXT: {e}")
        logger.warning("CCXT failed for %s: %s", market, e)

    return [], "; ".join(errors) if errors else "No provider found"


def _download_range_for_venue(market: str, resolution_s: int, start_ts: int, end_ts: int, venue: str, logger):
    """Download candles from a specific venue.

    Returns (candles, error_message) tuple.
    """
    if venue == "drift":
        # Drift API + S3 chain
        try:
            from ...providers.drift_candles import DriftCandleProvider
            provider = DriftCandleProvider()
            try:
                fetched = provider.fetch_candles(market, resolution_s, start_ts, end_ts)
            finally:
                provider.close()
            if fetched:
                return fetched, None
        except Exception as e:
            logger.warning("Drift API failed: %s", e)
        # Try S3 fallback
        try:
            from ...providers.drift_s3 import DriftS3Provider
            provider = DriftS3Provider()
            try:
                fetched = provider.fetch_candles(market, resolution_s, start_ts, end_ts)
            finally:
                provider.close()
            if fetched:
                return fetched, None
        except Exception as e:
            logger.warning("Drift S3 failed: %s", e)
        return [], "Drift data unavailable"

    elif venue == "hyperliquid":
        try:
            from ...providers.hyperliquid_candles import HyperliquidCandleProvider, _FLINT_TO_HL
            if market not in _FLINT_TO_HL:
                return [], f"Market {market} not available on Hyperliquid"
            provider = HyperliquidCandleProvider()
            try:
                _SEC_TO_INTERVAL = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}
                interval = _SEC_TO_INTERVAL.get(resolution_s, "1h")
                fetched = provider.fetch_candles(market, start_ts, end_ts, resolution=interval)
            finally:
                provider.close()
            if fetched:
                return fetched, None
        except Exception as e:
            return [], f"Hyperliquid: {e}"
        return [], "Hyperliquid data unavailable"

    elif venue in ("binance", "okx", "bybit"):
        try:
            from ...providers.ccxt_provider import CCXTProvider
            from ...models import Candle
            if market.endswith("-SPOT"):
                base = market.replace("-SPOT", "")
                ccxt_symbol = f"{base}/USDT"
            else:
                ccxt_symbol = market
            provider = CCXTProvider(venue)
            try:
                fetched = provider.fetch_candles(ccxt_symbol, resolution_s, start_ts, end_ts)
            finally:
                provider.close()
            if fetched:
                # Re-tag with original market name and venue
                tagged = [
                    Candle(market=market, ts=c.ts, open=c.open, high=c.high,
                           low=c.low, close=c.close, volume=c.volume,
                           resolution_s=c.resolution_s, venue=venue)
                    for c in fetched
                ]
                return tagged, None
        except Exception as e:
            return [], f"{venue}: {e}"
        return [], f"{venue} data unavailable"

    return [], f"Unknown venue: {venue}"


FUNDING_VENUES = ["drift", "hyperliquid", "okx", "bybit", "gateio", "bitget", "dydx"]


def _forward_fill_to_hourly(snapshots: list) -> list:
    """Forward-fill funding snapshots to hourly resolution.

    Venues like OKX/Bybit report every 8h. This fills intermediate hours
    with the previous rate so the DB always has hourly data. Gaps >24h
    are left empty (not filled).
    """
    if not snapshots:
        return snapshots

    from ...providers.funding_rates import FundingSnapshot

    sorted_snaps = sorted(snapshots, key=lambda s: s.ts)
    filled = []

    for i, snap in enumerate(sorted_snaps):
        hour_ts = (snap.ts // 3600) * 3600
        filled.append(FundingSnapshot(
            venue=snap.venue, market=snap.market, ts=hour_ts,
            rate_hourly=snap.rate_hourly, mark_price=snap.mark_price,
            index_price=snap.index_price,
        ))

        # Forward-fill hourly until next point (max 24h)
        if i < len(sorted_snaps) - 1:
            next_ts = (sorted_snaps[i + 1].ts // 3600) * 3600
            gap = next_ts - hour_ts
            if 3600 < gap <= 86400:
                t = hour_ts + 3600
                while t < next_ts:
                    filled.append(FundingSnapshot(
                        venue=snap.venue, market=snap.market, ts=t,
                        rate_hourly=snap.rate_hourly, mark_price=snap.mark_price,
                        index_price=snap.index_price,
                    ))
                    t += 3600

    return filled


def _fetch_historical_mark_prices(
    market: str, venue: str, start_ts: int, end_ts: int, logger
) -> Dict[int, tuple]:
    """Fetch historical mark price candles for a venue.

    Returns {hourly_ts: (mark_price, index_price)} for joining with funding records.
    Only fetches for venues that have historical mark price APIs.
    """
    import httpx
    prices: Dict[int, tuple] = {}

    if venue == "binance":
        from ...providers.funding_rates import BINANCE_SYMBOLS
        symbol = BINANCE_SYMBOLS.get(market)
        if not symbol:
            return prices
        client = httpx.Client(timeout=15)
        try:
            cursor = start_ts * 1000
            while cursor < end_ts * 1000:
                resp = client.get("https://fapi.binance.com/fapi/v1/markPriceKlines", params={
                    "symbol": symbol, "interval": "1h",
                    "startTime": cursor, "endTime": end_ts * 1000, "limit": 1500,
                })
                if resp.status_code != 200:
                    break
                data = resp.json()
                if not data:
                    break
                for row in data:
                    ts = int(row[0]) // 1000
                    mark_close = float(row[4])  # close of mark price candle
                    prices[ts] = (mark_close, mark_close)
                cursor = int(data[-1][0]) + 1
                import time; time.sleep(0.1)
        except Exception as e:
            logger.debug("Binance mark klines: %s", e)
        finally:
            client.close()

    elif venue == "okx":
        from ...providers.funding_rates import OKX_SYMBOLS
        inst_id = OKX_SYMBOLS.get(market)
        if not inst_id:
            return prices
        client = httpx.Client(timeout=15)
        try:
            cursor = str(end_ts * 1000 + 1)
            for _ in range(200):
                resp = client.get("https://www.okx.com/api/v5/market/history-mark-price-candles", params={
                    "instId": inst_id, "bar": "1H", "after": cursor, "limit": "100",
                })
                if resp.status_code != 200:
                    break
                data = resp.json().get("data", [])
                if not data:
                    break
                for row in data:
                    ts = int(row[0]) // 1000
                    if start_ts <= ts <= end_ts:
                        mark_close = float(row[4])
                        prices[ts] = (mark_close, mark_close)
                oldest_ts = int(data[-1][0]) // 1000
                if oldest_ts <= start_ts:
                    break
                cursor = data[-1][0]
                import time; time.sleep(0.1)
        except Exception as e:
            logger.debug("OKX mark klines: %s", e)
        finally:
            client.close()

    elif venue == "bybit":
        from ...providers.funding_rates import BYBIT_SYMBOLS
        symbol = BYBIT_SYMBOLS.get(market)
        if not symbol:
            return prices
        client = httpx.Client(timeout=15)
        try:
            cursor_end = end_ts * 1000
            for _ in range(200):
                resp = client.get("https://api.bybit.com/v5/market/mark-price-kline", params={
                    "category": "linear", "symbol": symbol, "interval": "60",
                    "start": str(start_ts * 1000), "end": str(cursor_end), "limit": "1000",
                })
                if resp.status_code != 200:
                    break
                items = resp.json().get("result", {}).get("list", [])
                if not items:
                    break
                for row in items:
                    ts = int(row[0]) // 1000
                    if start_ts <= ts <= end_ts:
                        mark_close = float(row[4])
                        prices[ts] = (mark_close, mark_close)
                oldest = min(int(row[0]) for row in items)
                if oldest // 1000 <= start_ts:
                    break
                cursor_end = oldest - 1
                import time; time.sleep(0.1)
        except Exception as e:
            logger.debug("Bybit mark klines: %s", e)
        finally:
            client.close()

    elif venue == "hyperliquid":
        # Hyperliquid has no mark price candle API.
        # Use trade candle close as proxy (mark ≈ trade for liquid markets).
        from ...providers.funding_rates import HYPERLIQUID_SYMBOLS
        symbol = HYPERLIQUID_SYMBOLS.get(market)
        if not symbol:
            return prices
        client = httpx.Client(timeout=15)
        try:
            resp = client.post("https://api.hyperliquid.xyz/info", json={
                "type": "candleSnapshot",
                "req": {"coin": symbol, "interval": "1h",
                        "startTime": start_ts * 1000, "endTime": end_ts * 1000},
            })
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    for row in data:
                        ts = int(row.get("t", 0)) // 1000
                        close = float(row.get("c", 0))
                        if close > 0 and start_ts <= ts <= end_ts:
                            prices[ts] = (close, close)
        except Exception as e:
            logger.debug("Hyperliquid trade candles: %s", e)
        finally:
            client.close()

    if prices:
        logger.info("%s: fetched %d historical mark prices for %s", venue, len(prices), market)
    return prices


def _enrich_funding_with_mark_prices(
    snapshots: list, mark_prices: Dict[int, tuple],
) -> list:
    """Replace static mark/index prices with historical per-timestamp prices."""
    from ...providers.funding_rates import FundingSnapshot
    enriched = []
    for s in snapshots:
        hour_ts = (s.ts // 3600) * 3600
        prices = mark_prices.get(hour_ts)
        if prices:
            enriched.append(FundingSnapshot(
                venue=s.venue, market=s.market, ts=s.ts,
                rate_hourly=s.rate_hourly,
                mark_price=prices[0], index_price=prices[1],
            ))
        else:
            enriched.append(s)
    return enriched


def _fetch_venue_open_interest(
    store, market: str, venue: str, start_ts: int, end_ts: int, logger
) -> int:
    """Fetch historical open interest for a specific venue and store it.

    Uses CCXT fetch_open_interest_history for venues that support it.
    Returns number of records stored.
    """
    from ...providers.funding_rates import (
        BINANCE_SYMBOLS, OKX_SYMBOLS, BYBIT_SYMBOLS, HYPERLIQUID_SYMBOLS,
    )
    from ...models import OpenInterest

    # Map venue to CCXT exchange + symbol
    VENUE_TO_CCXT = {
        "binance": ("binanceusdm", BINANCE_SYMBOLS),
        "okx": ("okx", {k: k.replace("-PERP", "/USDT:USDT") for k in OKX_SYMBOLS}),
        "bybit": ("bybit", {k: k.replace("-PERP", "/USDT:USDT") for k in BYBIT_SYMBOLS}),
    }

    if venue not in VENUE_TO_CCXT:
        # Hyperliquid: use their native API (live snapshot only — no history)
        if venue == "hyperliquid":
            import httpx
            symbol = HYPERLIQUID_SYMBOLS.get(market)
            if not symbol:
                return 0
            try:
                client = httpx.Client(timeout=15)
                resp = client.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"})
                client.close()
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and len(data) >= 2:
                        meta = data[0]
                        ctxs = data[1]
                        symbols = [u.get("name", "") for u in meta.get("universe", [])]
                        for i, ctx in enumerate(ctxs):
                            if i < len(symbols) and symbols[i] == symbol:
                                oi_val = float(ctx.get("openInterest", 0))
                                if oi_val > 0:
                                    import time as _time
                                    now_ts = int(_time.time())
                                    rec = OpenInterest(venue="hyperliquid", market=market,
                                                       ts=now_ts, long_oi=oi_val / 2, short_oi=oi_val / 2)
                                    return store.upsert_open_interest([rec])
            except Exception as e:
                logger.debug("Hyperliquid OI: %s", e)
        return 0

    exchange_name, symbol_map = VENUE_TO_CCXT[venue]
    ccxt_symbol = symbol_map.get(market)
    if not ccxt_symbol:
        return 0

    # For CCXT-mapped symbols, ensure proper format
    if not "/" in ccxt_symbol:
        ccxt_symbol = f"{ccxt_symbol}/USDT:USDT"

    try:
        import ccxt as _ccxt
        exchange = getattr(_ccxt, exchange_name)({"enableRateLimit": True})

        records = []
        since = start_ts * 1000
        for _ in range(50):
            try:
                history = exchange.fetch_open_interest_history(
                    ccxt_symbol, timeframe="1h", since=since, limit=200
                )
            except Exception:
                break
            if not history:
                break
            for h in history:
                ts = h.get("timestamp", 0) // 1000
                if ts < start_ts or ts > end_ts:
                    continue
                oi = float(h.get("openInterestAmount", 0))
                if oi > 0:
                    records.append(OpenInterest(
                        venue=venue, market=market, ts=ts,
                        long_oi=oi / 2, short_oi=oi / 2,  # API gives total, assume 50/50
                    ))
            last_ts = history[-1].get("timestamp", 0)
            if last_ts <= since or last_ts // 1000 >= end_ts:
                break
            since = last_ts + 1
            import time; time.sleep(0.2)

        if records:
            stored = store.upsert_open_interest(records)
            logger.info("%s: stored %d OI records for %s", venue, stored, market)
            return stored
    except ImportError:
        logger.debug("ccxt not installed, skipping %s OI", venue)
    except Exception as e:
        logger.warning("%s OI fetch failed: %s", venue, e)

    return 0


def _download_funding_all_venues(store, market: str, start_ts: int, end_ts: int, logger, venues=None, warnings=None) -> int:
    """Download funding rates for a market from selected venues.

    Args:
        venues: Optional list of venue IDs to download from.
                If None, downloads from all available venues.
        warnings: Optional list to append provider failure messages to.
    """
    if "-PERP" not in market:
        return 0

    from ...providers.funding_rates import (
        BinanceFundingProvider, DriftFundingProvider, HyperliquidFundingProvider,
        OKXFundingProvider, BybitFundingProvider,
        GateioFundingProvider, BitgetFundingProvider, DydxFundingProvider,
        CCXTFundingProvider, CCXT_FUNDING_EXCHANGES,
    )

    native_providers: dict = {
        "drift": DriftFundingProvider,
        "hyperliquid": HyperliquidFundingProvider,
        "binance": BinanceFundingProvider,
        "okx": OKXFundingProvider,
        "bybit": BybitFundingProvider,
        "gateio": GateioFundingProvider,
        "bitget": BitgetFundingProvider,
        "dydx": DydxFundingProvider,
    }

    ccxt_exchanges = set(CCXT_FUNDING_EXCHANGES)

    # If venues specified, filter to only those
    if venues is not None:
        venue_set = set(venues)
    else:
        venue_set = set(native_providers.keys()) | ccxt_exchanges

    total = 0

    # Venues that have historical mark price candle APIs
    MARK_PRICE_VENUES = {"binance", "okx", "bybit", "hyperliquid"}

    # Native providers
    for venue, ProviderClass in native_providers.items():
        if venue not in venue_set:
            continue
        try:
            provider = ProviderClass()
            try:
                snapshots = provider.fetch_funding(market, start_ts, end_ts)
            finally:
                provider.close()
            if snapshots:
                # Enrich with historical mark prices (Drift/dYdX already have them per-record)
                if venue in MARK_PRICE_VENUES and venue not in ("drift", "dydx"):
                    mark_prices = _fetch_historical_mark_prices(market, venue, start_ts, end_ts, logger)
                    if mark_prices:
                        snapshots = _enrich_funding_with_mark_prices(snapshots, mark_prices)

                hourly = _forward_fill_to_hourly(snapshots)
                stored = store.upsert_venue_funding(hourly)
                total += stored
                logger.info("%s: %d raw → %d hourly funding rates for %s",
                            venue, len(snapshots), stored, market)
        except Exception as e:
            logger.warning("%s funding failed for %s: %s", venue, market, e)
            if warnings is not None:
                warnings.append(f"{venue} funding unavailable: {e}")

    # CCXT exchanges (mexc, phemex, bitmex, etc.)
    for exchange in ccxt_exchanges:
        if exchange not in venue_set:
            continue
        try:
            provider = CCXTFundingProvider(exchange)
            try:
                snapshots = provider.fetch_funding(market, start_ts, end_ts)
            finally:
                provider.close()
            if snapshots:
                hourly = _forward_fill_to_hourly(snapshots)
                stored = store.upsert_venue_funding(hourly)
                total += stored
                logger.info("ccxt/%s: %d raw → %d hourly funding rates for %s",
                            exchange, len(snapshots), stored, market)
        except Exception as e:
            logger.warning("ccxt/%s funding failed for %s: %s", exchange, market, e)
            if warnings is not None:
                warn_key = f"ccxt/{exchange}"
                if warn_key not in _ccxt_warned:
                    _ccxt_warned.add(warn_key)
                    warnings.append(f"ccxt/{exchange} funding unavailable: {e}")

    # Also fetch per-venue open interest alongside funding
    OI_VENUES = {"binance", "okx", "bybit", "hyperliquid"}
    for venue in venue_set & OI_VENUES:
        try:
            _fetch_venue_open_interest(store, market, venue, start_ts, end_ts, logger)
        except Exception as e:
            logger.debug("%s OI fetch failed: %s", venue, e)

    return total


@router.get("/available-markets")
def list_available_markets():
    """List all markets available for download from Drift (perp + spot) + CoinGecko."""
    from ...collector.tasks import MARKET_INDEX, SPOT_MARKET_INDEX, SPOT_WITH_CANDLES

    markets = []
    seen_spot = set()

    # Perp markets (all have candle data)
    for market, idx in sorted(MARKET_INDEX.items(), key=lambda x: x[1]):
        markets.append({
            "market": market,
            "source": "drift",
            "market_index": idx,
            "type": "perp",
        })

    # Spot markets from Drift (those with confirmed candle data)
    for market, idx in sorted(SPOT_MARKET_INDEX.items(), key=lambda x: x[1]):
        if market not in SPOT_WITH_CANDLES:
            continue
        markets.append({
            "market": market,
            "source": "drift",
            "market_index": idx,
            "type": "spot",
        })
        seen_spot.add(market)

    # BTC and ETH spot from CoinGecko (Drift doesn't have spot candle data for these)
    for symbol in ("BTC", "ETH"):
        if symbol not in seen_spot:
            markets.append({
                "market": symbol,
                "source": "coingecko",
                "market_index": -1,
                "type": "spot",
            })

    # CEX spot markets (via CCXT/OKX) — real CEX prices for basis computation
    cex_spot = ["SOL-SPOT", "BTC-SPOT", "ETH-SPOT", "DOGE-SPOT", "AVAX-SPOT",
                "LINK-SPOT", "ARB-SPOT", "SUI-SPOT", "XRP-SPOT"]
    for mkt in cex_spot:
        markets.append({
            "market": mkt,
            "source": "ccxt",
            "market_index": -1,
            "type": "cex-spot",
        })

    return {"markets": markets}
