"""Tests for the Raydium provider."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from flint.providers.raydium import RaydiumProvider

# ---------------------------------------------------------------------------
# Sample API responses
# ---------------------------------------------------------------------------

_POOL_LIST_RESPONSE = {
    "data": {
        "data": [
            {
                "id": "pool_abc123",
                "type": "Standard",
                "mintA": {
                    "address": "So11111111111111111111111111111111111111112",
                    "symbol": "SOL",
                },
                "mintB": {
                    "address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                    "symbol": "USDC",
                },
                "tvl": 12_500_000.0,
                "day": {"volume": 8_300_000.0, "volumeFee": 24_900.0},
                "feeRate": 0.0025,
                "lpMint": {"address": "LPmint111111111111111111111111111111111111"},
            },
            {
                "id": "pool_def456",
                "type": "Concentrated",
                "mintA": {
                    "address": "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
                    "symbol": "mSOL",
                },
                "mintB": {
                    "address": "So11111111111111111111111111111111111111112",
                    "symbol": "SOL",
                },
                "tvl": 4_200_000.0,
                "day": {"volume": 1_100_000.0, "volumeFee": 3_300.0},
                "feeRate": 0.0004,
                "lpMint": {"address": "LPmint222222222222222222222222222222222222"},
            },
        ]
    }
}

_POOL_BY_ID_RESPONSE = {
    "data": [
        {
            "id": "pool_abc123",
            "type": "Standard",
            "mintA": {
                "address": "So11111111111111111111111111111111111111112",
                "symbol": "SOL",
            },
            "mintB": {
                "address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "symbol": "USDC",
            },
            "tvl": 12_500_000.0,
            "day": {"volume": 8_300_000.0, "volumeFee": 24_900.0},
            "feeRate": 0.0025,
            "lpMint": {"address": "LPmint111111111111111111111111111111111111"},
        }
    ]
}

_EMPTY_POOL_RESPONSE = {"data": []}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_client(response: dict) -> httpx.Client:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = response

    client = MagicMock(spec=httpx.Client)
    client.get.return_value = mock_resp
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFetchPools:
    def test_returns_normalized_pools(self):
        client = _mock_client(_POOL_LIST_RESPONSE)
        provider = RaydiumProvider(client=client)

        pools = provider.fetch_pools()

        assert len(pools) == 2
        p = pools[0]
        assert p["pool_id"] == "pool_abc123"
        assert p["pool_type"] == "Standard"
        assert p["token_a_mint"] == "So11111111111111111111111111111111111111112"
        assert p["token_a_symbol"] == "SOL"
        assert p["token_b_symbol"] == "USDC"
        assert p["tvl"] == 12_500_000.0
        assert p["volume_24h"] == 8_300_000.0
        assert p["fee_24h"] == 24_900.0
        assert p["fee_rate"] == 0.0025
        assert p["lp_mint"] == "LPmint111111111111111111111111111111111111"

    def test_passes_query_params(self):
        client = _mock_client(_POOL_LIST_RESPONSE)
        provider = RaydiumProvider(client=client)

        provider.fetch_pools(
            token_mint="So11111111111111111111111111111111111111112",
            pool_type="concentrated",
            sort_by="volume",
            limit=5,
        )

        _, kwargs = client.get.call_args
        params = kwargs["params"]
        assert params["mint1"] == "So11111111111111111111111111111111111111112"
        assert params["poolType"] == "concentrated"
        assert params["sortField"] == "volume"
        assert params["pageSize"] == "5"

    def test_no_mint_filter_omits_param(self):
        client = _mock_client(_POOL_LIST_RESPONSE)
        provider = RaydiumProvider(client=client)

        provider.fetch_pools()

        _, kwargs = client.get.call_args
        params = kwargs["params"]
        assert "mint1" not in params


class TestFetchPoolById:
    def test_returns_normalized_pool(self):
        client = _mock_client(_POOL_BY_ID_RESPONSE)
        provider = RaydiumProvider(client=client)

        pool = provider.fetch_pool_by_id("pool_abc123")

        assert pool is not None
        assert pool["pool_id"] == "pool_abc123"
        assert pool["token_a_symbol"] == "SOL"
        assert pool["tvl"] == 12_500_000.0

    def test_returns_none_when_empty(self):
        client = _mock_client(_EMPTY_POOL_RESPONSE)
        provider = RaydiumProvider(client=client)

        pool = provider.fetch_pool_by_id("nonexistent")

        assert pool is None

    def test_passes_pool_id_param(self):
        client = _mock_client(_POOL_BY_ID_RESPONSE)
        provider = RaydiumProvider(client=client)

        provider.fetch_pool_by_id("pool_abc123")

        _, kwargs = client.get.call_args
        assert kwargs["params"]["ids"] == "pool_abc123"


class TestFetchPoolVolume:
    def test_returns_dex_volume(self):
        client = _mock_client(_POOL_BY_ID_RESPONSE)
        provider = RaydiumProvider(client=client)

        vol = provider.fetch_pool_volume("pool_abc123")

        assert vol.dex == "raydium"
        assert vol.market == "SOL/USDC"
        assert vol.volume_usd == 8_300_000.0

    def test_returns_zero_volume_when_pool_missing(self):
        client = _mock_client(_EMPTY_POOL_RESPONSE)
        provider = RaydiumProvider(client=client)

        vol = provider.fetch_pool_volume("nonexistent")

        assert vol.dex == "raydium"
        assert vol.volume_usd == 0.0
        assert vol.market == "nonexistent"


class TestFetchTopPoolsVolume:
    def test_returns_list_of_dex_volumes(self):
        client = _mock_client(_POOL_LIST_RESPONSE)
        provider = RaydiumProvider(client=client)

        vols = provider.fetch_top_pools_volume(limit=20)

        assert len(vols) == 2
        assert vols[0].dex == "raydium"
        assert vols[0].market == "SOL/USDC"
        assert vols[0].volume_usd == 8_300_000.0
        assert vols[1].market == "mSOL/SOL"
        assert vols[1].volume_usd == 1_100_000.0


class TestProviderMetadata:
    def test_name_and_data_types(self):
        assert RaydiumProvider.name == "raydium"
        assert "pool_data" in RaydiumProvider.supported_data_types
        assert "dex_volume" in RaydiumProvider.supported_data_types

    def test_close_only_closes_owned_client(self):
        external = MagicMock(spec=httpx.Client)
        provider = RaydiumProvider(client=external)
        provider.close()
        external.close.assert_not_called()
