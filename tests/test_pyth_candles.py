"""Tests for PythCandleProvider."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from flint.providers.pyth_candles import PythCandleProvider, _MARKET_SYMBOLS, _RESOLUTION_MAP
from flint.models import Candle


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_TV_RESPONSE = {
    "s": "ok",
    "t": [1700000000, 1700003600],
    "o": [60.1, 60.5],
    "h": [61.0, 61.2],
    "l": [59.8, 60.0],
    "c": [60.5, 60.9],
    "v": [1000.0, 1200.0],
}

MOCK_NO_DATA_RESPONSE = {"s": "no_data"}


def _make_provider(mock_response: dict) -> tuple[PythCandleProvider, MagicMock]:
    """Return a provider wired to a mocked httpx.Client."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_response

    mock_client = MagicMock()
    mock_client.get.return_value = mock_resp

    provider = PythCandleProvider(client=mock_client)
    return provider, mock_client


# ---------------------------------------------------------------------------
# Mapping tests (no HTTP)
# ---------------------------------------------------------------------------


def test_market_to_symbol_sol():
    assert _MARKET_SYMBOLS["SOL-PERP"] == "Crypto.SOL/USD"


def test_market_to_symbol_btc():
    assert _MARKET_SYMBOLS["BTC-PERP"] == "Crypto.BTC/USD"


def test_market_to_symbol_eth():
    assert _MARKET_SYMBOLS["ETH-PERP"] == "Crypto.ETH/USD"


def test_resolution_to_tv_1h():
    assert _RESOLUTION_MAP[3600] == "60"


def test_resolution_to_tv_1d():
    assert _RESOLUTION_MAP[86400] == "1D"


def test_resolution_to_tv_5m():
    assert _RESOLUTION_MAP[300] == "5"


# ---------------------------------------------------------------------------
# fetch_candles — happy path
# ---------------------------------------------------------------------------


def test_fetch_candles_returns_candle_objects():
    provider, mock_client = _make_provider(MOCK_TV_RESPONSE)

    candles = provider.fetch_candles("SOL-PERP", 3600, 1700000000, 1700007200)

    assert len(candles) == 2
    assert all(isinstance(c, Candle) for c in candles)


def test_fetch_candles_values():
    provider, _ = _make_provider(MOCK_TV_RESPONSE)
    candles = provider.fetch_candles("SOL-PERP", 3600, 1700000000, 1700007200)

    c0 = candles[0]
    assert c0.ts == 1700000000
    assert c0.open == 60.1
    assert c0.high == 61.0
    assert c0.low == 59.8
    assert c0.close == 60.5
    assert c0.volume == 1000.0
    assert c0.market == "SOL-PERP"
    assert c0.resolution_s == 3600
    assert c0.venue == "pyth"


def test_fetch_candles_second_bar():
    provider, _ = _make_provider(MOCK_TV_RESPONSE)
    candles = provider.fetch_candles("SOL-PERP", 3600, 1700000000, 1700007200)

    c1 = candles[1]
    assert c1.ts == 1700003600
    assert c1.open == 60.5
    assert c1.close == 60.9


def test_fetch_candles_calls_correct_endpoint():
    provider, mock_client = _make_provider(MOCK_TV_RESPONSE)
    provider.fetch_candles("SOL-PERP", 3600, 1700000000, 1700007200)

    mock_client.get.assert_called_once()
    call_kwargs = mock_client.get.call_args

    # Verify URL
    url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("url", call_kwargs[0][0])
    assert "benchmarks.pyth.network" in url
    assert "history" in url


def test_fetch_candles_passes_correct_params():
    provider, mock_client = _make_provider(MOCK_TV_RESPONSE)
    provider.fetch_candles("BTC-PERP", 86400, 1700000000, 1700086400)

    params = mock_client.get.call_args[1]["params"]
    assert params["symbol"] == "Crypto.BTC/USD"
    assert params["resolution"] == "1D"
    assert params["from"] == 1700000000
    assert params["to"] == 1700086400


# ---------------------------------------------------------------------------
# fetch_candles — no_data
# ---------------------------------------------------------------------------


def test_fetch_candles_no_data_returns_empty_list():
    provider, _ = _make_provider(MOCK_NO_DATA_RESPONSE)
    candles = provider.fetch_candles("SOL-PERP", 3600, 1700000000, 1700007200)
    assert candles == []


# ---------------------------------------------------------------------------
# Supported markets
# ---------------------------------------------------------------------------


def test_supported_markets_contains_sol_perp():
    provider, _ = _make_provider(MOCK_TV_RESPONSE)
    markets = provider.supported_markets()
    assert "SOL-PERP" in markets


def test_supported_markets_contains_btc_perp():
    provider, _ = _make_provider(MOCK_TV_RESPONSE)
    markets = provider.supported_markets()
    assert "BTC-PERP" in markets


def test_supported_markets_contains_eth_perp():
    provider, _ = _make_provider(MOCK_TV_RESPONSE)
    markets = provider.supported_markets()
    assert "ETH-PERP" in markets


def test_supported_data_types():
    """supported_data_types is a class-level list (backward-compat pattern)."""
    provider, _ = _make_provider(MOCK_TV_RESPONSE)
    # The class var shadows the base method; access directly as a list.
    data_types = (
        provider.supported_data_types()
        if callable(provider.supported_data_types)
        else provider.supported_data_types
    )
    assert "candles" in data_types


# ---------------------------------------------------------------------------
# Unknown market fallback
# ---------------------------------------------------------------------------


def test_unknown_market_fallback():
    """For an unknown market, the provider should try Crypto.FOO/USD."""
    provider, mock_client = _make_provider(MOCK_TV_RESPONSE)
    provider.fetch_candles("FOO-PERP", 3600, 1700000000, 1700007200)

    params = mock_client.get.call_args[1]["params"]
    assert params["symbol"] == "Crypto.FOO/USD"


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


def test_close_does_not_raise():
    provider, mock_client = _make_provider(MOCK_TV_RESPONSE)
    provider.close()  # should not raise


def test_close_calls_client_close_when_owned():
    """close() should call client.close() only when provider owns the client."""
    real_provider = PythCandleProvider()
    # Patch the underlying client close so we don't do real network ops
    real_provider._client = MagicMock()
    real_provider._owns_client = True
    real_provider.close()
    real_provider._client.close.assert_called_once()


def test_close_skips_client_close_when_not_owned():
    mock_client = MagicMock()
    provider = PythCandleProvider(client=mock_client)
    assert provider._owns_client is False
    provider.close()
    mock_client.close.assert_not_called()


# ---------------------------------------------------------------------------
# HTTP error handling
# ---------------------------------------------------------------------------


def test_fetch_candles_raises_on_http_error():
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.text = "Service unavailable"

    mock_client = MagicMock()
    mock_client.get.return_value = mock_resp

    provider = PythCandleProvider(client=mock_client)

    with pytest.raises(ValueError, match="503"):
        provider.fetch_candles("SOL-PERP", 3600, 1700000000, 1700007200)


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


def test_is_available_returns_true():
    provider, _ = _make_provider(MOCK_TV_RESPONSE)
    assert provider.is_available() is True
