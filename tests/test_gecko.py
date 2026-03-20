"""Tests for the GeckoTerminal provider."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

from flint.providers.gecko import GeckoProvider, _resolution_to_timeframe

_SAMPLE_RESPONSE = {
    "data": {
        "id": "test-id",
        "type": "ohlcv_request_response",
        "attributes": {
            "ohlcv_list": [
                [1700006400, 100.0, 105.0, 98.0, 103.0, 50000.0],
                [1700002800, 99.0, 102.0, 97.0, 100.0, 45000.0],
                [1699999200, 95.0, 100.0, 94.0, 99.0, 40000.0],
            ]
        },
    }
}


def _make_mock_client(response_data: dict) -> httpx.Client:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = response_data

    client = MagicMock(spec=httpx.Client)
    client.get.return_value = mock_resp
    return client


def test_resolution_mapping():
    assert _resolution_to_timeframe(60) == "minute"
    assert _resolution_to_timeframe(300) == "minute"
    assert _resolution_to_timeframe(3600) == "hour"
    assert _resolution_to_timeframe(86400) == "day"


def test_fetch_candles_parses_ohlcv():
    client = _make_mock_client(_SAMPLE_RESPONSE)
    provider = GeckoProvider(client=client)

    candles = provider.fetch_candles(
        market="pool-address",
        resolution_s=3600,
        start_ts=1699999200,
        end_ts=1700006400,
    )

    assert len(candles) == 3
    assert candles[0].ts == 1699999200  # sorted ascending
    assert candles[0].open == 95.0
    assert candles[-1].close == 103.0
    assert candles[-1].volume == 50000.0


def test_fetch_candles_filters_by_time():
    client = _make_mock_client(_SAMPLE_RESPONSE)
    provider = GeckoProvider(client=client)

    candles = provider.fetch_candles(
        market="pool-address",
        resolution_s=3600,
        start_ts=1700000000,
        end_ts=1700006400,
    )

    # only 1700002800 and 1700006400 are >= 1700000000
    assert len(candles) == 2


def test_empty_response():
    empty = {"data": {"attributes": {"ohlcv_list": []}}}
    client = _make_mock_client(empty)
    provider = GeckoProvider(client=client)

    candles = provider.fetch_candles("pool", 3600, 0, 9999999999)
    assert candles == []
