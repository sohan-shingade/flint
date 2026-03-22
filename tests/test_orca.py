"""Tests for the Orca provider."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from flint.providers.orca import OrcaProvider

# ---------------------------------------------------------------------------
# Sample API responses
# ---------------------------------------------------------------------------

_WHIRLPOOL_LIST_RESPONSE = {
    "whirlpools": [
        {
            "address": "whirl_sol_usdc",
            "tokenA": {
                "mint": "So11111111111111111111111111111111111111112",
                "symbol": "SOL",
            },
            "tokenB": {
                "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "symbol": "USDC",
            },
            "tvl": 9_800_000.0,
            "volume": {"day": 5_400_000.0},
            "lpFeeRate": 0.003,
            "price": 135.42,
            "tickCurrentIndex": -22000,
        },
        {
            "address": "whirl_msol_sol",
            "tokenA": {
                "mint": "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
                "symbol": "mSOL",
            },
            "tokenB": {
                "mint": "So11111111111111111111111111111111111111112",
                "symbol": "SOL",
            },
            "tvl": 3_200_000.0,
            "volume": {"day": 900_000.0},
            "lpFeeRate": 0.0004,
            "price": 1.05,
            "tickCurrentIndex": 487,
        },
        {
            "address": "whirl_bonk_usdc",
            "tokenA": {
                "mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
                "symbol": "BONK",
            },
            "tokenB": {
                "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "symbol": "USDC",
            },
            "tvl": 800_000.0,
            "volume": {"day": 350_000.0},
            "lpFeeRate": 0.01,
            "price": 0.0000154,
            "tickCurrentIndex": -110000,
        },
    ]
}

_SINGLE_WHIRLPOOL_RESPONSE = {
    "address": "whirl_sol_usdc",
    "tokenA": {
        "mint": "So11111111111111111111111111111111111111112",
        "symbol": "SOL",
    },
    "tokenB": {
        "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "symbol": "USDC",
    },
    "tvl": 9_800_000.0,
    "volume": {"day": 5_400_000.0},
    "lpFeeRate": 0.003,
    "price": 135.42,
    "tickCurrentIndex": -22000,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_client(response: dict, status_code: int = 200) -> httpx.Client:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = response

    client = MagicMock(spec=httpx.Client)
    client.get.return_value = mock_resp
    return client


def _mock_client_404() -> httpx.Client:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 404

    client = MagicMock(spec=httpx.Client)
    client.get.return_value = mock_resp
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFetchWhirlpools:
    def test_returns_normalized_pools(self):
        client = _mock_client(_WHIRLPOOL_LIST_RESPONSE)
        provider = OrcaProvider(client=client)

        pools = provider.fetch_whirlpools()

        assert len(pools) == 3
        p = pools[0]
        assert p["pool_address"] == "whirl_sol_usdc"
        assert p["token_a_mint"] == "So11111111111111111111111111111111111111112"
        assert p["token_a_symbol"] == "SOL"
        assert p["token_b_symbol"] == "USDC"
        assert p["tvl"] == 9_800_000.0
        assert p["volume_24h"] == 5_400_000.0
        assert p["fee_rate"] == 0.003
        assert p["price"] == 135.42
        assert p["tick_current"] == -22000

    def test_sorted_by_tvl_desc(self):
        client = _mock_client(_WHIRLPOOL_LIST_RESPONSE)
        provider = OrcaProvider(client=client)

        pools = provider.fetch_whirlpools()

        tvls = [p["tvl"] for p in pools]
        assert tvls == sorted(tvls, reverse=True)

    def test_limit_truncates(self):
        client = _mock_client(_WHIRLPOOL_LIST_RESPONSE)
        provider = OrcaProvider(client=client)

        pools = provider.fetch_whirlpools(limit=2)

        assert len(pools) == 2
        # Should keep top-2 by TVL
        assert pools[0]["pool_address"] == "whirl_sol_usdc"
        assert pools[1]["pool_address"] == "whirl_msol_sol"

    def test_filter_by_token_mint(self):
        client = _mock_client(_WHIRLPOOL_LIST_RESPONSE)
        provider = OrcaProvider(client=client)

        bonk_mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
        pools = provider.fetch_whirlpools(token_mint=bonk_mint)

        assert len(pools) == 1
        assert pools[0]["token_a_symbol"] == "BONK"

    def test_empty_response(self):
        client = _mock_client({"whirlpools": []})
        provider = OrcaProvider(client=client)

        pools = provider.fetch_whirlpools()

        assert pools == []


class TestFetchPoolByAddress:
    def test_returns_normalized_pool(self):
        client = _mock_client(_SINGLE_WHIRLPOOL_RESPONSE)
        provider = OrcaProvider(client=client)

        pool = provider.fetch_pool_by_address("whirl_sol_usdc")

        assert pool is not None
        assert pool["pool_address"] == "whirl_sol_usdc"
        assert pool["token_a_symbol"] == "SOL"
        assert pool["tvl"] == 9_800_000.0
        assert pool["price"] == 135.42

    def test_returns_none_on_404(self):
        client = _mock_client_404()
        provider = OrcaProvider(client=client)

        pool = provider.fetch_pool_by_address("nonexistent")

        assert pool is None

    def test_calls_correct_endpoint(self):
        client = _mock_client(_SINGLE_WHIRLPOOL_RESPONSE)
        provider = OrcaProvider(client=client)

        provider.fetch_pool_by_address("whirl_sol_usdc")

        args, _ = client.get.call_args
        assert args[0] == "https://api.mainnet.orca.so/v1/whirlpool/whirl_sol_usdc"


class TestFetchTopPoolsVolume:
    def test_returns_list_of_dex_volumes(self):
        client = _mock_client(_WHIRLPOOL_LIST_RESPONSE)
        provider = OrcaProvider(client=client)

        vols = provider.fetch_top_pools_volume(limit=20)

        assert len(vols) == 3
        assert vols[0].dex == "orca"
        assert vols[0].market == "SOL/USDC"
        assert vols[0].volume_usd == 5_400_000.0
        assert vols[1].market == "mSOL/SOL"
        assert vols[1].volume_usd == 900_000.0
        assert vols[2].market == "BONK/USDC"

    def test_respects_limit(self):
        client = _mock_client(_WHIRLPOOL_LIST_RESPONSE)
        provider = OrcaProvider(client=client)

        vols = provider.fetch_top_pools_volume(limit=1)

        assert len(vols) == 1
        assert vols[0].market == "SOL/USDC"


class TestProviderMetadata:
    def test_name_and_data_types(self):
        assert OrcaProvider.name == "orca"
        assert "pool_data" in OrcaProvider.supported_data_types
        assert "dex_volume" in OrcaProvider.supported_data_types

    def test_close_only_closes_owned_client(self):
        external = MagicMock(spec=httpx.Client)
        provider = OrcaProvider(client=external)
        provider.close()
        external.close.assert_not_called()
