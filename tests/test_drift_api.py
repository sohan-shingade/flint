"""Tests for the Drift Data API provider."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

from flint.providers.drift_api import DriftDataProvider, _PRICE_PRECISION, _BASE_PRECISION

_FUNDING_RESPONSE = {
    "fundingRates": [
        {
            "txSig": "sig1",
            "slot": 401001868,
            "ts": "1771383600",
            "recordId": "28756",
            "marketIndex": 0,
            "fundingRate": "-1889875",
            "cumulativeFundingRateLong": "45306173178",
            "cumulativeFundingRateShort": "45099973503",
            "oraclePriceTwap": "84642173",
            "markPriceTwap": "84579888",
            "fundingRateLong": "-1889875",
            "fundingRateShort": "-1889875",
            "periodRevenue": "1537154433",
            "baseAssetAmountWithAmm": "-4622930000000",
            "baseAssetAmountWithUnsettledLp": "0",
        }
    ]
}

_L2_RESPONSE = {
    "bids": [
        {"price": "89415545", "size": "520000000", "sources": {"dlob": "520000000"}},
        {"price": "89399400", "size": "55850000000", "sources": {"vamm": "55850000000"}},
    ],
    "asks": [
        {"price": "89499800", "size": "55850000000", "sources": {"vamm": "55850000000"}},
        {"price": "89500200", "size": "111700000000", "sources": {"vamm": "111700000000"}},
    ],
    "slot": 400000000,
}


def _mock_client(url_to_response: dict) -> httpx.Client:
    """Mock client that returns different responses per URL prefix."""
    def _get(url, **kwargs):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        for prefix, data in url_to_response.items():
            if prefix in str(url):
                resp.json.return_value = data
                return resp
        resp.json.return_value = {}
        return resp

    client = MagicMock(spec=httpx.Client)
    client.get = _get
    return client


def test_fetch_funding_rates():
    client = _mock_client({"data.api.drift.trade": _FUNDING_RESPONSE})
    provider = DriftDataProvider(client=client)

    rates = provider.fetch_funding_rates(market_index=0, market_name="SOL-PERP")
    assert len(rates) == 1

    r = rates[0]
    assert r.market == "SOL-PERP"
    assert r.ts == 1771383600
    # rate = raw_rate / oracle_price (dollar-denominated → fractional)
    raw_rate = -1889875 / _PRICE_PRECISION
    oracle = 84642173 / _PRICE_PRECISION
    assert r.rate == pytest.approx(raw_rate / oracle)
    assert r.oracle_price == pytest.approx(oracle)
    assert r.mark_price == pytest.approx(84579888 / _PRICE_PRECISION)
    assert r.slot == 401001868


def test_fetch_orderbook():
    client = _mock_client({"dlob.drift.trade": _L2_RESPONSE})
    provider = DriftDataProvider(client=client)

    ob = provider.fetch_orderbook("SOL-PERP", depth=2)
    assert ob.market == "SOL-PERP"
    assert len(ob.bids) == 2
    assert len(ob.asks) == 2
    assert ob.bids[0].price == pytest.approx(89415545 / _PRICE_PRECISION)
    assert ob.bids[0].size == pytest.approx(520000000 / _BASE_PRECISION)
    assert ob.asks[0].price == pytest.approx(89499800 / _PRICE_PRECISION)
    assert ob.slot == 400000000


def test_fetch_mid_price():
    client = _mock_client({"dlob.drift.trade": _L2_RESPONSE})
    provider = DriftDataProvider(client=client)

    mid = provider.fetch_mid_price("SOL-PERP")
    expected = (89415545 / _PRICE_PRECISION + 89499800 / _PRICE_PRECISION) / 2
    assert mid == pytest.approx(expected)


def test_fetch_orderbook_empty():
    client = _mock_client({"dlob.drift.trade": {"bids": [], "asks": [], "slot": 0}})
    provider = DriftDataProvider(client=client)

    with pytest.raises(ValueError, match="Empty orderbook"):
        provider.fetch_mid_price("SOL-PERP")
