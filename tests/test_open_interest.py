"""Tests for the Drift open interest provider."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from flint.providers.open_interest import DriftOpenInterestProvider, _BASE_PRECISION

# -- fixtures / helpers -------------------------------------------------------

_OI_RESPONSE = [
    {
        "ts": 1711150000,
        "longOI": "5000000000000",   # 5000.0 after /1e9
        "shortOI": "3500000000000",  # 3500.0 after /1e9
    },
    {
        "ts": 1711153600,
        "longOI": "5200000000000",   # 5200.0
        "shortOI": "3600000000000",  # 3600.0
    },
]

_OI_WRAPPED_RESPONSE = {
    "data": [
        {
            "ts": 1711150000,
            "longOI": "5000000000000",
            "shortOI": "3500000000000",
        },
    ],
}

_OI_CURRENT_RESPONSE = [
    {
        "ts": 1711159000,
        "longOI": "6000000000000",   # 6000.0
        "shortOI": "4000000000000",  # 4000.0
    },
]


def _mock_client(url_to_response: dict) -> httpx.Client:
    """Create a mock httpx.Client that dispatches by URL substring."""
    def _get(url, **kwargs):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        for prefix, data in url_to_response.items():
            if prefix in str(url):
                resp.json.return_value = data
                return resp
        resp.json.return_value = []
        return resp

    client = MagicMock(spec=httpx.Client)
    client.get = _get
    return client


# -- tests --------------------------------------------------------------------


class TestFetchOpenInterest:
    def test_returns_list_of_models(self):
        client = _mock_client({"openInterest": _OI_RESPONSE})
        provider = DriftOpenInterestProvider(client=client)

        results = provider.fetch_open_interest("SOL-PERP", 1711140000, 1711160000)

        assert len(results) == 2

        r0 = results[0]
        assert r0.market == "SOL-PERP"
        assert r0.ts == 1711150000
        assert r0.long_oi == pytest.approx(5000.0)
        assert r0.short_oi == pytest.approx(3500.0)
        assert r0.net_oi == pytest.approx(1500.0)
        assert r0.total_oi == pytest.approx(8500.0)

        r1 = results[1]
        assert r1.ts == 1711153600
        assert r1.long_oi == pytest.approx(5200.0)
        assert r1.short_oi == pytest.approx(3600.0)

    def test_wrapped_response_format(self):
        """API sometimes returns {data: [...]} instead of bare list."""
        client = _mock_client({"openInterest": _OI_WRAPPED_RESPONSE})
        provider = DriftOpenInterestProvider(client=client)

        results = provider.fetch_open_interest("SOL-PERP", 1711140000, 1711160000)

        assert len(results) == 1
        assert results[0].long_oi == pytest.approx(5000.0)


class TestFetchCurrentOI:
    def test_returns_latest_snapshot(self):
        client = _mock_client({"openInterest": _OI_CURRENT_RESPONSE})
        provider = DriftOpenInterestProvider(client=client)

        oi = provider.fetch_current_oi("SOL-PERP")

        assert oi is not None
        assert oi.market == "SOL-PERP"
        assert oi.ts == 1711159000
        assert oi.long_oi == pytest.approx(6000.0)
        assert oi.short_oi == pytest.approx(4000.0)

    def test_returns_none_when_empty(self):
        client = _mock_client({"openInterest": []})
        provider = DriftOpenInterestProvider(client=client)

        oi = provider.fetch_current_oi("SOL-PERP")
        assert oi is None


class TestBasePrecision:
    def test_precision_value(self):
        assert _BASE_PRECISION == 1e9

    def test_division_accuracy(self):
        raw = 1234567890000
        result = raw / _BASE_PRECISION
        assert result == pytest.approx(1234.56789)


class TestClientLifecycle:
    def test_close_owned_client(self):
        mock = MagicMock(spec=httpx.Client)
        provider = DriftOpenInterestProvider.__new__(DriftOpenInterestProvider)
        provider._client = mock
        provider._owns_client = True
        provider.close()
        mock.close.assert_called_once()

    def test_close_external_client(self):
        mock = MagicMock(spec=httpx.Client)
        provider = DriftOpenInterestProvider.__new__(DriftOpenInterestProvider)
        provider._client = mock
        provider._owns_client = False
        provider.close()
        mock.close.assert_not_called()
