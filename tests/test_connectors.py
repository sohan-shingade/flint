"""Tests for protocol connectors."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import httpx
import pytest

from flint.connectors.drift.client import DriftConnector, _KNOWN_PERP_MARKETS
from flint.connectors.drift.types import DriftUserPosition
from flint.connectors.drift.stream import DriftPoller
from flint.connectors.jupiter.client import JupiterConnector
from flint.models import Order, Side, OrderType
from flint.providers.drift_api import _PRICE_PRECISION


# -- Drift types ---------------------------------------------------------------

def test_drift_perp_market():
    m = _KNOWN_PERP_MARKETS[0]
    assert m.symbol == "SOL-PERP"
    assert m.market_index == 0
    assert m.taker_fee == 0.001


def test_drift_user_position_long():
    pos = DriftUserPosition(
        market_index=0,
        market_symbol="SOL-PERP",
        base_asset_amount=10.0,
        quote_entry_amount=-1300.0,
        quote_break_even_amount=-1295.0,
        last_cumulative_funding_rate=0.0,
        open_orders=0,
        unsettled_pnl=0.0,
    )
    assert pos.is_long
    assert pos.size == 10.0
    assert pos.entry_price == pytest.approx(130.0)
    assert pos.unrealised_pnl(140.0) == pytest.approx(100.0)


def test_drift_user_position_short():
    pos = DriftUserPosition(
        market_index=0,
        market_symbol="SOL-PERP",
        base_asset_amount=-5.0,
        quote_entry_amount=500.0,
        quote_break_even_amount=498.0,
        last_cumulative_funding_rate=0.0,
        open_orders=0,
        unsettled_pnl=0.0,
    )
    assert not pos.is_long
    assert pos.size == 5.0
    assert pos.entry_price == pytest.approx(100.0)
    # short from 100, price goes to 90 → profit
    assert pos.unrealised_pnl(90.0) == pytest.approx(50.0)


# -- Drift connector -----------------------------------------------------------

_L2_RESPONSE = {
    "bids": [{"price": "89000000", "size": "1000000000"}],
    "asks": [{"price": "89100000", "size": "1000000000"}],
    "slot": 400000000,
}

_FUNDING_RESPONSE = {
    "fundingRates": [
        {
            "txSig": "sig1", "slot": 400000000, "ts": "1700000000",
            "recordId": "1", "marketIndex": 0,
            "fundingRate": "-1000000",
            "oraclePriceTwap": "89000000",
            "markPriceTwap": "88900000",
            "fundingRateLong": "-1000000",
            "fundingRateShort": "-1000000",
            "periodRevenue": "0",
            "baseAssetAmountWithAmm": "0",
            "baseAssetAmountWithUnsettledLp": "0",
        }
    ]
}


def _mock_drift_client() -> httpx.Client:
    def _get(url, **kwargs):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        if "dlob.drift.trade" in str(url):
            resp.json.return_value = _L2_RESPONSE
        elif "data.api.drift.trade" in str(url):
            resp.json.return_value = _FUNDING_RESPONSE
        else:
            resp.json.return_value = {}
        return resp

    client = MagicMock(spec=httpx.Client)
    client.get = _get
    return client


def test_drift_connector_get_markets():
    conn = DriftConnector(client=_mock_drift_client())
    markets = asyncio.get_event_loop().run_until_complete(conn.get_markets())
    assert len(markets) >= 3
    symbols = [m.symbol for m in markets]
    assert "SOL-PERP" in symbols
    assert "BTC-PERP" in symbols


def test_drift_connector_get_orderbook():
    conn = DriftConnector(client=_mock_drift_client())
    ob = asyncio.get_event_loop().run_until_complete(conn.get_orderbook("SOL-PERP"))
    assert ob.market == "SOL-PERP"
    assert len(ob.bids) == 1
    assert ob.bids[0].price == pytest.approx(89000000 / _PRICE_PRECISION)


def test_drift_connector_get_price():
    conn = DriftConnector(client=_mock_drift_client())
    price = asyncio.get_event_loop().run_until_complete(conn.get_price("SOL-PERP"))
    expected = (89000000 + 89100000) / 2 / _PRICE_PRECISION
    assert price == pytest.approx(expected)


def test_drift_connector_get_funding():
    """fetch_funding_rates is deprecated — returns empty list."""
    conn = DriftConnector(client=_mock_drift_client())
    rates = asyncio.get_event_loop().run_until_complete(
        conn.get_funding_rates("SOL-PERP", limit=1)
    )
    assert len(rates) == 0  # deprecated endpoint


def test_drift_connector_place_order_raises():
    conn = DriftConnector(client=_mock_drift_client())
    order = Order("SOL-PERP", Side.LONG, OrderType.MARKET, 1.0)
    with pytest.raises(NotImplementedError):
        asyncio.get_event_loop().run_until_complete(conn.place_order(order))


def test_drift_connector_market_info():
    conn = DriftConnector(client=_mock_drift_client())
    info = conn.get_market_info("SOL-PERP")
    assert info is not None
    assert info.symbol == "SOL-PERP"
    assert info.taker_fee == 0.001

    assert conn.get_market_info("NONEXISTENT") is None


# -- Drift poller ---------------------------------------------------------------

def test_drift_poller_poll_once():
    from flint.providers.drift_api import DriftDataProvider

    client = _mock_drift_client()
    provider = DriftDataProvider(client=client)
    poller = DriftPoller(provider=provider)

    snapshots = []
    poller.on_orderbook = lambda ob: snapshots.append(ob)
    poller.poll_once("SOL-PERP")

    assert len(snapshots) == 1
    assert snapshots[0].market == "SOL-PERP"


def test_drift_poller_run_loop():
    from flint.providers.drift_api import DriftDataProvider

    client = _mock_drift_client()
    provider = DriftDataProvider(client=client)
    poller = DriftPoller(provider=provider)

    count = 0
    def counter(ob):
        nonlocal count
        count += 1
    poller.on_orderbook = counter
    poller.run_loop("SOL-PERP", interval_s=0, max_iterations=3)

    assert count == 3


# -- Jupiter connector ---------------------------------------------------------

_JUPITER_QUOTE = {
    "outAmount": "89500000",
    "routePlan": [{"percent": 100}],
}


def _mock_jupiter_client() -> httpx.Client:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = _JUPITER_QUOTE

    client = MagicMock(spec=httpx.Client)
    client.get.return_value = mock_resp
    return client


def test_jupiter_connector_protocol():
    conn = JupiterConnector(client=_mock_jupiter_client())
    assert conn.protocol == "jupiter"


def test_jupiter_connector_get_price():
    conn = JupiterConnector(client=_mock_jupiter_client())
    price = asyncio.get_event_loop().run_until_complete(conn.get_price("SOL/USDC"))
    assert price == pytest.approx(89.5)


def test_jupiter_connector_place_order_raises():
    conn = JupiterConnector(client=_mock_jupiter_client())
    order = Order("SOL/USDC", Side.LONG, OrderType.MARKET, 1.0)
    with pytest.raises(NotImplementedError):
        asyncio.get_event_loop().run_until_complete(conn.place_order(order))
