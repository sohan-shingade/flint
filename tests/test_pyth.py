"""Tests for the Pyth oracle price provider."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from flint.providers.pyth import PythProvider, FEED_IDS

# -- fixtures / helpers -------------------------------------------------------

_SINGLE_PRICE_RESPONSE = {
    "binary": {"encoding": "hex", "data": ["deadbeef"]},
    "parsed": [
        {
            "id": "ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
            "price": {
                "price": "14523000000",
                "conf": "7200000",
                "expo": -8,
                "publish_time": 1711150000,
            },
            "ema_price": {
                "price": "14510000000",
                "conf": "6800000",
                "expo": -8,
                "publish_time": 1711150000,
            },
        }
    ],
}

_BATCH_PRICE_RESPONSE = {
    "binary": {"encoding": "hex", "data": ["deadbeef"]},
    "parsed": [
        {
            "id": "ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
            "price": {
                "price": "14523000000",
                "conf": "7200000",
                "expo": -8,
                "publish_time": 1711150000,
            },
            "ema_price": {
                "price": "14510000000",
                "conf": "6800000",
                "expo": -8,
                "publish_time": 1711150000,
            },
        },
        {
            "id": "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
            "price": {
                "price": "6700000000000",
                "conf": "3500000000",
                "expo": -8,
                "publish_time": 1711150001,
            },
            "ema_price": {
                "price": "6695000000000",
                "conf": "3200000000",
                "expo": -8,
                "publish_time": 1711150001,
            },
        },
    ],
}


def _mock_client(response_data: dict) -> httpx.Client:
    """Create a mock httpx.Client that returns *response_data* for any GET."""
    def _get(url, **kwargs):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = response_data
        return resp

    client = MagicMock(spec=httpx.Client)
    client.get = _get
    return client


# -- tests --------------------------------------------------------------------


class TestResolveFeed:
    def test_known_pair(self):
        fid = PythProvider.resolve_feed("SOL/USD")
        assert fid == FEED_IDS["SOL/USD"]
        assert fid.startswith("0x")

    def test_unknown_pair_raises(self):
        with pytest.raises(KeyError):
            PythProvider.resolve_feed("UNKNOWN/USD")


class TestFetchPrice:
    def test_single_price(self):
        client = _mock_client(_SINGLE_PRICE_RESPONSE)
        provider = PythProvider(client=client)

        result = provider.fetch_price("SOL/USD")

        assert result["pair"] == "SOL/USD"
        # 14523000000 * 10**-8 = 145.23
        assert result["price"] == pytest.approx(145.23)
        assert result["confidence"] == pytest.approx(0.072)
        assert result["ema_price"] == pytest.approx(145.10)
        assert result["ts"] == 1711150000
        assert result["expo"] == -8

    def test_empty_response_raises(self):
        client = _mock_client({"parsed": []})
        provider = PythProvider(client=client)

        with pytest.raises(ValueError, match="No price data"):
            provider.fetch_price("SOL/USD")


class TestFetchPrices:
    def test_batch_prices(self):
        client = _mock_client(_BATCH_PRICE_RESPONSE)
        provider = PythProvider(client=client)

        results = provider.fetch_prices(["SOL/USD", "BTC/USD"])

        assert len(results) == 2
        sol = results[0]
        btc = results[1]

        assert sol["pair"] == "SOL/USD"
        assert sol["price"] == pytest.approx(145.23)

        assert btc["pair"] == "BTC/USD"
        # 6700000000000 * 10**-8 = 67000.0
        assert btc["price"] == pytest.approx(67000.0)
        assert btc["ts"] == 1711150001


class TestFetchOraclePrices:
    def test_returns_oracle_price_models(self):
        client = _mock_client(_BATCH_PRICE_RESPONSE)
        provider = PythProvider(client=client)

        prices = provider.fetch_oracle_prices(["SOL/USD", "BTC/USD"])

        assert len(prices) == 2
        assert prices[0].market == "SOL/USD"
        assert prices[0].price == pytest.approx(145.23)
        assert prices[0].ts == 1711150000

        assert prices[1].market == "BTC/USD"
        assert prices[1].price == pytest.approx(67000.0)


class TestFeedIdsCoverage:
    def test_has_twenty_pairs(self):
        assert len(FEED_IDS) == 20

    def test_all_ids_are_hex(self):
        for pair, fid in FEED_IDS.items():
            assert fid.startswith("0x"), f"{pair} feed ID missing 0x prefix"
            # Should be 66 chars: 0x + 64 hex digits
            assert len(fid) == 66, f"{pair} feed ID has wrong length"


class TestClientLifecycle:
    def test_close_owned_client(self):
        mock = MagicMock(spec=httpx.Client)
        provider = PythProvider.__new__(PythProvider)
        provider._client = mock
        provider._owns_client = True
        provider.close()
        mock.close.assert_called_once()

    def test_close_external_client(self):
        mock = MagicMock(spec=httpx.Client)
        provider = PythProvider.__new__(PythProvider)
        provider._client = mock
        provider._owns_client = False
        provider.close()
        mock.close.assert_not_called()
