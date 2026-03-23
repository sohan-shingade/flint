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
    limit: int = Query(500, le=5000),
):
    import logging as _logging
    _logger = _logging.getLogger("flint.api.data")

    store = _get_store(request)
    if store is None:
        return {"market": market, "count": 0, "rates": []}
    try:
        rates = store.query_funding_rates(market, start_ts, end_ts)

        # Auto-fetch from Drift if no local funding data
        # Note: Drift funding API only has ~30 days of history
        if not rates and "-PERP" in market:
            try:
                import time as _t
                now = int(_t.time())
                _sync_funding_to_candle_range(store, market, now - 90 * 86400, now, _logger)
                rates = store.query_funding_rates(market, start_ts, end_ts)
            except Exception as e:
                _logger.warning("Auto-fetch funding failed for %s: %s", market, e)

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


@router.post("/sync-funding")
def sync_funding(request: Request, body: dict):
    """Sync funding rate data to match candle coverage for a market.

    Body: { "market": "SOL-PERP" }
    """
    import logging
    _logger = logging.getLogger("flint.api.data")

    market = body.get("market", "")
    if not market or "-PERP" not in market:
        return {"market": market, "synced": 0, "error": "Only perp markets have funding rates"}

    store = _get_store(request)
    if store is None:
        return {"market": market, "synced": 0, "error": "Store not available"}

    # Get candle range for this market
    candles = store.query_candles(market, 3600)
    if not candles:
        return {"market": market, "synced": 0, "error": "No candle data — download candles first"}

    start_ts = candles[0].ts
    end_ts = candles[-1].ts

    synced = _sync_funding_to_candle_range(store, market, start_ts, end_ts, _logger)

    return {"market": market, "synced": synced, "candle_range": [start_ts, end_ts]}


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

    if not start_ts or not end_ts or start_ts >= end_ts:
        from fastapi import HTTPException
        raise HTTPException(400, "Invalid date range — start_ts and end_ts required, start < end")

    store = _get_store(request)
    if store is None:
        from fastapi import HTTPException
        raise HTTPException(500, "Store not available")

    # Check what we already have
    existing = store.query_candles(market, resolution_s, start_ts, end_ts)
    existing_count = len(existing)

    # Determine what ranges we still need to download
    # If we have data, only fetch the gaps (before first candle, after last candle)
    gaps = []
    if not existing:
        # No local data at all — download everything
        gaps.append((start_ts, end_ts))
    else:
        first_ts = existing[0].ts
        last_ts = existing[-1].ts
        # Gap at the beginning?
        if first_ts > start_ts + resolution_s:
            gaps.append((start_ts, first_ts))
        # Gap at the end?
        if last_ts < end_ts - resolution_s:
            gaps.append((last_ts, end_ts))

    if not gaps:
        # Candles fully covered — but still check funding coverage
        funding_fetched = 0
        if "-PERP" in market:
            funding_fetched = _sync_funding_to_candle_range(store, market, start_ts, end_ts, logger)
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

    for gap_start, gap_end in gaps:
        fetched = _download_range(market, resolution_s, gap_start, gap_end, logger)
        if fetched:
            total_fetched += len(fetched)
            total_cached += store.upsert_candles(fetched)
            source = fetched[0].market  # will be overwritten below

    # Determine source used
    if total_fetched > 0:
        source = "drift_api"  # default, gets overwritten by _download_range

    # Re-count total
    final_count = len(store.query_candles(market, resolution_s, start_ts, end_ts))

    # Also fetch funding rates for perp markets to match candle coverage
    funding_fetched = 0
    if "-PERP" in market:
        funding_fetched = _sync_funding_to_candle_range(store, market, start_ts, end_ts, logger)

    return {
        "market": market,
        "resolution_s": resolution_s,
        "downloaded": total_fetched,
        "cached": total_cached,
        "existing": existing_count,
        "total": final_count,
        "funding_fetched": funding_fetched,
        "source": source,
    }


