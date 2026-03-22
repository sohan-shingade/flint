"""Tests for the CCXT provider and market mapper.

All tests use mocks — no real exchange connections needed.
"""
from __future__ import annotations

import sys
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ─── Test: ccxt not installed ────────────────────────────────────

class TestCCXTNotInstalled:
    """Verify graceful handling when ccxt is not installed."""

    def test_import_without_ccxt(self):
        """CCXTProvider should import even if ccxt is absent."""
        # Force-reset the cached module to test the import path
        import flint.providers.ccxt_provider as mod
        original = mod._ccxt
        mod._ccxt = None

        with patch.dict(sys.modules, {"ccxt": None}):
            from flint.providers.ccxt_provider import _is_ccxt_available
            # Re-reset after patch
            mod._ccxt = None
            assert _is_ccxt_available() is False

        # Restore
        mod._ccxt = original

    def test_provider_is_available_without_ccxt(self):
        """is_available() should return False when ccxt is missing."""
        import flint.providers.ccxt_provider as mod
        original = mod._ccxt
        mod._ccxt = None

        with patch.dict(sys.modules, {"ccxt": None}):
            from flint.providers.ccxt_provider import CCXTProvider
            mod._ccxt = None
            provider = CCXTProvider(exchange="binance")
            assert provider.is_available() is False

        mod._ccxt = original

    def test_fetch_candles_raises_without_ccxt(self):
        """fetch_candles should raise ImportError when ccxt is missing."""
        import flint.providers.ccxt_provider as mod
        original = mod._ccxt
        mod._ccxt = None

        with patch.dict(sys.modules, {"ccxt": None}):
            from flint.providers.ccxt_provider import CCXTProvider
            mod._ccxt = None
            provider = CCXTProvider(exchange="binance")
            with pytest.raises(ImportError, match="ccxt is not installed"):
                provider.fetch_candles("SOL/USDT", 3600, 0, 1000)

        mod._ccxt = original


# ─── Test: Symbol mapping ───────────────────────────────────────

class TestCCXTMarketMapper:
    """Test bidirectional symbol conversion."""

    def test_perp_to_ccxt_binance(self):
        from flint.providers.ccxt_markets import CCXTMarketMapper
        mapper = CCXTMarketMapper()
        assert mapper.to_ccxt_symbol("SOL-PERP", "binance") == "SOL/USDT:USDT"
        assert mapper.to_ccxt_symbol("BTC-PERP", "binance") == "BTC/USDT:USDT"
        assert mapper.to_ccxt_symbol("ETH-PERP", "bybit") == "ETH/USDT:USDT"

    def test_perp_to_ccxt_coinbase(self):
        from flint.providers.ccxt_markets import CCXTMarketMapper
        mapper = CCXTMarketMapper()
        assert mapper.to_ccxt_symbol("SOL-PERP", "coinbase") == "SOL/USD:USD"

    def test_spot_passthrough(self):
        from flint.providers.ccxt_markets import CCXTMarketMapper
        mapper = CCXTMarketMapper()
        assert mapper.to_ccxt_symbol("SOL/USDT", "binance") == "SOL/USDT"
        assert mapper.to_ccxt_symbol("BTC/USD", "kraken") == "BTC/USD"

    def test_bare_symbol_becomes_spot(self):
        from flint.providers.ccxt_markets import CCXTMarketMapper
        mapper = CCXTMarketMapper()
        assert mapper.to_ccxt_symbol("SOL", "binance") == "SOL/USDT"

    def test_from_ccxt_perp(self):
        from flint.providers.ccxt_markets import CCXTMarketMapper
        mapper = CCXTMarketMapper()
        assert mapper.from_ccxt_symbol("SOL/USDT:USDT", "binance") == "SOL-PERP"
        assert mapper.from_ccxt_symbol("BTC/USD:BTC", "bitmex") == "BTC-PERP"

    def test_from_ccxt_spot(self):
        from flint.providers.ccxt_markets import CCXTMarketMapper
        mapper = CCXTMarketMapper()
        assert mapper.from_ccxt_symbol("SOL/USDT", "binance") == "SOL/USDT"
        assert mapper.from_ccxt_symbol("ETH/BTC", "kraken") == "ETH/BTC"

    def test_roundtrip_perp(self):
        from flint.providers.ccxt_markets import CCXTMarketMapper
        mapper = CCXTMarketMapper()
        for exchange in ["binance", "bybit", "okx", "kucoin"]:
            ccxt_sym = mapper.to_ccxt_symbol("SOL-PERP", exchange)
            back = mapper.from_ccxt_symbol(ccxt_sym, exchange)
            assert back == "SOL-PERP", f"Roundtrip failed for {exchange}: {ccxt_sym} -> {back}"

    def test_roundtrip_spot(self):
        from flint.providers.ccxt_markets import CCXTMarketMapper
        mapper = CCXTMarketMapper()
        ccxt_sym = mapper.to_ccxt_symbol("ETH/USDT", "binance")
        back = mapper.from_ccxt_symbol(ccxt_sym, "binance")
        assert back == "ETH/USDT"


