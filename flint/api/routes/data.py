"""Data query API — candles, funding rates, OI, liquidations, whale transfers,
DEX volume, freshness, correlation.  Thread-safe via shared store."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, Query, Request

from ...store import FlintStore
from ...providers import list_providers, get_provider_class
from ...analytics.correlation import compute_correlation_matrix
from ...services.data import (
    dl_set, dl_get,
    check_single_market,
    download_venue_volume,
    download_pyth_candles,
    download_range,
    download_range_for_venue,
    download_funding_all_venues,
    list_available_markets as _list_available_markets,
)

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
                 "venue": getattr(c, 'venue', 'pyth')}
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
        venues_rows = [(v,) for v in store.list_venues_for_market(market)]

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

    deleted = store.delete_market_data(market)
    return {"market": market, "deleted": deleted, "total_records": sum(deleted.values())}


@router.get("/markets")
def list_markets(request: Request):
    """List markets with data in the store."""
    store = _get_store(request)
    if store is None:
        return {"markets": []}
    try:
        return {"markets": store.list_markets_with_data()}
    except Exception as e:
        return {"markets": [], "error": str(e)}


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
        return check_single_market(store, market_list[0], resolution_s, start_ts, end_ts)

    # Multiple markets — wrap in results list
    return {
        "results": [
            check_single_market(store, m, resolution_s, start_ts, end_ts)
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
                    funding_fetched = download_funding_all_venues(
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
                has_venue_data = store.count_venue_candles(market, start_ts, end_ts) > 100
            except Exception:
                pass
            if not has_venue_data:
                volume_merged = download_venue_volume(
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
            fetched, err = download_pyth_candles(market, resolution_s, gap_start, gap_end)
            source = "pyth"
            if fetched:
                total_fetched += len(fetched)
                total_cached += store.upsert_candles(fetched)
            if err:
                errors.append(err)
                download_warnings.append(f"Candle download ({source}): {err}")

        # Merge volume from Hyperliquid into Pyth candles (Pyth has price-only, volume=0)
        venue_candle_count = download_venue_volume(
            store, market, resolution_s, start_ts, end_ts, logger, download_warnings
        )

        # Also download venue candles if explicitly requested
        for ev in (execution_venues or []):
            if ev in ("jupiter",):
                continue
            try:
                venue_candles, _ = download_range_for_venue(market, resolution_s, start_ts, end_ts, ev, logger)
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
                funding_fetched = download_funding_all_venues(
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


@router.post("/download-async")
def start_async_download(request: Request, body: dict):
    """Start an async download for one or more markets. Returns immediately with a download ID.

    Body: {
        "markets": ["SOL-PERP", "BTC-PERP", "ETH-PERP"],
        "resolution_s": 3600,
        "start_ts": ..., "end_ts": ...,
        "days": 90,                     // alternative to start_ts/end_ts
        "execution_venues": ["drift", "hyperliquid"]
    }
    Returns: { "id": "abc123", "status": "downloading", "markets": [...] }
    Poll: GET /download-async/{id}/status
    """
    markets = body.get("markets") or [body.get("market", "SOL-PERP")]
    resolution_s = body.get("resolution_s", 3600)
    start_ts = body.get("start_ts")
    end_ts = body.get("end_ts")
    days = body.get("days")
    execution_venues = body.get("execution_venues") or body.get("funding_venues")

    if days and (not start_ts or not end_ts):
        end_ts = int(time.time())
        start_ts = end_ts - int(days) * 86400

    if not start_ts or not end_ts or start_ts >= end_ts:
        from fastapi import HTTPException
        raise HTTPException(400, "Invalid date range")

    dl_id = str(uuid.uuid4())[:8]
    market_progress = [{"market": m, "status": "pending", "detail": ""} for m in markets]
    started_at = time.time()

    dl_set(dl_id, status="downloading", progress={
        "markets": market_progress, "pct": 0, "elapsed_s": 0,
        "started_at": started_at,
    })

    def _worker():
        results = []
        for i, market in enumerate(markets):
            market_progress[i]["status"] = "downloading"
            dl_set(dl_id, progress={
                "markets": [dict(m) for m in market_progress],
                "pct": int(i / len(markets) * 100),
                "elapsed_s": round(time.time() - started_at, 1),
            })

            try:
                # Reuse the synchronous download function
                from unittest.mock import MagicMock
                mock_request = MagicMock()
                mock_request.app.state.store = getattr(request.app.state, "store", None)
                result = download_market_data(mock_request, {
                    "market": market,
                    "resolution_s": resolution_s,
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                    "execution_venues": execution_venues,
                })
                market_progress[i]["status"] = "done"
                total = result.get("total", 0) or result.get("cached", 0)
                market_progress[i]["detail"] = f"{total:,} candles"
                results.append(result)
            except Exception as e:
                market_progress[i]["status"] = "failed"
                market_progress[i]["detail"] = str(e)
                results.append({"market": market, "error": str(e)})

            dl_set(dl_id, progress={
                "markets": [dict(m) for m in market_progress],
                "pct": int((i + 1) / len(markets) * 100),
                "elapsed_s": round(time.time() - started_at, 1),
            })

        dl_set(dl_id, status="complete", result={
            "markets": results,
            "total_markets": len(markets),
            "elapsed_s": round(time.time() - started_at, 1),
        })

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    return {"id": dl_id, "status": "downloading", "markets": markets}


@router.get("/download-async/{dl_id}/status")
def get_download_status(dl_id: str):
    """Poll async download progress."""
    state = dl_get(dl_id)
    if state is None:
        from fastapi import HTTPException
        raise HTTPException(404, "Download not found")

    progress = state["progress"]
    elapsed = time.time() - progress.get("started_at", time.time())
    return {
        "id": dl_id,
        "status": state["status"],
        "progress": {
            "markets": progress.get("markets", []),
            "pct": progress.get("pct", 0),
            "elapsed_s": round(elapsed, 1),
        },
        "results": state["result"],
    }


@router.get("/available-markets")
def available_markets():
    """List all markets available for download from Drift (perp + spot) + CoinGecko."""
    return _list_available_markets()
