"""Tests for the Birdeye provider."""
from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock

import httpx
import pytest

from flint.providers.birdeye import (
    BirdeyeProvider,
    KNOWN_TOKENS,
    _TIMEFRAMES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_ohlcv_response():
    return {
        "data": {
            "items": [
                {"unixTime": 1700000000, "o": 100.0, "h": 105.0, "l": 99.0, "c": 103.0, "v": 50000.0},
                {"unixTime": 1700003600, "o": 103.0, "h": 106.0, "l": 102.0, "c": 104.0, "v": 45000.0},
            ]
        },
        "success": True,
    }


def _make_mock_client(response_data: dict, status_code: int = 200) -> httpx.Client:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = response_data
    mock_resp.text = ""

    client = MagicMock(spec=httpx.Client)
    client.get.return_value = mock_resp
    return client


# ---------------------------------------------------------------------------
# Availability tests
# ---------------------------------------------------------------------------

def test_birdeye_requires_api_key():
    p = BirdeyeProvider(api_key="")
    assert not p.is_available()


def test_birdeye_available_with_key():
    p = BirdeyeProvider(api_key="test-key")
    assert p.is_available()


def test_birdeye_name_and_flags():
    p = BirdeyeProvider(api_key="x")
    assert p.name == "birdeye"
    assert p.requires_api_key is True


# ---------------------------------------------------------------------------
# Supported data types
# ---------------------------------------------------------------------------

def test_birdeye_supported_data_types():
    p = BirdeyeProvider(api_key="test")
    dtypes = p.supported_data_types()
    assert "candles" in dtypes
    assert "token_metadata" in dtypes
    assert "price" in dtypes


# ---------------------------------------------------------------------------
# fetch_candles
# ---------------------------------------------------------------------------

def test_birdeye_fetch_candles():
    client = _make_mock_client(_mock_ohlcv_response())
    p = BirdeyeProvider(api_key="test-key", client=client)

    candles = p.fetch_candles(
        token_address="So11111111111111111111111111111111111111112",
        resolution_s=3600,
        start_ts=1700000000,
        end_ts=1700007200,
    )

    assert len(candles) == 2
    assert candles[0].ts == 1700000000
    assert candles[0].open == 100.0
    assert candles[0].high == 105.0
    assert candles[0].low == 99.0
    assert candles[0].close == 103.0
    assert candles[0].volume == 50000.0
    assert candles[0].market == "So11111111111111111111111111111111111111112"
    assert candles[0].resolution_s == 3600

    assert candles[1].ts == 1700003600
    assert candles[1].open == 103.0


def test_birdeye_fetch_candles_sends_correct_headers():
    client = _make_mock_client(_mock_ohlcv_response())
    p = BirdeyeProvider(api_key="my-secret-key", client=client)

    p.fetch_candles(
        token_address="So11111111111111111111111111111111111111112",
        resolution_s=3600,
        start_ts=1700000000,
        end_ts=1700007200,
    )

    call_kwargs = client.get.call_args
    headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
    assert headers["X-API-KEY"] == "my-secret-key"
    assert headers["x-chain"] == "solana"


def test_birdeye_fetch_candles_sends_correct_params():
    client = _make_mock_client(_mock_ohlcv_response())
    p = BirdeyeProvider(api_key="key", client=client)

    p.fetch_candles(
        token_address="TOKEN_ADDR",
        resolution_s=3600,
        start_ts=1700000000,
        end_ts=1700007200,
    )

    # Check the first call's params (call_args_list[0])
    first_call = client.get.call_args_list[0]
    params = first_call.kwargs.get("params") or first_call[1].get("params", {})
    assert params["address"] == "TOKEN_ADDR"
    assert params["type"] == "1H"
    assert params["time_from"] == 1700000000


def test_birdeye_fetch_candles_empty_response():
    empty_resp = {"data": {"items": []}, "success": True}
    client = _make_mock_client(empty_resp)
    p = BirdeyeProvider(api_key="key", client=client)

    candles = p.fetch_candles("ADDR", 3600, 1700000000, 1700007200)
    assert candles == []


def test_birdeye_fetch_candles_filters_by_time():
    resp = {
        "data": {
            "items": [
                {"unixTime": 1699990000, "o": 90.0, "h": 91.0, "l": 89.0, "c": 90.5, "v": 10000.0},
                {"unixTime": 1700000000, "o": 100.0, "h": 105.0, "l": 99.0, "c": 103.0, "v": 50000.0},
                {"unixTime": 1700099999, "o": 110.0, "h": 115.0, "l": 109.0, "c": 112.0, "v": 60000.0},
            ]
        },
        "success": True,
    }
    client = _make_mock_client(resp)
    p = BirdeyeProvider(api_key="key", client=client)

    candles = p.fetch_candles("ADDR", 3600, 1700000000, 1700010000)
    # Only ts=1700000000 is within range; 1699990000 is before, 1700099999 is after
    assert len(candles) == 1
    assert candles[0].ts == 1700000000


def test_birdeye_fetch_candles_deduplicates():
    resp = {
        "data": {
            "items": [
                {"unixTime": 1700000000, "o": 100.0, "h": 105.0, "l": 99.0, "c": 103.0, "v": 50000.0},
                {"unixTime": 1700000000, "o": 100.0, "h": 105.0, "l": 99.0, "c": 103.0, "v": 50000.0},
                {"unixTime": 1700003600, "o": 103.0, "h": 106.0, "l": 102.0, "c": 104.0, "v": 45000.0},
            ]
        },
        "success": True,
    }
    client = _make_mock_client(resp)
    p = BirdeyeProvider(api_key="key", client=client)

    candles = p.fetch_candles("ADDR", 3600, 1700000000, 1700007200)
    assert len(candles) == 2  # deduped


def test_birdeye_fetch_candles_sorted():
    resp = {
        "data": {
            "items": [
                {"unixTime": 1700003600, "o": 103.0, "h": 106.0, "l": 102.0, "c": 104.0, "v": 45000.0},
                {"unixTime": 1700000000, "o": 100.0, "h": 105.0, "l": 99.0, "c": 103.0, "v": 50000.0},
            ]
        },
        "success": True,
    }
    client = _make_mock_client(resp)
    p = BirdeyeProvider(api_key="key", client=client)

    candles = p.fetch_candles("ADDR", 3600, 1700000000, 1700007200)
    assert candles[0].ts < candles[1].ts


def test_birdeye_fetch_candles_non_200_stops():
    client = _make_mock_client({}, status_code=429)
    p = BirdeyeProvider(api_key="key", client=client)

    candles = p.fetch_candles("ADDR", 3600, 1700000000, 1700007200)
    assert candles == []


def test_birdeye_fetch_candles_exception_stops():
    client = MagicMock(spec=httpx.Client)
    client.get.side_effect = httpx.ConnectError("connection refused")
    p = BirdeyeProvider(api_key="key", client=client)

    candles = p.fetch_candles("ADDR", 3600, 1700000000, 1700007200)
    assert candles == []


def test_birdeye_fetch_candles_unsupported_resolution():
    """Unsupported resolution should snap to closest supported one."""
    client = _make_mock_client(_mock_ohlcv_response())
    p = BirdeyeProvider(api_key="key", client=client)

    candles = p.fetch_candles("ADDR", 120, 1700000000, 1700007200)
    # 120s is closest to 60s, so should use "1m" and resolution_s=60
    assert all(c.resolution_s == 60 for c in candles)


# ---------------------------------------------------------------------------
# fetch_token_metadata
# ---------------------------------------------------------------------------

def test_birdeye_token_metadata():
    resp = {
        "data": {"symbol": "SOL", "name": "Solana", "decimals": 9, "mc": 50_000_000_000},
        "success": True,
    }
    client = _make_mock_client(resp)
    p = BirdeyeProvider(api_key="test-key", client=client)

    meta = p.fetch_token_metadata("So11111111111111111111111111111111111111112")
    assert meta["symbol"] == "SOL"
    assert meta["name"] == "Solana"
    assert meta["decimals"] == 9


def test_birdeye_token_metadata_failure():
    client = _make_mock_client({}, status_code=500)
    p = BirdeyeProvider(api_key="key", client=client)

    meta = p.fetch_token_metadata("ADDR")
    assert meta == {}


def test_birdeye_token_metadata_exception():
    client = MagicMock(spec=httpx.Client)
    client.get.side_effect = httpx.ConnectError("timeout")
    p = BirdeyeProvider(api_key="key", client=client)

    meta = p.fetch_token_metadata("ADDR")
    assert meta == {}


# ---------------------------------------------------------------------------
# fetch_price
# ---------------------------------------------------------------------------

def test_birdeye_fetch_price():
    resp = {"data": {"value": 150.42}, "success": True}
    client = _make_mock_client(resp)
    p = BirdeyeProvider(api_key="key", client=client)

    price = p.fetch_price("So11111111111111111111111111111111111111112")
    assert price == 150.42


def test_birdeye_fetch_price_failure():
    client = _make_mock_client({}, status_code=500)
    p = BirdeyeProvider(api_key="key", client=client)

    price = p.fetch_price("ADDR")
    assert price is None


def test_birdeye_fetch_price_exception():
    client = MagicMock(spec=httpx.Client)
    client.get.side_effect = httpx.ConnectError("refused")
    p = BirdeyeProvider(api_key="key", client=client)

    price = p.fetch_price("ADDR")
    assert price is None


# ---------------------------------------------------------------------------
# resolve_token
# ---------------------------------------------------------------------------

def test_resolve_known_token():
    p = BirdeyeProvider(api_key="key")
    assert p.resolve_token("SOL") == KNOWN_TOKENS["SOL"]
    assert p.resolve_token("sol") == KNOWN_TOKENS["SOL"]  # case-insensitive
    assert p.resolve_token("BONK") == KNOWN_TOKENS["BONK"]


def test_resolve_unknown_token():
    p = BirdeyeProvider(api_key="key")
    assert p.resolve_token("UNKNOWN_TOKEN") is None


# ---------------------------------------------------------------------------
# KNOWN_TOKENS sanity
# ---------------------------------------------------------------------------

def test_known_tokens_has_expected_entries():
    expected = {"SOL", "USDC", "USDT", "BONK", "JUP", "WIF", "DRIFT", "RAY", "PYTH", "RENDER"}
    assert expected == set(KNOWN_TOKENS.keys())


# ---------------------------------------------------------------------------
# Timeframe mapping
# ---------------------------------------------------------------------------

def test_timeframe_mapping():
    assert _TIMEFRAMES[60] == "1m"
    assert _TIMEFRAMES[300] == "5m"
    assert _TIMEFRAMES[900] == "15m"
    assert _TIMEFRAMES[1800] == "30m"
    assert _TIMEFRAMES[3600] == "1H"
    assert _TIMEFRAMES[14400] == "4H"
    assert _TIMEFRAMES[86400] == "1D"
    assert _TIMEFRAMES[604800] == "1W"


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------

def test_close_owned_client():
    p = BirdeyeProvider(api_key="key")
    p.close()
    # Should not raise; internal client is closed


def test_close_external_client_not_closed():
    client = MagicMock(spec=httpx.Client)
    p = BirdeyeProvider(api_key="key", client=client)
    p.close()
    client.close.assert_not_called()