# ─── Test: CCXTProvider with mocked exchange ─────────────────────

def _make_mock_ccxt():
    """Create a mock ccxt module with a mock exchange."""
    mock_ccxt = MagicMock()
    mock_ccxt.exchanges = ["binance", "bybit", "okx", "kraken", "coinbase"]

    mock_exchange_cls = MagicMock()
    mock_exchange_instance = MagicMock()
    mock_exchange_instance.id = "binance"
    mock_exchange_instance.markets = {}
    mock_exchange_cls.return_value = mock_exchange_instance
    mock_ccxt.binance = mock_exchange_cls

    return mock_ccxt, mock_exchange_instance


class TestCCXTProviderCandles:
    """Test candle fetching with mocked exchange."""

    def test_fetch_candles_basic(self):
        mock_ccxt, mock_exchange = _make_mock_ccxt()

        now = int(time.time())
        start_ts = now - 7200
        end_ts = now

        # Mock OHLCV data: [timestamp_ms, open, high, low, close, volume]
        mock_ohlcv = [
            [start_ts * 1000, 100.0, 105.0, 99.0, 103.0, 1000.0],
            [(start_ts + 3600) * 1000, 103.0, 108.0, 101.0, 106.0, 1500.0],
        ]
        mock_exchange.fetch_ohlcv.return_value = mock_ohlcv

        import flint.providers.ccxt_provider as mod
        original = mod._ccxt
        mod._ccxt = mock_ccxt

        try:
            from flint.providers.ccxt_provider import CCXTProvider
            provider = CCXTProvider(exchange="binance")
            candles = provider.fetch_candles("SOL/USDT", 3600, start_ts, end_ts)

            assert len(candles) == 2
            assert candles[0].open == 100.0
            assert candles[0].close == 103.0
            assert candles[0].volume == 1000.0
            assert candles[0].resolution_s == 3600
            assert candles[1].open == 103.0
            assert candles[1].high == 108.0

            # Verify the exchange was called with correct params
            mock_exchange.fetch_ohlcv.assert_called()
        finally:
            mod._ccxt = original

    def test_fetch_candles_deduplicates(self):
        mock_ccxt, mock_exchange = _make_mock_ccxt()

        now = int(time.time())
        ts = now - 3600

        # Duplicate timestamps
        mock_ohlcv = [
            [ts * 1000, 100.0, 105.0, 99.0, 103.0, 1000.0],
            [ts * 1000, 100.0, 105.0, 99.0, 103.0, 1000.0],  # duplicate
        ]
        mock_exchange.fetch_ohlcv.return_value = mock_ohlcv

        import flint.providers.ccxt_provider as mod
        original = mod._ccxt
        mod._ccxt = mock_ccxt

        try:
            from flint.providers.ccxt_provider import CCXTProvider
            provider = CCXTProvider(exchange="binance")
            candles = provider.fetch_candles("SOL/USDT", 3600, ts, now)
            assert len(candles) == 1
        finally:
            mod._ccxt = original

    def test_fetch_candles_empty_response(self):
        mock_ccxt, mock_exchange = _make_mock_ccxt()
        mock_exchange.fetch_ohlcv.return_value = []

        import flint.providers.ccxt_provider as mod
        original = mod._ccxt
        mod._ccxt = mock_ccxt

        try:
            from flint.providers.ccxt_provider import CCXTProvider
            provider = CCXTProvider(exchange="binance")
            candles = provider.fetch_candles("SOL/USDT", 3600, 0, 1000)
            assert candles == []
        finally:
            mod._ccxt = original

    def test_fetch_candles_with_flint_perp_symbol(self):
        """SOL-PERP should be converted to SOL/USDT:USDT for the API call."""
        mock_ccxt, mock_exchange = _make_mock_ccxt()
        mock_exchange.fetch_ohlcv.return_value = []

        import flint.providers.ccxt_provider as mod
        original = mod._ccxt
        mod._ccxt = mock_ccxt

        try:
            from flint.providers.ccxt_provider import CCXTProvider
            provider = CCXTProvider(exchange="binance")
            provider.fetch_candles("SOL-PERP", 3600, 0, 1000)

            # Should have been called with the CCXT symbol format
            call_args = mock_exchange.fetch_ohlcv.call_args
            assert call_args[0][0] == "SOL/USDT:USDT"
        finally:
            mod._ccxt = original


