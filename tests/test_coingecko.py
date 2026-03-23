"""Tests for CoinGecko provider."""
from unittest.mock import MagicMock
from flint.providers.coingecko import CoinGeckoProvider, COINGECKO_IDS


def test_coingecko_no_key_required():
    p = CoinGeckoProvider()
    assert p.is_available()
    assert not p.requires_api_key


def test_coingecko_supported_data_types():
    p = CoinGeckoProvider()
    assert "candles" in p.supported_data_types()


def test_resolve_id():
    p = CoinGeckoProvider()
    assert p.resolve_id("BTC") == "bitcoin"
    assert p.resolve_id("ETH") == "ethereum"
    assert p.resolve_id("SOL") == "solana"
    assert p.resolve_id("BTC-PERP") == "bitcoin"
    assert p.resolve_id("NONEXISTENT") is None


def test_known_ids_coverage():
    assert "BTC" in COINGECKO_IDS
    assert "ETH" in COINGECKO_IDS
    assert "SOL" in COINGECKO_IDS
    assert len(COINGECKO_IDS) >= 20


def test_fetch_candles_parses_market_chart():
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    # Simulate 3 hourly price points
    mock_resp.json.return_value = {
        "prices": [
            [1700000000000, 35000.0],
            [1700003600000, 35100.0],
            [1700007200000, 35050.0],
        ],
        "total_volumes": [
            [1700000000000, 1000000000.0],
            [1700003600000, 1100000000.0],
            [1700007200000, 900000000.0],
        ],
    }
    mock_client.get.return_value = mock_resp

    p = CoinGeckoProvider(client=mock_client)
    candles = p.fetch_candles("BTC", 3600, 1700000000, 1700010800)

    assert len(candles) == 3
    assert candles[0].market == "BTC"
    assert candles[0].close == 35000.0
    assert candles[1].close == 35100.0
    assert candles[0].resolution_s == 3600


def test_fetch_candles_aggregates_to_resolution():
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    # 4 hourly points → should aggregate to 2 candles at 2h resolution
    mock_resp.json.return_value = {
        "prices": [
            [1700000000000, 100.0],
            [1700003600000, 105.0],
            [1700007200000, 103.0],
            [1700010800000, 108.0],
        ],
        "total_volumes": [
            [1700000000000, 1000.0],
            [1700003600000, 2000.0],
            [1700007200000, 1500.0],
            [1700010800000, 1800.0],
        ],
    }
    mock_client.get.return_value = mock_resp

    p = CoinGeckoProvider(client=mock_client)
    candles = p.fetch_candles("ETH", 7200, 1700000000, 1700014400)  # 2h resolution

    assert len(candles) == 2
    # First 2h candle: open=100, high=105, low=100, close=105
    assert candles[0].open == 100.0
    assert candles[0].high == 105.0
    assert candles[0].close == 105.0


def test_fetch_candles_empty_response():
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"prices": [], "total_volumes": []}
    mock_client.get.return_value = mock_resp

    p = CoinGeckoProvider(client=mock_client)
    candles = p.fetch_candles("BTC", 3600, 1700000000, 1700010800)
    assert candles == []


def test_fetch_candles_handles_error():
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_client.get.return_value = mock_resp

    p = CoinGeckoProvider(client=mock_client)
    candles = p.fetch_candles("BTC", 3600, 1700000000, 1700010800)
    assert candles == []


def test_unknown_market_returns_empty():
    p = CoinGeckoProvider()
    candles = p.fetch_candles("FAKECOIN123", 3600, 1700000000, 1700010800)
    assert candles == []


def test_close():
    mock_client = MagicMock()
    p = CoinGeckoProvider(client=mock_client)
    p.close()
    mock_client.close.assert_not_called()  # didn't create it

    p2 = CoinGeckoProvider()
    p2.close()  # should not raise
