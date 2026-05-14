"""Data service — store-backed read APIs + download/provider business logic.

Wraps the OHLCV / funding / borrow / market metadata queries used by
both the FastAPI data routes and the MCP data tools. Returns plain dicts
so callers don't need to know about Candle/FundingRate/BorrowSnapshot
dataclasses.

Also contains download orchestration logic (provider chains, venue volume,
funding aggregation, mark-price enrichment, open-interest fetching) that was
previously inlined in the route layer.
"""
from __future__ import annotations

import threading
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


# ─── Async download state ────────────────────────────────────
_dl_lock = threading.Lock()
_dl_status: Dict[str, str] = {}       # id -> "downloading"|"complete"|"failed"
_dl_progress: Dict[str, dict] = {}    # id -> {markets: [{market, status, detail}], pct, elapsed_s}
_dl_results: Dict[str, dict] = {}     # id -> final aggregate results

_ccxt_warned: set = set()


def dl_set(dl_id: str, *, status: str = None, progress: dict = None, result: dict = None):
    with _dl_lock:
        if status:
            _dl_status[dl_id] = status
        if progress:
            _dl_progress[dl_id] = progress
        if result is not None:
            _dl_results[dl_id] = result


def dl_get(dl_id: str) -> dict:
    """Return download state for a given ID, or None if not found."""
    with _dl_lock:
        status = _dl_status.get(dl_id)
        if status is None:
            return None
        progress = dict(_dl_progress.get(dl_id, {}))
        result = _dl_results.get(dl_id)
    return {"status": status, "progress": progress, "result": result}


def check_single_market(
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

        # Check if local data covers enough of the requested range to run a backtest.
        # Lenient: start within 7 days, end within 7 days, coverage >= 80%.
        # A few missing days at the edges shouldn't block a multi-month backtest.
        covers_range = False
        if candles:
            end_ok = candles[-1].ts >= end_ts - 7 * 86400  # within 7 days of end
            start_ok = candles[0].ts <= start_ts + 7 * 86400  # within 7 days of start
            covers_range = end_ok and start_ok and coverage_pct >= 80

        will_download = not covers_range

        # --- Funding rates ---
        funding_info = {"available": False, "count": 0}
        try:
            fr_count = store.count_funding_rates(market, start_ts, end_ts)
            funding_info = {"available": fr_count > 0, "count": fr_count}
        except Exception:
            pass

        # --- Orderbook snapshots ---
        orderbook_info = {"available": False, "count": 0}
        try:
            ob_count = store.count_orderbook_snapshots(market, start_ts, end_ts)
            orderbook_info = {"available": ob_count > 0, "count": ob_count}
        except Exception:
            pass

        # --- Open interest ---
        oi_info = {"available": False, "count": 0}
        try:
            oi_count = store.count_open_interest(market, start_ts, end_ts)
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


# Venues to download volume from, in priority order.
# Each stores candles under its own venue tag.
_VOLUME_VENUES = [
    ("hyperliquid", "native"),  # native = use HyperliquidCandleProvider directly
    ("jupiter", "dune"),        # dune/helius = Jupiter Perps volume
    ("okx", "ccxt"),
    ("coinbase", "ccxt"),
    ("gate", "ccxt"),
    ("binanceus", "ccxt"),
]


def download_venue_volume(store, market, resolution_s, start_ts, end_ts, logger, warnings):
    """Download candles from multiple venues and store per-venue.

    Like funding rates, volume is stored per-venue so you can compare
    Drift vs Hyperliquid vs OKX volume. Also merges the best available
    volume into zero-volume Pyth candles.

    Returns total number of venue candles stored.
    """
    from ..models import Candle as CandleModel
    total_stored = 0

    for venue_name, provider_type in _VOLUME_VENUES:
        try:
            venue_candles = []

            if provider_type == "native" and venue_name == "hyperliquid":
                from ..providers.hyperliquid_candles import HyperliquidCandleProvider, _FLINT_TO_HL
                if market not in _FLINT_TO_HL:
                    continue
                hl = HyperliquidCandleProvider()
                try:
                    interval_map = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}
                    interval = interval_map.get(resolution_s, "1h")
                    venue_candles = hl.fetch_candles(market, start_ts, end_ts, resolution=interval)
                finally:
                    hl._client.close()

            elif provider_type == "dune" and venue_name == "jupiter":
                import os
                # Try Helius first (faster, more reliable), fall back to Dune
                helius_key = os.environ.get("HELIUS_API_KEY", "")
                if helius_key and "-PERP" in market:
                    from ..providers.jupiter_borrow import HeliusJupiterVolume
                    hv = HeliusJupiterVolume(helius_key)
                    try:
                        venue_candles = hv.fetch_hourly_volume(market, start_ts, end_ts)
                    finally:
                        hv.close()
                elif not helius_key:
                    # Fall back to Dune if available
                    dune_key = os.environ.get("FLINT_DUNE_API_KEY", "")
                    if dune_key and "-PERP" in market:
                        from ..providers.jupiter_borrow import DuneVolumeBackfill
                        dune = DuneVolumeBackfill(dune_key)
                        try:
                            venue_candles = dune.fetch(market, start_ts, end_ts)
                        finally:
                            dune.close()

            elif provider_type == "ccxt":
                from ..providers.ccxt_provider import CCXTProvider
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
        vol_by_ts: dict = {}  # ts -> (volume, venue)
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