class TestCCXTProviderFunding:
    """Test funding rate fetching with mocked exchange."""

    def test_fetch_funding_rates(self):
        mock_ccxt, mock_exchange = _make_mock_ccxt()

        now = int(time.time())
        start_ts = now - 86400
        end_ts = now

        mock_history = [
            {
                "timestamp": (start_ts + 3600) * 1000,
                "fundingRate": 0.0001,
                "markPrice": 100.5,
                "indexPrice": 100.0,
                "info": {},
            },
            {
                "timestamp": (start_ts + 32400) * 1000,  # 9h later
                "fundingRate": 0.00015,
                "markPrice": 101.0,
                "indexPrice": 100.8,
                "info": {},
            },
        ]
        # Return data on first call, empty on second to stop pagination
        mock_exchange.fetch_funding_rate_history.side_effect = [mock_history, []]

        import flint.providers.ccxt_provider as mod
        original = mod._ccxt
        mod._ccxt = mock_ccxt

        try:
            from flint.providers.ccxt_provider import CCXTProvider
            provider = CCXTProvider(exchange="binance")
            rates = provider.fetch_funding_rates("SOL-PERP", start_ts, end_ts)

            assert len(rates) == 2
            # Binance uses 8h funding, so rate should be divided by 8
            assert rates[0].rate == pytest.approx(0.0001 / 8)
            assert rates[0].mark_price == 100.5
            assert rates[0].oracle_price == 100.0
            assert rates[0].market == "SOL-PERP"
        finally:
            mod._ccxt = original

    def test_fetch_funding_rates_empty(self):
        mock_ccxt, mock_exchange = _make_mock_ccxt()
        mock_exchange.fetch_funding_rate_history.return_value = []

        import flint.providers.ccxt_provider as mod
        original = mod._ccxt
        mod._ccxt = mock_ccxt

        try:
            from flint.providers.ccxt_provider import CCXTProvider
            provider = CCXTProvider(exchange="binance")
            rates = provider.fetch_funding_rates("SOL-PERP", 0, 1000)
            assert rates == []
        finally:
            mod._ccxt = original


