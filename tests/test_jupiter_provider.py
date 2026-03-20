"""Tests for the Jupiter provider."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from flint.providers.jupiter import JupiterProvider, SOL_MINT, USDC_MINT

_QUOTE_RESPONSE = {
    "inputMint": SOL_MINT,
    "inAmount": "1000000000",
    "outputMint": USDC_MINT,
    "outAmount": "89500000",  # 89.5 USDC
    "otherAmountThreshold": "89055000",
    "swapMode": "ExactIn",
    "slippageBps": 50,
    "priceImpactPct": "0.001",
    "routePlan": [
        {
            "swapInfo": {
                "ammKey": "pool1",
                "label": "Raydium",
                "inputMint": SOL_MINT,
                "outputMint": USDC_MINT,
                "inAmount": "1000000000",
                "outAmount": "89500000",
                "feeAmount": "25000",
                "feeMint": USDC_MINT,
            },
            "percent": 100,
        }
    ],
}


def _mock_client(response: dict) -> httpx.Client:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = response

    client = MagicMock(spec=httpx.Client)
    client.get.return_value = mock_resp
    return client


def test_get_quote():
    client = _mock_client(_QUOTE_RESPONSE)
    provider = JupiterProvider(client=client)

    quote = provider.get_quote(SOL_MINT, USDC_MINT, 1_000_000_000)
    assert quote["outAmount"] == "89500000"
    assert len(quote["routePlan"]) == 1


def test_get_price_sol_usdc():
    client = _mock_client(_QUOTE_RESPONSE)
    provider = JupiterProvider(client=client)

    price = provider.get_price_sol_usdc(1.0)
    assert price == pytest.approx(89.5)


def test_get_swap_routes():
    client = _mock_client(_QUOTE_RESPONSE)
    provider = JupiterProvider(client=client)

    routes = provider.get_swap_routes(SOL_MINT, USDC_MINT, 1_000_000_000)
    assert len(routes) == 1
    assert routes[0]["swapInfo"]["label"] == "Raydium"
    assert routes[0]["percent"] == 100