def download_pyth_candles(market: str, resolution_s: int, start_ts: int, end_ts: int):
    """Download candles from Pyth Benchmarks API.

    Returns (List[Candle], Optional[error_msg])
    """
    from ..providers.pyth_candles import PythCandleProvider
    provider = PythCandleProvider()
    try:
        candles = provider.fetch_candles(market, resolution_s, start_ts, end_ts)
        return candles, None
    except Exception as e:
        return [], str(e)
    finally:
        provider.close()


def download_range(market: str, resolution_s: int, start_ts: int, end_ts: int, logger):
    """Try all providers in order for a specific time range.

    Returns (candles, error_message) tuple.
    """
    errors = []

    # Try Drift Data API
    try:
        from ..providers.drift_candles import DriftCandleProvider
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
        from ..providers.drift_s3 import DriftS3Provider
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
        from ..providers.coingecko import CoinGeckoProvider
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
        from ..providers.hyperliquid_candles import HyperliquidCandleProvider, _FLINT_TO_HL
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
        from ..providers.ccxt_provider import CCXTProvider
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
                from ..models import Candle
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


def download_range_for_venue(market: str, resolution_s: int, start_ts: int, end_ts: int, venue: str, logger):
    """Download candles from a specific venue.

    Returns (candles, error_message) tuple.
    """
    if venue == "drift":
        # Drift API + S3 chain
        try:
            from ..providers.drift_candles import DriftCandleProvider
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
            from ..providers.drift_s3 import DriftS3Provider
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
            from ..providers.hyperliquid_candles import HyperliquidCandleProvider, _FLINT_TO_HL
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
            from ..providers.ccxt_provider import CCXTProvider
            from ..models import Candle
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


def forward_fill_to_hourly(snapshots: list) -> list:
    """Forward-fill funding snapshots to hourly resolution.

    Venues like OKX/Bybit report every 8h. This fills intermediate hours
    with the previous rate so the DB always has hourly data. Gaps >24h
    are left empty (not filled).
    """
    if not snapshots:
        return snapshots

    from ..providers.funding_rates import FundingSnapshot

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


def fetch_historical_mark_prices(
    market: str, venue: str, start_ts: int, end_ts: int, logger
) -> Dict[int, tuple]:
    """Fetch historical mark price candles for a venue.

    Returns {hourly_ts: (mark_price, index_price)} for joining with funding records.
    Only fetches for venues that have historical mark price APIs.
    """
    import httpx
    prices: Dict[int, tuple] = {}

    if venue == "binance":
        from ..providers.funding_rates import BINANCE_SYMBOLS
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
        from ..providers.funding_rates import OKX_SYMBOLS
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
        from ..providers.funding_rates import BYBIT_SYMBOLS
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
        # Use trade candle close as proxy (mark ~ trade for liquid markets).
        from ..providers.funding_rates import HYPERLIQUID_SYMBOLS
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


def enrich_funding_with_mark_prices(
    snapshots: list, mark_prices: Dict[int, tuple],
) -> list:
    """Replace static mark/index prices with historical per-timestamp prices."""
    from ..providers.funding_rates import FundingSnapshot
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


def fetch_venue_open_interest(
    store, market: str, venue: str, start_ts: int, end_ts: int, logger
) -> int:
    """Fetch historical open interest for a specific venue and store it.

    Uses CCXT fetch_open_interest_history for venues that support it.
    Returns number of records stored.
    """
    from ..providers.funding_rates import (
        BINANCE_SYMBOLS, OKX_SYMBOLS, BYBIT_SYMBOLS, HYPERLIQUID_SYMBOLS,
    )
    from ..models import OpenInterest

    # Map venue to CCXT exchange + symbol
    VENUE_TO_CCXT = {
        "binance": ("binanceusdm", BINANCE_SYMBOLS),
        "okx": ("okx", {k: k.replace("-PERP", "/USDT:USDT") for k in OKX_SYMBOLS}),
        "bybit": ("bybit", {k: k.replace("-PERP", "/USDT:USDT") for k in BYBIT_SYMBOLS}),
    }

    if venue not in VENUE_TO_CCXT:
        # Hyperliquid: use their native API (live snapshot only -- no history)
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
    if "/" not in ccxt_symbol:
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


def download_funding_all_venues(store, market: str, start_ts: int, end_ts: int, logger, venues=None, warnings=None) -> int:
    """Download funding rates for a market from selected venues.

    Args:
        venues: Optional list of venue IDs to download from.
                If None, downloads from all available venues.
        warnings: Optional list to append provider failure messages to.
    """
    if "-PERP" not in market:
        return 0

    from ..providers.funding_rates import (
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
                    mark_prices = fetch_historical_mark_prices(market, venue, start_ts, end_ts, logger)
                    if mark_prices:
                        snapshots = enrich_funding_with_mark_prices(snapshots, mark_prices)

                hourly = forward_fill_to_hourly(snapshots)
                stored = store.upsert_venue_funding(hourly)
                total += stored
                logger.info("%s: %d raw -> %d hourly funding rates for %s",
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
                hourly = forward_fill_to_hourly(snapshots)
                stored = store.upsert_venue_funding(hourly)
                total += stored
                logger.info("ccxt/%s: %d raw -> %d hourly funding rates for %s",
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
            fetch_venue_open_interest(store, market, venue, start_ts, end_ts, logger)
        except Exception as e:
            logger.debug("%s OI fetch failed: %s", venue, e)

    return total


def list_available_markets() -> dict:
    """List all markets available for download from Drift (perp + spot) + CoinGecko."""
    from ..collector.tasks import MARKET_INDEX, SPOT_MARKET_INDEX, SPOT_WITH_CANDLES

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

    # CEX spot markets (via CCXT/OKX) -- real CEX prices for basis computation
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
