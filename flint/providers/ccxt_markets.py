"""CCXT market discovery and symbol mapping.

Handles conversion between Flint's internal market naming convention
(e.g. "SOL-PERP", "BTC/USDT") and exchange-specific CCXT symbol formats
(e.g. "SOL/USDT:USDT" for Binance perps, "SOL-USDT-SWAP" for OKX).
"""
from __future__ import annotations


# Phase 1 T1.3.a + D-1.3-providers — point-in-time declaration.
# Defaults are conservative — callers should verify against the
# specific source API when using this data in parity/PIT-sensitive
# contexts. Review date: 2026-04-24.
PIT_METADATA = {
    "candle_ts": "bar-close",
    "funding_ts": "accrual-time",
    "orderbook_ts": "exchange-time",
    "oi_ts": "exchange-time",
    "reviewed": "2026-04-24",
}

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class CCXTMarketMapper:
    """Bidirectional symbol mapping between Flint and CCXT exchanges."""

    # Exchange-specific quirks for perpetual futures symbol formats.
    # Most exchanges use "BASE/QUOTE:SETTLE" for linear perps,
    # but the settle currency and conventions vary.
    _PERP_SUFFIX: Dict[str, str] = {
        "binance": "/USDT:USDT",
        "bybit": "/USDT:USDT",
        "okx": "/USDT:USDT",
        "bitget": "/USDT:USDT",
        "gate": "/USDT:USDT",
        "mexc": "/USDT:USDT",
        "htx": "/USDT:USDT",
        "kucoin": "/USDT:USDT",
        "coinbase": "/USD:USD",  # Coinbase derivatives use USD
        "kraken": "/USD:USD",
    }

    def __init__(self, exchange_instance=None):
        """Optionally accept a live ccxt exchange instance for market lookups."""
        self._exchange = exchange_instance
        self._markets_loaded = False

    def _ensure_markets(self) -> None:
        """Load markets from the exchange if we have a live instance."""
        if self._exchange and not self._markets_loaded:
            try:
                self._exchange.load_markets()
                self._markets_loaded = True
            except Exception as e:
                logger.warning("Failed to load markets: %s", e)

    # ── Flint → CCXT ─────────────────────────────────────────

    def to_ccxt_symbol(self, flint_market: str, exchange: str = "binance") -> str:
        """Convert a Flint market name to a CCXT symbol.

        Conversions:
            "SOL-PERP"   → "SOL/USDT:USDT"  (perp on most exchanges)
            "SOL/USDT"   → "SOL/USDT"        (spot, passthrough)
            "BTC/USD"    → "BTC/USD"          (spot, passthrough)

        Args:
            flint_market: Flint-style market string.
            exchange: Exchange name (lowercase) for exchange-specific formatting.

        Returns:
            CCXT-compatible symbol string.
        """
        exchange = exchange.lower()

        # Already in CCXT format (contains "/" with possible ":SETTLE")
        if "/" in flint_market and "-PERP" not in flint_market:
            return flint_market

        # Perpetual futures: "SOL-PERP" → "SOL/USDT:USDT"
        if flint_market.endswith("-PERP"):
            base = flint_market[:-5]  # strip "-PERP"
            suffix = self._PERP_SUFFIX.get(exchange, "/USDT:USDT")
            return f"{base}{suffix}"

        # Fallback: assume spot with USDT quote
        # "SOL" → "SOL/USDT"
        if "/" not in flint_market and "-" not in flint_market:
            return f"{flint_market}/USDT"

        # Unknown format — return as-is
        return flint_market

    # ── CCXT → Flint ─────────────────────────────────────────

    def from_ccxt_symbol(self, ccxt_symbol: str, exchange: str = "binance") -> str:
        """Convert a CCXT symbol back to Flint's naming convention.

        Conversions:
            "SOL/USDT:USDT" → "SOL-PERP"   (linear perp)
            "SOL/USDT"      → "SOL/USDT"    (spot, passthrough)
            "BTC/USD:BTC"   → "BTC-PERP"    (inverse perp)

        Args:
            ccxt_symbol: CCXT-style symbol string.
            exchange: Exchange name (lowercase).

        Returns:
            Flint-style market string.
        """
        # Perp format: "BASE/QUOTE:SETTLE"
        if ":" in ccxt_symbol:
            base_quote, _settle = ccxt_symbol.split(":", 1)
            base = base_quote.split("/")[0]
            return f"{base}-PERP"

        # Spot: passthrough
        return ccxt_symbol

    # ── Market discovery ─────────────────────────────────────

    def discover_markets(
        self,
        exchange=None,
        market_type: str = "swap",
        quote: str = "USDT",
        active_only: bool = True,
    ) -> List[dict]:
        """List available markets from a live exchange instance.

        Args:
            exchange: A ccxt exchange instance (overrides self._exchange).
            market_type: "swap" (perps), "spot", "future", or "option".
            quote: Quote currency filter (e.g. "USDT", "USD").
            active_only: Only return active/tradeable markets.

        Returns:
            List of dicts with keys: symbol, base, quote, type, active, flint_symbol.
        """
        ex = exchange or self._exchange
        if ex is None:
            logger.warning("No exchange instance for market discovery")
            return []

        try:
            if not ex.markets:
                ex.load_markets()
        except Exception as e:
            logger.error("Failed to load markets: %s", e)
            return []

        results = []
        for symbol, market in ex.markets.items():
            # Type filter
            mtype = market.get("type", "")
            if market_type and mtype != market_type:
                continue

            # Quote currency filter
            mquote = market.get("quote", "")
            if quote and mquote.upper() != quote.upper():
                continue

            # Active filter
            if active_only and not market.get("active", True):
                continue

            exchange_name = getattr(ex, "id", "unknown")
            flint_sym = self.from_ccxt_symbol(symbol, exchange_name)

            results.append({
                "symbol": symbol,
                "base": market.get("base", ""),
                "quote": mquote,
                "type": mtype,
                "active": market.get("active", True),
                "flint_symbol": flint_sym,
            })

        # Sort by base currency
        results.sort(key=lambda m: m["base"])
        return results