class TestCCXTProviderMarkets:
    """Test market listing with mocked exchange."""

    def test_list_markets(self):
        mock_ccxt, mock_exchange = _make_mock_ccxt()
        mock_exchange.markets = {
            "SOL/USDT": {"base": "SOL", "quote": "USDT", "type": "spot", "active": True},
            "BTC/USDT": {"base": "BTC", "quote": "USDT", "type": "spot", "active": True},
            "ETH/BTC": {"base": "ETH", "quote": "BTC", "type": "spot", "active": True},
            "SOL/USDT:USDT": {"base": "SOL", "quote": "USDT", "type": "swap", "active": True},
        }

        import flint.providers.ccxt_provider as mod
        original = mod._ccxt
        mod._ccxt = mock_ccxt

        try:
            from flint.providers.ccxt_provider import CCXTProvider
            provider = CCXTProvider(exchange="binance")
            markets = provider.list_markets(quote="USDT")

            # Should include SOL/USDT, BTC/USDT, SOL/USDT:USDT but NOT ETH/BTC
            symbols = [m["symbol"] for m in markets]
            assert "SOL/USDT" in symbols
            assert "BTC/USDT" in symbols
            assert "SOL/USDT:USDT" in symbols
            assert "ETH/BTC" not in symbols
        finally:
            mod._ccxt = original

    def test_list_markets_all_quotes(self):
        mock_ccxt, mock_exchange = _make_mock_ccxt()
        mock_exchange.markets = {
            "SOL/USDT": {"base": "SOL", "quote": "USDT", "type": "spot", "active": True},
            "ETH/BTC": {"base": "ETH", "quote": "BTC", "type": "spot", "active": True},
        }

        import flint.providers.ccxt_provider as mod
        original = mod._ccxt
        mod._ccxt = mock_ccxt

        try:
            from flint.providers.ccxt_provider import CCXTProvider
            provider = CCXTProvider(exchange="binance")
            markets = provider.list_markets(quote="")

            # Should include all markets
            assert len(markets) == 2
        finally:
            mod._ccxt = original


class TestCCXTProviderOrderbook:
    """Test orderbook fetching with mocked exchange."""

    def test_fetch_orderbook(self):
        mock_ccxt, mock_exchange = _make_mock_ccxt()
        mock_exchange.fetch_order_book.return_value = {
            "timestamp": 1700000000000,
            "bids": [[100.0, 10.0], [99.5, 20.0], [99.0, 30.0]],
            "asks": [[100.5, 15.0], [101.0, 25.0], [101.5, 35.0]],
        }

        import flint.providers.ccxt_provider as mod
        original = mod._ccxt
        mod._ccxt = mock_ccxt

        try:
            from flint.providers.ccxt_provider import CCXTProvider
            provider = CCXTProvider(exchange="binance")
            book = provider.fetch_orderbook("SOL/USDT", limit=2)

            assert len(book["bids"]) == 2
            assert len(book["asks"]) == 2
            assert book["bids"][0] == [100.0, 10.0]
            assert book["asks"][0] == [100.5, 15.0]
        finally:
            mod._ccxt = original


class TestCCXTProviderTicker:
    """Test ticker fetching with mocked exchange."""

    def test_fetch_ticker(self):
        mock_ccxt, mock_exchange = _make_mock_ccxt()
        mock_exchange.fetch_ticker.return_value = {
            "last": 100.5,
            "bid": 100.0,
            "ask": 101.0,
            "high": 105.0,
            "low": 95.0,
            "baseVolume": 1000000,
            "quoteVolume": 100500000,
            "change": 2.5,
            "percentage": 2.55,
            "vwap": 100.2,
            "timestamp": 1700000000000,
        }

        import flint.providers.ccxt_provider as mod
        original = mod._ccxt
        mod._ccxt = mock_ccxt

        try:
            from flint.providers.ccxt_provider import CCXTProvider
            provider = CCXTProvider(exchange="binance")
            ticker = provider.fetch_ticker("SOL/USDT")

            assert ticker["last"] == 100.5
            assert ticker["bid"] == 100.0
            assert ticker["ask"] == 101.0
            assert ticker["volume"] == 1000000
        finally:
            mod._ccxt = original


