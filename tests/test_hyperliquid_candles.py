"""Tests for HyperliquidCandleProvider — mocked HTTP."""
from unittest.mock import patch, MagicMock

from flint.providers.hyperliquid_candles import HyperliquidCandleProvider


class TestFetchCandles:
    def test_parses_candle_response(self):
        provider = HyperliquidCandleProvider()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"t": 1700000000000, "T": 1700000060000, "s": "SOL",
             "o": "150.0", "h": "155.0", "l": "148.0", "c": "153.0", "v": "1000.0"},
            {"t": 1700000060000, "T": 1700000120000, "s": "SOL",
             "o": "153.0", "h": "156.0", "l": "152.0", "c": "154.0", "v": "800.0"},
        ]
        with patch.object(provider._client, "post", return_value=mock_response):
            candles = provider.fetch_candles("SOL-PERP", 1700000000, 1700003600)
        assert len(candles) == 2
        assert candles[0].market == "SOL-PERP"
        assert candles[0].venue == "hyperliquid"
        assert candles[0].open == 150.0
        assert candles[0].close == 153.0
        assert candles[0].ts == 1700000000
        provider.close()

    def test_unknown_market_returns_empty(self):
        provider = HyperliquidCandleProvider()
        candles = provider.fetch_candles("UNKNOWN-PERP", 1700000000, 1700003600)
        assert candles == []
        provider.close()

    def test_pagination(self):
        provider = HyperliquidCandleProvider()
        first_batch = [
            {"t": 1700000000000 + i * 60000, "T": 1700000000000 + (i + 1) * 60000,
             "s": "SOL", "o": "150.0", "h": "155.0", "l": "148.0", "c": "153.0", "v": "100.0"}
            for i in range(5000)
        ]
        second_batch = [
            {"t": 1700000000000 + 5000 * 60000 + i * 60000,
             "T": 1700000000000 + 5001 * 60000 + i * 60000,
             "s": "SOL", "o": "153.0", "h": "156.0", "l": "152.0", "c": "154.0", "v": "80.0"}
            for i in range(100)
        ]
        mock_resp1 = MagicMock()
        mock_resp1.status_code = 200
        mock_resp1.json.return_value = first_batch
        mock_resp2 = MagicMock()
        mock_resp2.status_code = 200
        mock_resp2.json.return_value = second_batch
        with patch.object(provider._client, "post", side_effect=[mock_resp1, mock_resp2]):
            candles = provider.fetch_candles("SOL-PERP", 1700000000, 1700400000)
        assert len(candles) == 5100
        provider.close()

    def test_resolution_parameter(self):
        provider = HyperliquidCandleProvider()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"t": 1700000000000, "T": 1700003600000, "s": "SOL",
             "o": "150.0", "h": "155.0", "l": "148.0", "c": "153.0", "v": "5000.0"},
        ]
        with patch.object(provider._client, "post", return_value=mock_response) as mock_post:
            candles = provider.fetch_candles("SOL-PERP", 1700000000, 1700003600, resolution="1h")
        call_json = mock_post.call_args.kwargs.get("json", mock_post.call_args[1].get("json"))
        assert call_json["req"]["interval"] == "1h"
        assert candles[0].resolution_s == 3600
        provider.close()
