"""Pyth Benchmarks API OHLCV candle provider.

Fetches historical candle data from Pyth's TradingView-compatible Benchmarks API.
No API key required.

Endpoint: https://benchmarks.pyth.network/v1/shims/tradingview/history
Params:   symbol (e.g. "Crypto.SOL/USD"), resolution, from (unix), to (unix)
Response: TradingView UDF format — {s, t, o, h, l, c, v}
"""
from __future__ import annotations


# Phase 1 T1.3.a + D-1.3-providers — point-in-time declaration.
# Defaults are conservative — callers should verify against the
# specific source API when using this data in parity/PIT-sensitive
# contexts. Review date: 2026-04-24.
PIT_METADATA = {
    "candle_ts": "bar-close",
    "funding_ts": "exchange-time",
    "orderbook_ts": "exchange-time",
    "oi_ts": "exchange-time",
    "reviewed": "2026-04-24",
}

import logging
from typing import List, Optional

import httpx

from ..models import Candle
from .registry import DataProvider, register

logger = logging.getLogger("flint.providers.pyth_candles")

_BENCHMARKS_API = "https://benchmarks.pyth.network/v1/shims/tradingview/history"

# Flint market name → Pyth TradingView symbol
_MARKET_SYMBOLS: dict[str, str] = {
    # Perpetuals
    "SOL-PERP":    "Crypto.SOL/USD",
    "BTC-PERP":    "Crypto.BTC/USD",
    "ETH-PERP":    "Crypto.ETH/USD",
    "BONK-PERP":   "Crypto.BONK/USD",
    "JUP-PERP":    "Crypto.JUP/USD",
    "WIF-PERP":    "Crypto.WIF/USD",
    "DOGE-PERP":   "Crypto.DOGE/USD",
    "AVAX-PERP":   "Crypto.AVAX/USD",
    "LINK-PERP":   "Crypto.LINK/USD",
    "SUI-PERP":    "Crypto.SUI/USD",
    "ARB-PERP":    "Crypto.ARB/USD",
    "XRP-PERP":    "Crypto.XRP/USD",
    "INJ-PERP":    "Crypto.INJ/USD",
    "OP-PERP":     "Crypto.OP/USD",
    "TIA-PERP":    "Crypto.TIA/USD",
    "SEI-PERP":    "Crypto.SEI/USD",
    "BNB-PERP":    "Crypto.BNB/USD",
    "DRIFT-PERP":  "Crypto.DRIFT/USD",
    "RENDER-PERP": "Crypto.RENDER/USD",
    "PYTH-PERP":   "Crypto.PYTH/USD",
    # Spot
    "SOL":         "Crypto.SOL/USD",
    "BTC":         "Crypto.BTC/USD",
    "ETH":         "Crypto.ETH/USD",
}

# Resolution in seconds → TradingView resolution string
_RESOLUTION_MAP: dict[int, str] = {
    60:     "1",
    300:    "5",
    900:    "15",
    1800:   "30",
    3600:   "60",
    14400:  "240",
    86400:  "1D",
    604800: "1W",
}


def _market_to_symbol(market: str) -> str:
    """Convert a Flint market name to a Pyth TradingView symbol.

    Falls back to ``Crypto.<BASE>/USD`` for unknown markets, e.g.
    ``FOO-PERP`` → ``Crypto.FOO/USD``.
    """
    if market in _MARKET_SYMBOLS:
        return _MARKET_SYMBOLS[market]
    # Best-effort fallback: strip "-PERP" suffix and build symbol
    base = market.replace("-PERP", "").replace("-SPOT", "")
    fallback = f"Crypto.{base}/USD"
    logger.debug("Unknown market %r; falling back to symbol %r", market, fallback)
    return fallback


def _resolution_to_tv(resolution_s: int) -> str:
    """Convert resolution in seconds to TradingView resolution string.

    Falls back to "60" (1-hour) if the resolution is not in the map.
    """
    if resolution_s in _RESOLUTION_MAP:
        return _RESOLUTION_MAP[resolution_s]
    logger.warning(
        "Unsupported resolution %ds for Pyth Benchmarks API, falling back to 60 (1h)",
        resolution_s,
    )
    return "60"


@register
class PythCandleProvider(DataProvider):
    """Fetches OHLCV candles from the Pyth Benchmarks TradingView-shim API."""

    name = "pyth_candles"
    requires_api_key = False

    # Class-level list for backward compat with registry.supported_data_types()
    supported_data_types = ["candles"]

    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._client = client or httpx.Client(timeout=30)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def is_available(self) -> bool:
        return True

    # -- public API -----------------------------------------------------------

    def supported_markets(self) -> List[str]:
        """Return the list of markets with explicit symbol mappings."""
        return list(_MARKET_SYMBOLS.keys())

    def fetch_candles(
        self,
        market: str,
        resolution_s: int,
        start_ts: int,
        end_ts: int,
    ) -> List[Candle]:
        """Fetch OHLCV candles from the Pyth Benchmarks API.

        Args:
            market:      Flint market name, e.g. ``"SOL-PERP"``.
            resolution_s: Candle width in seconds (60, 3600, 86400, …).
            start_ts:    Range start as a Unix timestamp (inclusive).
            end_ts:      Range end as a Unix timestamp (inclusive).

        Returns:
            A list of :class:`~flint.models.Candle` objects with
            ``venue="pyth"``.  Returns an empty list when the API reports
            ``"no_data"`` for the requested range.

        Raises:
            ValueError: If the API returns a non-200 HTTP status.
        """
        symbol = _market_to_symbol(market)
        resolution = _resolution_to_tv(resolution_s)

        params = {
            "symbol": symbol,
            "resolution": resolution,
            "from": start_ts,
            "to": end_ts,
        }

        resp = self._client.get(_BENCHMARKS_API, params=params)
        if resp.status_code != 200:
            raise ValueError(
                f"Pyth Benchmarks API returned {resp.status_code}: {resp.text[:200]}"
            )

        data = resp.json()

        if data.get("s") == "no_data" or data.get("s") != "ok":
            logger.debug(
                "Pyth returned status=%r for %s [%s, %s]",
                data.get("s"),
                market,
                start_ts,
                end_ts,
            )
            return []

        timestamps: List[int] = data["t"]
        opens: List[float] = data["o"]
        highs: List[float] = data["h"]
        lows: List[float] = data["l"]
        closes: List[float] = data["c"]
        volumes: List[float] = data["v"]

        candles: List[Candle] = []
        for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes):
            candles.append(
                Candle(
                    ts=int(ts),
                    open=float(o),
                    high=float(h),
                    low=float(l),
                    close=float(c),
                    volume=float(v),
                    market=market,
                    resolution_s=resolution_s,
                    venue="pyth",
                )
            )

        return candles
