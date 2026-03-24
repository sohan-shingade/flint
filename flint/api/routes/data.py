"""Data query API — candles, funding rates, OI, liquidations, whale transfers,
DEX volume, freshness, correlation.  Thread-safe via shared store."""
from __future__ import annotations

import time
from typing import List, Optional

from fastapi import APIRouter, Query, Request

from ...store import FlintStore
from ...providers import list_providers, get_provider_class
from ...analytics.correlation import compute_correlation_matrix

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
):
    """Get funding rates for a market, grouped by venue."""
    store = _get_store(request)
    if store is None:
        return {"market": market, "venues": {}, "count": 0}
    try:
        by_venue = store.query_funding_by_venue(market, start_ts, end_ts)
        total = sum(len(v) for v in by_venue.values())
        return {"market": market, "venues": by_venue, "count": total}
    except Exception as e:
        return {"market": market, "venues": {}, "count": 0, "error": str(e)}


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
    """Download market data from Drift for a specific market and date range.

    Body: { "market": "SOL-PERP", "resolution_s": 3600, "start_ts": ..., "end_ts": ... }
    Returns: { "market": ..., "downloaded": N, "cached": N, "source": "drift_api" | "drift_s3" }
    """
    import logging
    logger = logging.getLogger("flint.api.data")

    market = body.get("market", "SOL-PERP")
    resolution_s = body.get("resolution_s", 3600)
    start_ts = body.get("start_ts")
    end_ts = body.get("end_ts")
    funding_venues = body.get("funding_venues")  # optional list of venue IDs

    if not start_ts or not end_ts or start_ts >= end_ts:
        from fastapi import HTTPException
        raise HTTPException(400, "Invalid date range — start_ts and end_ts required, start < end")

    store = _get_store(request)
    if store is None:
        from fastapi import HTTPException
        raise HTTPException(500, "Store not available")

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
                    funding_fetched = _download_funding_all_venues(store, market, start_ts, end_ts, logger, venues=funding_venues)
                except Exception as e:
                    logger.warning("Funding sync failed for %s: %s", market, e)
            return {
                "market": market,
                "resolution_s": resolution_s,
                "downloaded": 0,
                "cached": 0,
                "existing": existing_count,
                "total": existing_count,
                "funding_fetched": funding_fetched,
                "source": "local",
                "skipped": True,
            }

        # Download only the missing gaps
        total_fetched = 0
        total_cached = 0
        source = "none"
        errors: list = []

        for gap_start, gap_end in gaps:
            fetched, err = _download_range(market, resolution_s, gap_start, gap_end, logger)
            if fetched:
                total_fetched += len(fetched)
                total_cached += store.upsert_candles(fetched)
                source = "drift_api"
            if err:
                errors.append(err)

        # Re-count total
        final_count = len(store.query_candles(market, resolution_s, start_ts, end_ts))

        # Also fetch funding rates for perp markets
        funding_fetched = 0
        if "-PERP" in market:
            try:
                funding_fetched = _download_funding_all_venues(store, market, start_ts, end_ts, logger)
            except Exception as e:
                logger.warning("Funding sync failed for %s: %s", market, e)

        result = {
            "market": market,
            "resolution_s": resolution_s,
            "downloaded": total_fetched,
            "cached": total_cached,
            "existing": existing_count,
            "total": final_count,
            "funding_fetched": funding_fetched,
            "source": source,
        }
        if errors:
            result["error"] = "; ".join(errors)
        return result

    except Exception as e:
        logger.error("Download failed for %s: %s", market, e)
        return {
            "market": market,
            "resolution_s": resolution_s,
            "downloaded": 0,
            "cached": 0,
            "existing": 0,
            "total": 0,
            "error": str(e),
        }


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

    return [], "; ".join(errors) if errors else "No provider found"


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


def _download_funding_all_venues(store, market: str, start_ts: int, end_ts: int, logger, venues=None) -> int:
    """Download funding rates for a market from selected venues.

    Args:
        venues: Optional list of venue IDs to download from.
                If None, downloads from all available venues.
    """
    if "-PERP" not in market:
        return 0

    from ...providers.funding_rates import (
        DriftFundingProvider, HyperliquidFundingProvider,
        OKXFundingProvider, BybitFundingProvider,
        GateioFundingProvider, BitgetFundingProvider, DydxFundingProvider,
        CCXTFundingProvider, CCXT_FUNDING_EXCHANGES,
    )

    native_providers: dict = {
        "drift": DriftFundingProvider,
        "hyperliquid": HyperliquidFundingProvider,
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
                hourly = _forward_fill_to_hourly(snapshots)
                stored = store.upsert_venue_funding(hourly)
                total += stored
                logger.info("%s: %d raw → %d hourly funding rates for %s",
                            venue, len(snapshots), stored, market)
        except Exception as e:
            logger.warning("%s funding failed for %s: %s", venue, market, e)

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

    return {"markets": markets}
