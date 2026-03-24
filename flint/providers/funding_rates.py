"""Cross-venue funding rate providers.

Fetches historical funding rates from multiple venues for
cross-venue dislocation analysis. All endpoints are free, no API keys.

Venues:
- Drift: data.api.drift.trade (already integrated)
- Binance Futures: fapi.binance.com (free, public)
- Hyperliquid: api.hyperliquid.xyz (free, public)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger("flint.providers.funding")


@dataclass
class FundingSnapshot:
    """Normalized funding rate from any venue."""
    venue: str
    market: str
    ts: int
    rate_hourly: float  # normalized to 1-hour rate
    mark_price: float
    index_price: float


# ─── Symbol mapping ──────────────────────────────────

# Map Flint market names to exchange symbols
BINANCE_SYMBOLS = {
    "SOL-PERP": "SOLUSDT",
    "BTC-PERP": "BTCUSDT",
    "ETH-PERP": "ETHUSDT",
    "DOGE-PERP": "DOGEUSDT",
    "AVAX-PERP": "AVAXUSDT",
    "LINK-PERP": "LINKUSDT",
    "ARB-PERP": "ARBUSDT",
    "SUI-PERP": "SUIUSDT",
    "XRP-PERP": "XRPUSDT",
    "OP-PERP": "OPUSDT",
    "INJ-PERP": "INJUSDT",
    "TIA-PERP": "TIAUSDT",
    "SEI-PERP": "SEIUSDT",
    "WIF-PERP": "WIFUSDT",
    "JUP-PERP": "JUPUSDT",
    "RENDER-PERP": "RENDERUSDT",
    "BNB-PERP": "BNBUSDT",
}

HYPERLIQUID_SYMBOLS = {
    "SOL-PERP": "SOL",
    "BTC-PERP": "BTC",
    "ETH-PERP": "ETH",
    "DOGE-PERP": "DOGE",
    "AVAX-PERP": "AVAX",
    "LINK-PERP": "LINK",
    "ARB-PERP": "ARB",
    "SUI-PERP": "SUI",
    "XRP-PERP": "XRP",
    "OP-PERP": "OP",
    "INJ-PERP": "INJ",
    "TIA-PERP": "TIA",
    "SEI-PERP": "SEI",
    "WIF-PERP": "WIF",
    "JUP-PERP": "JUP",
    "RENDER-PERP": "RENDER",
    "BNB-PERP": "BNB",
}


OKX_SYMBOLS = {
    "SOL-PERP": "SOL-USDT-SWAP",
    "BTC-PERP": "BTC-USDT-SWAP",
    "ETH-PERP": "ETH-USDT-SWAP",
    "DOGE-PERP": "DOGE-USDT-SWAP",
    "AVAX-PERP": "AVAX-USDT-SWAP",
    "LINK-PERP": "LINK-USDT-SWAP",
    "ARB-PERP": "ARB-USDT-SWAP",
    "SUI-PERP": "SUI-USDT-SWAP",
    "XRP-PERP": "XRP-USDT-SWAP",
    "OP-PERP": "OP-USDT-SWAP",
    "INJ-PERP": "INJ-USDT-SWAP",
    "TIA-PERP": "TIA-USDT-SWAP",
    "SEI-PERP": "SEI-USDT-SWAP",
    "WIF-PERP": "WIF-USDT-SWAP",
    "JUP-PERP": "JUP-USDT-SWAP",
    "RENDER-PERP": "RENDER-USDT-SWAP",
    "BNB-PERP": "BNB-USDT-SWAP",
}

BYBIT_SYMBOLS = {
    "SOL-PERP": "SOLUSDT",
    "BTC-PERP": "BTCUSDT",
    "ETH-PERP": "ETHUSDT",
    "DOGE-PERP": "DOGEUSDT",
    "AVAX-PERP": "AVAXUSDT",
    "LINK-PERP": "LINKUSDT",
    "ARB-PERP": "ARBUSDT",
    "SUI-PERP": "SUIUSDT",
    "XRP-PERP": "XRPUSDT",
    "OP-PERP": "OPUSDT",
    "WIF-PERP": "WIFUSDT",
}


class BinanceFundingProvider:
    """Fetch historical funding rates from Binance Futures (free, no key).

    Note: Binance geo-blocks some regions (US, etc.) with HTTP 451.
    Binance.US does NOT have futures/funding rates.
    """

    # Try global first, no US alternative for futures
    BASE_URL = "https://fapi.binance.com/fapi/v1/fundingRate"

    def __init__(self):
        self._client = httpx.Client(timeout=15)

    def close(self):
        self._client.close()

    def fetch_funding(
        self,
        market: str,
        start_ts: int,
        end_ts: int,
        limit: int = 1000,
    ) -> List[FundingSnapshot]:
        """Fetch Binance funding rates. Binance pays every 8h."""
        symbol = BINANCE_SYMBOLS.get(market)
        if symbol is None:
            return []

        all_rates: List[FundingSnapshot] = []
        cursor = start_ts * 1000  # Binance uses milliseconds

        while cursor < end_ts * 1000:
            try:
                resp = self._client.get(self.BASE_URL, params={
                    "symbol": symbol,
                    "startTime": cursor,
                    "endTime": end_ts * 1000,
                    "limit": limit,
                })
                if resp.status_code != 200:
                    logger.warning("Binance API returned %d", resp.status_code)
                    break

                records = resp.json()
                if not records:
                    break

                for r in records:
                    ts = int(r["fundingTime"]) // 1000
                    rate_8h = float(r["fundingRate"])
                    # Normalize 8h rate to 1h: divide by 8
                    rate_1h = rate_8h / 8

                    all_rates.append(FundingSnapshot(
                        venue="binance",
                        market=market,
                        ts=ts,
                        rate_hourly=rate_1h,
                        mark_price=float(r.get("markPrice", 0)),
                        index_price=0,  # not in this endpoint
                    ))

                last_ts = int(records[-1]["fundingTime"])
                if last_ts <= cursor:
                    break
                cursor = last_ts + 1
                time.sleep(0.1)  # rate limit courtesy

            except Exception as e:
                logger.error("Binance funding error: %s", e)
                break

        return all_rates


class HyperliquidFundingProvider:
    """Fetch historical funding rates from Hyperliquid (free, no key)."""

    BASE_URL = "https://api.hyperliquid.xyz/info"

    def __init__(self):
        self._client = httpx.Client(timeout=15)

    def close(self):
        self._client.close()

    def fetch_funding(
        self,
        market: str,
        start_ts: int,
        end_ts: int,
    ) -> List[FundingSnapshot]:
        """Fetch Hyperliquid funding rates. Hyperliquid pays every 1h.

        Paginates: max 500 per request. Uses last record's time as next startTime.
        """
        symbol = HYPERLIQUID_SYMBOLS.get(market)
        if symbol is None:
            return []

        all_rates: List[FundingSnapshot] = []
        cursor_start = start_ts * 1000

        for _ in range(100):  # max pages
            try:
                resp = self._client.post(self.BASE_URL, json={
                    "type": "fundingHistory",
                    "coin": symbol,
                    "startTime": cursor_start,
                    "endTime": end_ts * 1000,
                })

                if resp.status_code != 200:
                    logger.warning("Hyperliquid API returned %d", resp.status_code)
                    break

                records = resp.json()
                if not isinstance(records, list) or not records:
                    break

                for r in records:
                    ts = int(r.get("time", 0)) // 1000 if isinstance(r.get("time"), int) else 0
                    if ts == 0:
                        try:
                            from datetime import datetime, timezone
                            dt = datetime.fromisoformat(r["time"].replace("Z", "+00:00"))
                            ts = int(dt.timestamp())
                        except Exception:
                            continue

                    if start_ts <= ts <= end_ts:
                        rate = float(r.get("fundingRate", 0))
                        all_rates.append(FundingSnapshot(
                            venue="hyperliquid",
                            market=market,
                            ts=ts,
                            rate_hourly=rate,
                            mark_price=0,
                            index_price=0,
                        ))

                # Paginate
                if len(records) < 500:
                    break
                last_time = max(
                    int(r.get("time", 0)) if isinstance(r.get("time"), int) else 0
                    for r in records
                )
                if last_time <= cursor_start:
                    break
                cursor_start = last_time + 1
                time.sleep(0.2)

            except Exception as e:
                logger.error("Hyperliquid funding error: %s", e)
                break

        all_rates.sort(key=lambda x: x.ts)
        return all_rates


class OKXFundingProvider:
    """Fetch historical funding rates from OKX (free, no key, no geo-block)."""

    BASE_URL = "https://www.okx.com/api/v5/public/funding-rate-history"

    def __init__(self):
        self._client = httpx.Client(timeout=15)

    def close(self):
        self._client.close()

    def fetch_funding(
        self,
        market: str,
        start_ts: int,
        end_ts: int,
    ) -> List[FundingSnapshot]:
        """Fetch OKX funding rates. OKX pays every 8h.

        OKX pagination: 'after' returns records OLDER than the given ID/ts.
        Records are returned newest-first.
        """
        inst_id = OKX_SYMBOLS.get(market)
        if inst_id is None:
            return []

        all_rates: List[FundingSnapshot] = []
        # OKX naming is confusing:
        # 'after' = return records with fundingTime LESS than this (older)
        # 'before' = return records with fundingTime GREATER than this (newer)
        # We want to paginate backwards from end_ts, so use 'after'
        cursor = str(end_ts * 1000 + 1)

        for _ in range(100):
            try:
                params: dict = {"instId": inst_id, "limit": "100", "after": cursor}

                resp = self._client.get(self.BASE_URL, params=params)
                if resp.status_code != 200:
                    logger.warning("OKX API returned %d", resp.status_code)
                    break

                data = resp.json()
                records = data.get("data", [])
                if not records:
                    break

                for r in records:
                    ts = int(r.get("fundingTime", 0)) // 1000
                    rate_8h = float(r.get("realizedRate", r.get("fundingRate", 0)))
                    if start_ts <= ts <= end_ts:
                        all_rates.append(FundingSnapshot(
                            venue="okx",
                            market=market,
                            ts=ts,
                            rate_hourly=rate_8h / 8,
                            mark_price=0,
                            index_price=0,
                        ))

                # Oldest record in this page
                oldest_ft = min(r.get("fundingTime", "0") for r in records)
                oldest_ts_s = int(oldest_ft) // 1000

                # Stop if we've gone past the start
                if oldest_ts_s < start_ts:
                    break

                cursor = oldest_ft
                time.sleep(0.1)

            except Exception as e:
                logger.error("OKX funding error: %s", e)
                break

        all_rates.sort(key=lambda x: x.ts)
        return all_rates


class BybitFundingProvider:
    """Fetch historical funding rates from Bybit (free, no key)."""

    BASE_URL = "https://api.bybit.com/v5/market/funding/history"

    def __init__(self):
        self._client = httpx.Client(timeout=15)

    def close(self):
        self._client.close()

    def fetch_funding(
        self,
        market: str,
        start_ts: int,
        end_ts: int,
    ) -> List[FundingSnapshot]:
        """Fetch Bybit funding rates. Bybit pays every 8h."""
        symbol = BYBIT_SYMBOLS.get(market)
        if symbol is None:
            return []

        all_rates: List[FundingSnapshot] = []
        cursor = ""

        for _ in range(50):
            try:
                params: dict = {
                    "category": "linear",
                    "symbol": symbol,
                    "startTime": str(start_ts * 1000),
                    "endTime": str(end_ts * 1000),
                    "limit": "200",
                }
                if cursor:
                    params["cursor"] = cursor

                resp = self._client.get(self.BASE_URL, params=params)
                if resp.status_code != 200:
                    logger.warning("Bybit API returned %d", resp.status_code)
                    break

                data = resp.json()
                result = data.get("result", {})
                records = result.get("list", [])
                if not records:
                    break

                for r in records:
                    ts = int(r.get("fundingRateTimestamp", 0)) // 1000
                    if ts < start_ts or ts > end_ts:
                        continue
                    rate_8h = float(r.get("fundingRate", 0))
                    all_rates.append(FundingSnapshot(
                        venue="bybit",
                        market=market,
                        ts=ts,
                        rate_hourly=rate_8h / 8,
                        mark_price=0,
                        index_price=0,
                    ))

                next_cursor = result.get("nextPageCursor", "")
                if not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor
                time.sleep(0.1)

            except Exception as e:
                logger.error("Bybit funding error: %s", e)
                break

        all_rates.sort(key=lambda x: x.ts)
        return all_rates


class DriftFundingProvider:
    """Fetch funding rates from Drift Data API with proper pagination."""

    BASE_URL = "https://data.api.drift.trade"

    def __init__(self):
        self._client = httpx.Client(timeout=15)

    def close(self):
        self._client.close()

    def fetch_funding(
        self,
        market: str,
        start_ts: int,
        end_ts: int,
    ) -> List[FundingSnapshot]:
        """Fetch Drift funding rates. Drift pays every 1h.

        Uses cursor-based pagination via meta.nextPage.
        """
        all_rates: List[FundingSnapshot] = []
        page_cursor = None
        url = f"{self.BASE_URL}/market/{market}/fundingRates"

        for _ in range(200):  # max pages
            try:
                params: dict = {"limit": 500}
                if page_cursor:
                    params["page"] = page_cursor

                resp = self._client.get(url, params=params)
                if resp.status_code != 200:
                    logger.warning("Drift funding API returned %d", resp.status_code)
                    break

                data = resp.json()
                if not data.get("success", True):
                    break

                records = data.get("records", [])
                if not records:
                    break

                found_in_range = False
                for r in records:
                    ts = int(r.get("ts", 0))

                    # Records are newest-first; stop if we're past our range
                    if ts < start_ts:
                        found_in_range = True  # we've passed through the range
                        continue
                    if ts > end_ts:
                        continue

                    rate = float(r.get("fundingRate", 0))
                    oracle = float(r.get("oraclePriceTwap", 0))
                    mark = float(r.get("markPriceTwap", 0))

                    # Drift Data API returns human-readable prices (e.g. 70620 for BTC)
                    # fundingRate is in dollar terms — divide by oracle to get fractional rate
                    if oracle > 0:
                        rate_hourly = rate / oracle
                    else:
                        rate_hourly = rate

                    mark_price = mark
                    index_price = oracle

                    all_rates.append(FundingSnapshot(
                        venue="drift",
                        market=market,
                        ts=ts,
                        rate_hourly=rate_hourly,
                        mark_price=mark_price,
                        index_price=index_price,
                    ))
                    found_in_range = True

                # Check if we've gone past our start timestamp
                oldest_ts = min(int(r.get("ts", 0)) for r in records)
                if oldest_ts < start_ts:
                    break

                # Get next page cursor
                meta = data.get("meta", {})
                next_page = meta.get("nextPage")
                if not next_page or next_page == page_cursor:
                    break
                page_cursor = next_page
                time.sleep(0.1)

            except Exception as e:
                logger.error("Drift funding error: %s", e)
                break

        all_rates.sort(key=lambda x: x.ts)
        return all_rates


GATEIO_SYMBOLS = {
    "SOL-PERP": "SOL_USDT", "BTC-PERP": "BTC_USDT", "ETH-PERP": "ETH_USDT",
    "DOGE-PERP": "DOGE_USDT", "AVAX-PERP": "AVAX_USDT", "LINK-PERP": "LINK_USDT",
    "ARB-PERP": "ARB_USDT", "SUI-PERP": "SUI_USDT", "XRP-PERP": "XRP_USDT",
    "OP-PERP": "OP_USDT", "INJ-PERP": "INJ_USDT", "TIA-PERP": "TIA_USDT",
    "SEI-PERP": "SEI_USDT", "WIF-PERP": "WIF_USDT", "JUP-PERP": "JUP_USDT",
    "RENDER-PERP": "RENDER_USDT", "BNB-PERP": "BNB_USDT",
}

BITGET_SYMBOLS = {
    "SOL-PERP": "SOLUSDT", "BTC-PERP": "BTCUSDT", "ETH-PERP": "ETHUSDT",
    "DOGE-PERP": "DOGEUSDT", "AVAX-PERP": "AVAXUSDT", "LINK-PERP": "LINKUSDT",
    "ARB-PERP": "ARBUSDT", "SUI-PERP": "SUIUSDT", "XRP-PERP": "XRPUSDT",
    "OP-PERP": "OPUSDT", "INJ-PERP": "INJUSDT", "TIA-PERP": "TIAUSDT",
    "SEI-PERP": "SEIUSDT", "WIF-PERP": "WIFUSDT", "JUP-PERP": "JUPUSDT",
    "RENDER-PERP": "RENDERUSDT", "BNB-PERP": "BNBUSDT",
}

DYDX_SYMBOLS = {
    "SOL-PERP": "SOL-USD", "BTC-PERP": "BTC-USD", "ETH-PERP": "ETH-USD",
    "DOGE-PERP": "DOGE-USD", "AVAX-PERP": "AVAX-USD", "LINK-PERP": "LINK-USD",
    "ARB-PERP": "ARB-USD", "SUI-PERP": "SUI-USD", "XRP-PERP": "XRP-USD",
    "OP-PERP": "OP-USD", "INJ-PERP": "INJ-USD", "TIA-PERP": "TIA-USD",
    "SEI-PERP": "SEI-USD", "WIF-PERP": "WIF-USD",
}


class GateioFundingProvider:
    """Fetch historical funding rates from Gate.io (free, no key, no geo-block)."""

    BASE_URL = "https://api.gateio.ws/api/v4/futures/usdt/funding_rate"

    def __init__(self):
        self._client = httpx.Client(timeout=15)

    def close(self):
        self._client.close()

    def fetch_funding(
        self, market: str, start_ts: int, end_ts: int,
    ) -> List[FundingSnapshot]:
        """Fetch Gate.io funding rates. Gate.io pays every 8h."""
        contract = GATEIO_SYMBOLS.get(market)
        if contract is None:
            return []

        all_rates: List[FundingSnapshot] = []

        for _ in range(50):
            try:
                params: dict = {
                    "contract": contract, "limit": 1000,
                    "from": start_ts, "to": end_ts,
                }
                resp = self._client.get(self.BASE_URL, params=params)
                if resp.status_code != 200:
                    logger.warning("Gate.io API returned %d", resp.status_code)
                    break

                records = resp.json()
                if not records:
                    break

                for r in records:
                    ts = int(r.get("t", 0))
                    rate_8h = float(r.get("r", 0))
                    if start_ts <= ts <= end_ts:
                        all_rates.append(FundingSnapshot(
                            venue="gateio", market=market, ts=ts,
                            rate_hourly=rate_8h / 8, mark_price=0, index_price=0,
                        ))

                # Gate.io returns newest-first; stop if oldest is before start
                oldest = min(int(r.get("t", 0)) for r in records)
                if oldest <= start_ts or len(records) < 1000:
                    break
                end_ts = oldest - 1
                time.sleep(0.1)

            except Exception as e:
                logger.error("Gate.io funding error: %s", e)
                break

        all_rates.sort(key=lambda x: x.ts)
        return all_rates


class BitgetFundingProvider:
    """Fetch historical funding rates from Bitget (free, no key, no geo-block)."""

    BASE_URL = "https://api.bitget.com/api/v2/mix/market/history-fund-rate"

    def __init__(self):
        self._client = httpx.Client(timeout=15)

    def close(self):
        self._client.close()

    def fetch_funding(
        self, market: str, start_ts: int, end_ts: int,
    ) -> List[FundingSnapshot]:
        """Fetch Bitget funding rates. Bitget pays every 8h."""
        symbol = BITGET_SYMBOLS.get(market)
        if symbol is None:
            return []

        all_rates: List[FundingSnapshot] = []
        page = 1

        for _ in range(50):
            try:
                params: dict = {
                    "symbol": symbol, "productType": "USDT-FUTURES",
                    "pageSize": 100, "pageNo": page,
                }
                resp = self._client.get(self.BASE_URL, params=params)
                if resp.status_code != 200:
                    logger.warning("Bitget API returned %d", resp.status_code)
                    break

                data = resp.json()
                records = data.get("data", [])
                if not records:
                    break

                for r in records:
                    ts = int(r.get("fundingTime", 0)) // 1000
                    if ts < start_ts or ts > end_ts:
                        continue
                    rate_8h = float(r.get("fundingRate", 0))
                    all_rates.append(FundingSnapshot(
                        venue="bitget", market=market, ts=ts,
                        rate_hourly=rate_8h / 8, mark_price=0, index_price=0,
                    ))

                # Check if we've gone past our range
                oldest = min(int(r.get("fundingTime", 0)) // 1000 for r in records)
                if oldest < start_ts or len(records) < 100:
                    break
                page += 1
                time.sleep(0.1)

            except Exception as e:
                logger.error("Bitget funding error: %s", e)
                break

        all_rates.sort(key=lambda x: x.ts)
        return all_rates


class DydxFundingProvider:
    """Fetch historical funding rates from dYdX v4 (free, no key, no geo-block)."""

    BASE_URL = "https://indexer.dydx.trade/v4/historicalFunding"

    def __init__(self):
        self._client = httpx.Client(timeout=15)

    def close(self):
        self._client.close()

    def fetch_funding(
        self, market: str, start_ts: int, end_ts: int,
    ) -> List[FundingSnapshot]:
        """Fetch dYdX funding rates. dYdX pays every 1h."""
        ticker = DYDX_SYMBOLS.get(market)
        if ticker is None:
            return []

        all_rates: List[FundingSnapshot] = []
        url = f"{self.BASE_URL}/{ticker}"
        # dYdX uses effectiveBeforeOrAt for pagination (ISO string)
        cursor_time = None

        for _ in range(100):
            try:
                params: dict = {"limit": 500}
                if cursor_time:
                    params["effectiveBeforeOrAt"] = cursor_time

                resp = self._client.get(url, params=params)
                if resp.status_code != 200:
                    logger.warning("dYdX API returned %d", resp.status_code)
                    break

                data = resp.json()
                records = data.get("historicalFunding", [])
                if not records:
                    break

                from datetime import datetime, timezone
                found_before_start = False
                for r in records:
                    try:
                        dt = datetime.fromisoformat(r["effectiveAt"].replace("Z", "+00:00"))
                        ts = int(dt.timestamp())
                    except Exception:
                        continue
                    if ts < start_ts:
                        found_before_start = True
                        continue
                    if ts > end_ts:
                        continue
                    rate = float(r.get("rate", 0))
                    price = float(r.get("price", 0))
                    all_rates.append(FundingSnapshot(
                        venue="dydx", market=market, ts=ts,
                        rate_hourly=rate, mark_price=price, index_price=price,
                    ))

                if found_before_start or len(records) < 500:
                    break

                # Paginate: use oldest record's time
                cursor_time = records[-1]["effectiveAt"]
                time.sleep(0.1)

            except Exception as e:
                logger.error("dYdX funding error: %s", e)
                break

        all_rates.sort(key=lambda x: x.ts)
        return all_rates


class CCXTFundingProvider:
    """Fetch funding rates from any exchange via CCXT.

    Usage:
        provider = CCXTFundingProvider("mexc")
        rates = provider.fetch_funding("SOL-PERP", start_ts, end_ts)
    """

    # Map Flint market names to CCXT unified symbols
    CCXT_SYMBOLS = {
        "SOL-PERP": "SOL/USDT:USDT", "BTC-PERP": "BTC/USDT:USDT",
        "ETH-PERP": "ETH/USDT:USDT", "DOGE-PERP": "DOGE/USDT:USDT",
        "AVAX-PERP": "AVAX/USDT:USDT", "LINK-PERP": "LINK/USDT:USDT",
        "ARB-PERP": "ARB/USDT:USDT", "SUI-PERP": "SUI/USDT:USDT",
        "XRP-PERP": "XRP/USDT:USDT", "OP-PERP": "OP/USDT:USDT",
        "INJ-PERP": "INJ/USDT:USDT", "WIF-PERP": "WIF/USDT:USDT",
        "JUP-PERP": "JUP/USDT:USDT", "BNB-PERP": "BNB/USDT:USDT",
        "RENDER-PERP": "RENDER/USDT:USDT", "TIA-PERP": "TIA/USDT:USDT",
        "SEI-PERP": "SEI/USDT:USDT",
    }

    def __init__(self, exchange: str = "mexc"):
        self._exchange_name = exchange
        self._exchange = None

    def _get_exchange(self):
        if self._exchange is None:
            try:
                import ccxt as _ccxt
                cls = getattr(_ccxt, self._exchange_name, None)
                if cls is None:
                    raise ValueError(f"Unknown exchange: {self._exchange_name}")
                self._exchange = cls({"enableRateLimit": True})
            except ImportError:
                raise ImportError("ccxt not installed — run: pip install ccxt")
        return self._exchange

    def close(self):
        self._exchange = None

    def fetch_funding(
        self, market: str, start_ts: int, end_ts: int,
    ) -> List[FundingSnapshot]:
        symbol = self.CCXT_SYMBOLS.get(market)
        if symbol is None:
            return []

        ex = self._get_exchange()
        all_rates: List[FundingSnapshot] = []
        cursor = start_ts * 1000  # CCXT uses ms

        for _ in range(100):
            try:
                rates = ex.fetch_funding_rate_history(symbol, since=cursor, limit=500)
                if not rates:
                    break

                for r in rates:
                    ts = r.get("timestamp", 0) // 1000
                    if ts < start_ts or ts > end_ts:
                        continue
                    rate = float(r.get("fundingRate", 0))
                    # Most exchanges use 8h funding, normalize to 1h
                    rate_1h = rate / 8
                    all_rates.append(FundingSnapshot(
                        venue=self._exchange_name, market=market, ts=ts,
                        rate_hourly=rate_1h, mark_price=0, index_price=0,
                    ))

                last_ts = rates[-1].get("timestamp", 0)
                if last_ts <= cursor or last_ts // 1000 > end_ts:
                    break
                cursor = last_ts + 1
                time.sleep(0.2)

            except Exception as e:
                logger.error("CCXT %s funding error: %s", self._exchange_name, e)
                break

        all_rates.sort(key=lambda x: x.ts)
        return all_rates


# Default CCXT exchanges to include in funding downloads
CCXT_FUNDING_EXCHANGES = ["mexc", "phemex", "bitmex"]


# ─── Cross-venue aggregator ──────────────────────────

class CrossVenueFunding:
    """Aggregate funding rates from multiple venues.

    Computes:
    - Per-venue normalized hourly funding
    - Cross-venue benchmark (weighted average)
    - Dislocation score per venue
    """

    def __init__(self):
        self.providers = {
            "drift": DriftFundingProvider(),
            "binance": BinanceFundingProvider(),
            "hyperliquid": HyperliquidFundingProvider(),
            "okx": OKXFundingProvider(),
            "bybit": BybitFundingProvider(),
        }

    def close(self):
        for p in self.providers.values():
            p.close()

    def fetch_all(
        self,
        market: str,
        start_ts: int,
        end_ts: int,
        venues: Optional[List[str]] = None,
    ) -> Dict[str, List[FundingSnapshot]]:
        """Fetch funding from all (or selected) venues.

        Args:
            venues: Optional list of venue names to query. Default: all.
        """
        result = {}
        targets = venues or list(self.providers.keys())

        for venue in targets:
            provider = self.providers.get(venue)
            if provider is None:
                continue
            try:
                rates = provider.fetch_funding(market, start_ts, end_ts)
                if rates:
                    result[venue] = rates
                    logger.info("%s: %d funding snapshots", venue, len(rates))
            except Exception as e:
                logger.warning("%s funding fetch failed: %s", venue, e)

        # Log summary
        total = sum(len(v) for v in result.values())
        logger.info("Cross-venue: %d venues, %d total snapshots for %s", len(result), total, market)

        # Also fetch Hyperliquid (always works, ensure it's included)
        if "hyperliquid" not in result and "hyperliquid" in targets:
            # Already tried above
            pass

        return result

    def compute_benchmark(
        self,
        all_rates: Dict[str, List[FundingSnapshot]],
        method: str = "equal",
    ) -> Dict[int, float]:
        """Compute cross-venue benchmark funding rate per timestamp.

        Returns {ts: benchmark_rate} using equal-weight average.
        """
        # Collect all rates indexed by nearest hour
        by_hour: Dict[int, List[float]] = {}
        for venue, snapshots in all_rates.items():
            for s in snapshots:
                hour_ts = (s.ts // 3600) * 3600
                by_hour.setdefault(hour_ts, []).append(s.rate_hourly)

        benchmark = {}
        for ts, rates in by_hour.items():
            benchmark[ts] = sum(rates) / len(rates)

        return benchmark

    def compute_dislocation(
        self,
        venue_rates: List[FundingSnapshot],
        benchmark: Dict[int, float],
    ) -> Dict[int, float]:
        """Compute funding dislocation: venue rate minus benchmark.

        Positive = venue funding is above benchmark (venue is rich).
        """
        dislocation = {}
        for s in venue_rates:
            hour_ts = (s.ts // 3600) * 3600
            bench = benchmark.get(hour_ts)
            if bench is not None:
                dislocation[hour_ts] = s.rate_hourly - bench
        return dislocation