def _download_range(market: str, resolution_s: int, start_ts: int, end_ts: int, logger) -> list:
    """Try all providers in order for a specific time range."""
    # Try Drift Data API
    try:
        from ...providers.drift_candles import DriftCandleProvider
        provider = DriftCandleProvider()
        fetched = provider.fetch_candles(market, resolution_s, start_ts, end_ts)
        provider.close()
        if fetched:
            return fetched
    except Exception as e:
        logger.warning("Drift API failed for %s: %s", market, e)

    # Fallback to S3
    try:
        from ...providers.drift_s3 import DriftS3Provider
        provider = DriftS3Provider()
        fetched = provider.fetch_candles(market, resolution_s, start_ts, end_ts)
        provider.close()
        if fetched:
            return fetched
    except Exception as e:
        logger.warning("Drift S3 failed for %s: %s", market, e)

    # Fallback to CoinGecko
    try:
        from ...providers.coingecko import CoinGeckoProvider
        cg = CoinGeckoProvider()
        if cg.resolve_id(market):
            fetched = cg.fetch_candles(market, resolution_s, start_ts, end_ts)
            cg.close()
            if fetched:
                return fetched
    except Exception as e:
        logger.warning("CoinGecko failed for %s: %s", market, e)

    return []


def _sync_funding_to_candle_range(store, market: str, start_ts: int, end_ts: int, logger) -> int:
    """Ensure funding rate data covers the same range as candle data.

    Checks local funding coverage vs the requested candle range and
    fetches missing funding data from Drift to fill gaps.
    """
    from ...collector.tasks import MARKET_INDEX

    market_index = MARKET_INDEX.get(market)
    if market_index is None:
        return 0

    existing_funding = store.query_funding_rates(market, start_ts, end_ts)
    total_fetched = 0

    # Determine funding gaps relative to the candle range
    funding_gaps = []
    if not existing_funding:
        funding_gaps.append((start_ts, end_ts))
    else:
        first_funding = existing_funding[0].ts
        last_funding = existing_funding[-1].ts
        if first_funding > start_ts + 7200:  # gap > 2h at start
            funding_gaps.append((start_ts, first_funding))
        if last_funding < end_ts - 7200:  # gap > 2h at end
            funding_gaps.append((last_funding, end_ts))

    if not funding_gaps:
        return 0

    for gap_start, gap_end in funding_gaps:
        fetched_rates = []

        # Source 1: Drift Data API (recent ~30 days)
        try:
            from ...providers.funding_rates import DriftFundingProvider
            from ...models import FundingRate
            provider = DriftFundingProvider()
            snapshots = provider.fetch_funding(market, gap_start, gap_end)
            provider.close()
            if snapshots:
                fetched_rates = [
                    FundingRate(market=s.market, ts=s.ts, rate=s.rate_hourly,
                                oracle_price=s.index_price, mark_price=s.mark_price, slot=0)
                    for s in snapshots
                ]
        except Exception as e:
            logger.warning("Drift API funding failed for %s: %s", market, e)

        # Source 2: Drift S3 (2022 - Jan 2025, daily CSV files)
        if not fetched_rates:
            try:
                from ...providers.drift_s3 import DriftS3Provider
                s3 = DriftS3Provider()
                fetched_rates = s3.fetch_funding_rates(market, gap_start, gap_end)
                s3.close()
            except Exception as e:
                logger.warning("Drift S3 funding failed for %s: %s", market, e)

        # Source 3: Hyperliquid (1 year history, hourly, use as proxy)
        if not fetched_rates:
            try:
                from ...providers.funding_rates import HyperliquidFundingProvider
                from ...models import FundingRate
                hl = HyperliquidFundingProvider()
                snapshots = hl.fetch_funding(market, gap_start, gap_end)
                hl.close()
                if snapshots:
                    fetched_rates = [
                        FundingRate(market=s.market, ts=s.ts, rate=s.rate_hourly,
                                    oracle_price=s.index_price, mark_price=s.mark_price, slot=0)
                        for s in snapshots
                    ]
                    logger.info("Using Hyperliquid funding as proxy for %s", market)
            except Exception as e:
                logger.warning("Hyperliquid funding failed for %s: %s", market, e)

        if fetched_rates:
            stored = store.upsert_funding_rates(fetched_rates)
            total_fetched += stored
            logger.info("Synced %d funding rates for %s (%d-%d)", stored, market, gap_start, gap_end)

    return total_fetched


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
