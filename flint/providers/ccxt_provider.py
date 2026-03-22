"""CCXT provider — unified access to 100+ crypto exchanges.

Supports any exchange that CCXT supports: Binance, Bybit, OKX, Coinbase, Kraken, etc.
Optional: pip install ccxt (not required for core Flint functionality).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from ..models import Candle, FundingRate
from .registry import DataProvider
from .ccxt_markets import CCXTMarketMapper

logger = logging.getLogger(__name__)

# Lazy-loaded ccxt module
_ccxt = None


def _get_ccxt():
    """Lazy-import ccxt to allow graceful degradation when not installed."""
    global _ccxt
    if _ccxt is None:
        try:
            import ccxt as _ccxt_mod
            _ccxt = _ccxt_mod
        except ImportError:
            raise ImportError(
                "ccxt is not installed. Install it with: pip install 'flint[ccxt]' "
                "or: pip install ccxt>=4.0"
            )
    return _ccxt


def _is_ccxt_available() -> bool:
    """Check if ccxt can be imported without raising."""
    try:
        _get_ccxt()
        return True
    except ImportError:
        return False


# ── Resolution mapping ───────────────────────────────────────────

# Map resolution in seconds to CCXT timeframe strings
_TIMEFRAMES: Dict[int, str] = {
    60: "1m",
    180: "3m",
    300: "5m",
    900: "15m",
    1800: "30m",
    3600: "1h",
    7200: "2h",
    14400: "4h",
    21600: "6h",
    28800: "8h",
    43200: "12h",
    86400: "1d",
    259200: "3d",
    604800: "1w",
    2592000: "1M",
}

# Common exchanges with good CCXT support
COMMON_EXCHANGES: List[str] = [
    "binance",
    "bybit",
    "okx",
    "coinbase",
    "kraken",
    "kucoin",
    "gate",
    "bitget",
    "mexc",
    "htx",
]


def _resolution_to_timeframe(resolution_s: int) -> str:
    """Convert resolution in seconds to a CCXT timeframe string.

    Falls back to the nearest supported timeframe if exact match is not found.
    """
    if resolution_s in _TIMEFRAMES:
        return _TIMEFRAMES[resolution_s]

    # Find closest supported resolution
    closest = min(_TIMEFRAMES.keys(), key=lambda k: abs(k - resolution_s))
    logger.warning(
        "Resolution %ds not directly supported by CCXT, using %s (%ds)",
        resolution_s,
        _TIMEFRAMES[closest],
        closest,
    )
    return _TIMEFRAMES[closest]


def _timeframe_to_resolution(timeframe: str) -> int:
    """Convert a CCXT timeframe string back to seconds."""
    reverse = {v: k for k, v in _TIMEFRAMES.items()}
    return reverse.get(timeframe, 3600)


class CCXTProvider(DataProvider):
    """Fetch market data from any CCXT-supported exchange.

    Usage:
        provider = CCXTProvider(exchange="binance")
        candles = provider.fetch_candles("SOL/USDT", 3600, start_ts, end_ts)
        funding = provider.fetch_funding_rates("SOL-PERP", start_ts, end_ts)
        book = provider.fetch_orderbook("SOL/USDT", limit=10)
        ticker = provider.fetch_ticker("SOL/USDT")
        markets = provider.list_markets(quote="USDT")

    The exchange instance is created lazily on first use.
    """

    name = "ccxt"
    requires_api_key = False  # Public data doesn't need keys

    def __init__(
        self,
        exchange: str = "binance",
        api_key: str = "",
        secret: str = "",
        password: str = "",
        sandbox: bool = False,
        extra_config: Optional[dict] = None,
    ) -> None:
        """Initialize the CCXT provider.

        Args:
            exchange: Exchange name (e.g. "binance", "bybit", "okx").
            api_key: API key (optional for public endpoints).
            secret: API secret (optional for public endpoints).
            password: API password/passphrase (some exchanges require this).
            sandbox: Use testnet/sandbox mode.
            extra_config: Additional ccxt config dict to pass to the exchange.
        """
        self._exchange_name = exchange.lower()
        self._api_key = api_key
        self._secret = secret
        self._password = password
        self._sandbox = sandbox
        self._extra_config = extra_config or {}
        self._exchange = None  # Created lazily
        self._mapper = CCXTMarketMapper()

    def _get_exchange(self):
        """Lazily create the ccxt exchange instance."""
        if self._exchange is not None:
            return self._exchange

        ccxt = _get_ccxt()

        # Validate exchange name
        if self._exchange_name not in ccxt.exchanges:
            available = ", ".join(COMMON_EXCHANGES)
            raise ValueError(
                f"Unknown exchange '{self._exchange_name}'. "
                f"Common exchanges: {available}. "
                f"Full list: ccxt.exchanges ({len(ccxt.exchanges)} total)"
            )

        # Build config
        config: Dict[str, Any] = {
            "enableRateLimit": True,
        }
        if self._api_key:
            config["apiKey"] = self._api_key
        if self._secret:
            config["secret"] = self._secret
        if self._password:
            config["password"] = self._password
        config.update(self._extra_config)

        # Create exchange instance
        exchange_class = getattr(ccxt, self._exchange_name)
        self._exchange = exchange_class(config)

        if self._sandbox:
            self._exchange.set_sandbox_mode(True)

        # Pass to mapper for symbol lookups
        self._mapper = CCXTMarketMapper(self._exchange)

        return self._exchange

    def is_available(self) -> bool:
        """Check if ccxt is installed and the exchange is valid."""
        if not _is_ccxt_available():
            return False
        try:
            ccxt = _get_ccxt()
            return self._exchange_name in ccxt.exchanges
        except Exception:
            return False

    def supported_data_types(self) -> List[str]:
        return ["candles", "funding", "orderbook", "ticker"]

    # ── Candles ───────────────────────────────────────────────

    def fetch_candles(
        self,
        symbol: str,
        resolution_s: int,
        start_ts: int,
        end_ts: int,
    ) -> List[Candle]:
        """Fetch OHLCV candles from the exchange.

        Args:
            symbol: Trading pair in CCXT format ("SOL/USDT") or Flint format
                    ("SOL-PERP"). Automatically converted as needed.
            resolution_s: Candle width in seconds.
            start_ts: Start unix timestamp (seconds).
            end_ts: End unix timestamp (seconds).

        Returns:
            Sorted, deduplicated list of Candle objects.
        """
        exchange = self._get_exchange()
        timeframe = _resolution_to_timeframe(resolution_s)

        # Convert Flint symbol to CCXT if needed
        ccxt_symbol = self._mapper.to_ccxt_symbol(symbol, self._exchange_name)
        # Determine the Flint market name for the Candle objects
        flint_market = self._mapper.from_ccxt_symbol(ccxt_symbol, self._exchange_name)

        # Use the actual resolution from the timeframe we're using
        actual_resolution = _timeframe_to_resolution(timeframe)

        candles: List[Candle] = []
        since_ms = start_ts * 1000
        end_ms = end_ts * 1000

        # CCXT fetch_ohlcv returns: [[timestamp_ms, open, high, low, close, volume], ...]
        # Most exchanges return max 500-1500 candles per request
        limit = 500

        while since_ms < end_ms:
            try:
                ohlcv = exchange.fetch_ohlcv(
                    ccxt_symbol,
                    timeframe=timeframe,
                    since=since_ms,
                    limit=limit,
                )
            except Exception as e:
                logger.error(
                    "CCXT fetch_ohlcv error (%s %s): %s",
                    self._exchange_name, ccxt_symbol, e,
                )
                break

            if not ohlcv:
                break

            for bar in ohlcv:
                ts_ms = bar[0]
                ts_s = ts_ms // 1000

                if ts_s < start_ts or ts_s > end_ts:
                    continue

                candles.append(Candle(
                    market=flint_market,
                    resolution_s=actual_resolution,
                    ts=ts_s,
                    open=float(bar[1]),
                    high=float(bar[2]),
                    low=float(bar[3]),
                    close=float(bar[4]),
                    volume=float(bar[5]) if bar[5] is not None else 0.0,
                ))

            # Advance cursor past the last candle
            last_ts_ms = ohlcv[-1][0]
            if last_ts_ms <= since_ms:
                break  # No progress, avoid infinite loop
            since_ms = last_ts_ms + 1

            # Respect rate limits (ccxt handles this, but add a small buffer)
            time.sleep(0.05)

        # Deduplicate by timestamp and sort
        seen: set = set()
        unique: List[Candle] = []
        for c in sorted(candles, key=lambda c: c.ts):
            if c.ts not in seen:
                seen.add(c.ts)
                unique.append(c)
        return unique

    # ── Funding Rates ────────────────────────────────────────

    def fetch_funding_rates(
        self,
        symbol: str,
        start_ts: int,
        end_ts: int,
    ) -> List[FundingRate]:
        """Fetch historical funding rate data for perpetual contracts.

        Args:
            symbol: Trading pair in Flint ("SOL-PERP") or CCXT ("SOL/USDT:USDT") format.
            start_ts: Start unix timestamp (seconds).
            end_ts: End unix timestamp (seconds).

        Returns:
            List of FundingRate objects sorted by timestamp.
        """
        exchange = self._get_exchange()

        # Ensure we're using a perp symbol
        ccxt_symbol = self._mapper.to_ccxt_symbol(symbol, self._exchange_name)
        flint_market = self._mapper.from_ccxt_symbol(ccxt_symbol, self._exchange_name)

        rates: List[FundingRate] = []
        since_ms = start_ts * 1000

        while since_ms < end_ts * 1000:
            try:
                history = exchange.fetch_funding_rate_history(
                    ccxt_symbol,
                    since=since_ms,
                    limit=500,
                )
            except Exception as e:
                logger.error(
                    "CCXT fetch_funding_rate_history error (%s %s): %s",
                    self._exchange_name, ccxt_symbol, e,
                )
                break

            if not history:
                break

            for entry in history:
                ts_ms = entry.get("timestamp", 0)
                ts_s = ts_ms // 1000 if ts_ms > 1e12 else ts_ms

                if ts_s < start_ts or ts_s > end_ts:
                    continue

                funding_rate = float(entry.get("fundingRate", 0) or 0)
                mark = float(entry.get("markPrice", 0) or 0)
                index = float(entry.get("indexPrice", 0) or 0)

                # Determine funding interval for normalization.
                # Most exchanges use 8h, Hyperliquid and Drift use 1h.
                info = entry.get("info", {})
                interval_h = 8  # default: 8-hour funding
                if self._exchange_name in ("hyperliquid",):
                    interval_h = 1

                # Normalize to hourly rate
                rate_hourly = funding_rate / interval_h if interval_h > 1 else funding_rate

                rates.append(FundingRate(
                    market=flint_market,
                    ts=int(ts_s),
                    rate=rate_hourly,
                    oracle_price=index,
                    mark_price=mark,
                    slot=0,
                ))

            # Advance cursor
            last_ts_ms = history[-1].get("timestamp", 0)
            if last_ts_ms <= since_ms:
                break
            since_ms = last_ts_ms + 1

            time.sleep(0.1)

        rates.sort(key=lambda r: r.ts)
        return rates

    # ── Order Book ───────────────────────────────────────────

    def fetch_orderbook(self, symbol: str, limit: int = 10) -> dict:
        """Fetch the current order book snapshot.

        Args:
            symbol: Trading pair in CCXT or Flint format.
            limit: Number of levels per side.

        Returns:
            Dict with keys: bids, asks, timestamp, symbol.
            Each side is a list of [price, size] pairs.
        """
        exchange = self._get_exchange()
        ccxt_symbol = self._mapper.to_ccxt_symbol(symbol, self._exchange_name)

        try:
            book = exchange.fetch_order_book(ccxt_symbol, limit=limit)
            return {
                "symbol": ccxt_symbol,
                "flint_symbol": self._mapper.from_ccxt_symbol(ccxt_symbol, self._exchange_name),
                "timestamp": book.get("timestamp"),
                "bids": book.get("bids", [])[:limit],
                "asks": book.get("asks", [])[:limit],
                "bid_count": len(book.get("bids", [])),
                "ask_count": len(book.get("asks", [])),
            }
        except Exception as e:
            logger.error(
                "CCXT fetch_order_book error (%s %s): %s",
                self._exchange_name, ccxt_symbol, e,
            )
            return {
                "symbol": ccxt_symbol,
                "flint_symbol": symbol,
                "timestamp": None,
                "bids": [],
                "asks": [],
                "error": str(e),
            }

    # ── Ticker ───────────────────────────────────────────────

    def fetch_ticker(self, symbol: str) -> dict:
        """Fetch the current ticker (price, volume, 24h change, etc.).

        Args:
            symbol: Trading pair in CCXT or Flint format.

        Returns:
            Dict with ticker data including: last, bid, ask, high, low,
            volume, change, percentage, vwap, etc.
        """
        exchange = self._get_exchange()
        ccxt_symbol = self._mapper.to_ccxt_symbol(symbol, self._exchange_name)

        try:
            ticker = exchange.fetch_ticker(ccxt_symbol)
            return {
                "symbol": ccxt_symbol,
                "flint_symbol": self._mapper.from_ccxt_symbol(ccxt_symbol, self._exchange_name),
                "last": ticker.get("last"),
                "bid": ticker.get("bid"),
                "ask": ticker.get("ask"),
                "high": ticker.get("high"),
                "low": ticker.get("low"),
                "volume": ticker.get("baseVolume"),
                "quote_volume": ticker.get("quoteVolume"),
                "change": ticker.get("change"),
                "percentage": ticker.get("percentage"),
                "vwap": ticker.get("vwap"),
                "timestamp": ticker.get("timestamp"),
            }
        except Exception as e:
            logger.error(
                "CCXT fetch_ticker error (%s %s): %s",
                self._exchange_name, ccxt_symbol, e,
            )
            return {
                "symbol": ccxt_symbol,
                "flint_symbol": symbol,
                "error": str(e),
            }

    # ── Market listing ───────────────────────────────────────

    def list_markets(self, quote: str = "USDT") -> List[dict]:
        """List available markets on the exchange.

        Args:
            quote: Filter by quote currency ("USDT", "USD", "BTC", etc.).
                   Pass "" or None to list all markets.

        Returns:
            List of dicts with keys: symbol, base, quote, type, active.
        """
        exchange = self._get_exchange()

        try:
            if not exchange.markets:
                exchange.load_markets()
        except Exception as e:
            logger.error("Failed to load markets from %s: %s", self._exchange_name, e)
            return []

        results = []
        for sym, market in exchange.markets.items():
            mquote = market.get("quote", "")
            if quote and mquote.upper() != quote.upper():
                continue

            results.append({
                "symbol": sym,
                "base": market.get("base", ""),
                "quote": mquote,
                "type": market.get("type", ""),
                "active": market.get("active", True),
                "flint_symbol": self._mapper.from_ccxt_symbol(
                    sym, self._exchange_name
                ),
            })

        results.sort(key=lambda m: (m["type"], m["base"]))
        return results

    # ── Static helpers ───────────────────────────────────────

    @staticmethod
    def list_exchanges() -> List[str]:
        """Return a curated list of common, well-supported exchanges.

        For the full list of 100+ supported exchanges, use ccxt.exchanges.
        """
        return list(COMMON_EXCHANGES)

    @staticmethod
    def list_all_exchanges() -> List[str]:
        """Return the full list of all CCXT-supported exchanges."""
        try:
            ccxt = _get_ccxt()
            return list(ccxt.exchanges)
        except ImportError:
            return list(COMMON_EXCHANGES)

    # ── Cleanup ──────────────────────────────────────────────

    def close(self) -> None:
        """Clean up the exchange instance."""
        if self._exchange is not None:
            try:
                self._exchange.close()
            except Exception:
                pass
            self._exchange = None