class TestCCXTProviderExchangeValidation:
    """Test exchange name validation."""

    def test_invalid_exchange_raises(self):
        mock_ccxt, _ = _make_mock_ccxt()

        import flint.providers.ccxt_provider as mod
        original = mod._ccxt
        mod._ccxt = mock_ccxt

        try:
            from flint.providers.ccxt_provider import CCXTProvider
            provider = CCXTProvider(exchange="not_a_real_exchange")
            with pytest.raises(ValueError, match="Unknown exchange"):
                provider.fetch_candles("SOL/USDT", 3600, 0, 1000)
        finally:
            mod._ccxt = original

    def test_is_available_valid_exchange(self):
        mock_ccxt, _ = _make_mock_ccxt()

        import flint.providers.ccxt_provider as mod
        original = mod._ccxt
        mod._ccxt = mock_ccxt

        try:
            from flint.providers.ccxt_provider import CCXTProvider
            provider = CCXTProvider(exchange="binance")
            assert provider.is_available() is True
        finally:
            mod._ccxt = original

    def test_is_available_invalid_exchange(self):
        mock_ccxt, _ = _make_mock_ccxt()

        import flint.providers.ccxt_provider as mod
        original = mod._ccxt
        mod._ccxt = mock_ccxt

        try:
            from flint.providers.ccxt_provider import CCXTProvider
            provider = CCXTProvider(exchange="fakexchange")
            assert provider.is_available() is False
        finally:
            mod._ccxt = original


class TestCCXTProviderMetadata:
    """Test provider metadata and static methods."""

    def test_name(self):
        from flint.providers.ccxt_provider import CCXTProvider
        provider = CCXTProvider(exchange="binance")
        assert provider.name == "ccxt"

    def test_requires_api_key(self):
        from flint.providers.ccxt_provider import CCXTProvider
        provider = CCXTProvider(exchange="binance")
        assert provider.requires_api_key is False

    def test_supported_data_types(self):
        from flint.providers.ccxt_provider import CCXTProvider
        provider = CCXTProvider(exchange="binance")
        types = provider.supported_data_types()
        assert "candles" in types
        assert "funding" in types
        assert "orderbook" in types
        assert "ticker" in types

    def test_list_exchanges_static(self):
        from flint.providers.ccxt_provider import CCXTProvider
        exchanges = CCXTProvider.list_exchanges()
        assert "binance" in exchanges
        assert "bybit" in exchanges
        assert "okx" in exchanges
        assert len(exchanges) >= 10

    def test_close(self):
        from flint.providers.ccxt_provider import CCXTProvider
        provider = CCXTProvider(exchange="binance")
        # Should not raise even without an exchange instance
        provider.close()


class TestCCXTMarketMapperDiscovery:
    """Test market discovery via CCXTMarketMapper."""

    def test_discover_markets(self):
        from flint.providers.ccxt_markets import CCXTMarketMapper

        mock_exchange = MagicMock()
        mock_exchange.id = "binance"
        mock_exchange.markets = {
            "SOL/USDT:USDT": {"base": "SOL", "quote": "USDT", "type": "swap", "active": True},
            "BTC/USDT:USDT": {"base": "BTC", "quote": "USDT", "type": "swap", "active": True},
            "ETH/USDT": {"base": "ETH", "quote": "USDT", "type": "spot", "active": True},
            "DOGE/BTC": {"base": "DOGE", "quote": "BTC", "type": "spot", "active": True},
        }

        mapper = CCXTMarketMapper(mock_exchange)
        results = mapper.discover_markets(market_type="swap", quote="USDT")

        assert len(results) == 2
        symbols = [r["symbol"] for r in results]
        assert "SOL/USDT:USDT" in symbols
        assert "BTC/USDT:USDT" in symbols

        # Check flint_symbol mapping
        for r in results:
            if r["symbol"] == "SOL/USDT:USDT":
                assert r["flint_symbol"] == "SOL-PERP"

    def test_discover_markets_no_exchange(self):
        from flint.providers.ccxt_markets import CCXTMarketMapper
        mapper = CCXTMarketMapper()
        results = mapper.discover_markets()
        assert results == []
