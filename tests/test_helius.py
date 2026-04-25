"""Tests for the Helius provider."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from flint.providers.helius import HeliusProvider


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _mock_client(responses: list | dict | None = None) -> httpx.Client:
    """Return a mock httpx.Client whose .get() returns canned responses.

    *responses* may be:
      - a list  -> successive calls pop from the front
      - a dict  -> used for every call
      - None    -> empty list for every call
    """
    if responses is None:
        responses = []

    remaining = list(responses) if isinstance(responses, list) else None
    static = responses if isinstance(responses, dict) else None

    def _get(url, **kwargs):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        if static is not None:
            resp.json.return_value = static
        elif remaining:
            resp.json.return_value = remaining.pop(0)
        else:
            resp.json.return_value = []
        return resp

    client = MagicMock(spec=httpx.Client)
    client.get = _get
    return client


# ---------------------------------------------------------------------------
# Availability / metadata
# ---------------------------------------------------------------------------

def test_helius_requires_api_key():
    p = HeliusProvider(api_key="")
    assert not p.is_available()


def test_helius_available_with_key():
    p = HeliusProvider(api_key="test-key")
    assert p.is_available()


def test_helius_supported_data_types():
    p = HeliusProvider(api_key="test")
    dtypes = p.supported_data_types
    assert "liquidations" in dtypes
    assert "whale_transfers" in dtypes
    assert "transactions" in dtypes


def test_helius_name():
    p = HeliusProvider(api_key="x")
    assert p.name == "helius"


# ---------------------------------------------------------------------------
# fetch_parsed_transactions
# ---------------------------------------------------------------------------

_PARSED_TX = {
    "signature": "sig1",
    "timestamp": 1700000100,
    "type": "TRANSFER",
    "description": "Transferred 10 SOL",
}


def test_fetch_parsed_transactions_basic():
    client = _mock_client([_PARSED_TX])
    # The mock returns the dict directly; Helius returns a list of txs,
    # so wrap it accordingly.
    client.get = MagicMock()
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = [_PARSED_TX]
    client.get.return_value = resp

    p = HeliusProvider(api_key="test-key", client=client)
    txs = p.fetch_parsed_transactions("SomeAddress", limit=10)

    assert len(txs) == 1
    assert txs[0]["signature"] == "sig1"

    # Verify the correct URL and params
    call_args = client.get.call_args
    url = call_args[0][0]
    assert "SomeAddress" in url
    assert "transactions" in url
    params = call_args[1].get("params", {})
    assert params["api-key"] == "test-key"
    assert params["limit"] == 10


def test_fetch_parsed_transactions_with_before():
    client = MagicMock(spec=httpx.Client)
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = [_PARSED_TX]
    client.get.return_value = resp

    p = HeliusProvider(api_key="key123", client=client)
    p.fetch_parsed_transactions("Addr", limit=50, before="prevSig")

    params = client.get.call_args[1]["params"]
    assert params["before"] == "prevSig"
    assert params["limit"] == 50


def test_fetch_parsed_transactions_error():
    client = MagicMock(spec=httpx.Client)
    client.get.side_effect = httpx.ConnectError("timeout")

    p = HeliusProvider(api_key="key", client=client)
    result = p.fetch_parsed_transactions("Addr")
    assert result == []


def test_fetch_parsed_transactions_non_200():
    client = MagicMock(spec=httpx.Client)
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 429
    client.get.return_value = resp

    p = HeliusProvider(api_key="key", client=client)
    result = p.fetch_parsed_transactions("Addr")
    assert result == []


# ---------------------------------------------------------------------------
# _parse_drift_events
# ---------------------------------------------------------------------------

def test_parse_drift_events_liquidation_long():
    p = HeliusProvider(api_key="test")
    txs = [
        {
            "signature": "abc123",
            "timestamp": 1700000000,
            "type": "PROGRAM_INTERACTION",
            "description": "liquidation of long position on SOL-PERP",
        }
    ]
    result = p._parse_drift_events(txs)
    assert isinstance(result, dict)
    assert "liquidations" in result
    assert len(result["liquidations"]) == 1
    liq = result["liquidations"][0]
    assert liq.side == "long"
    assert liq.ts == 1700000000
    assert liq.tx_sig == "abc123"


def test_parse_drift_events_liquidation_short():
    p = HeliusProvider(api_key="test")
    txs = [
        {
            "signature": "def456",
            "timestamp": 1700001000,
            "description": "Liquidated short position",
        }
    ]
    result = p._parse_drift_events(txs)
    liqs = result["liquidations"]
    assert len(liqs) == 1
    assert liqs[0].side == "short"


def test_parse_drift_events_no_liquidation():
    p = HeliusProvider(api_key="test")
    txs = [
        {
            "signature": "xyz",
            "timestamp": 1700000000,
            "description": "Placed limit order for SOL-PERP",
        }
    ]
    result = p._parse_drift_events(txs)
    assert result["liquidations"] == []


def test_parse_drift_events_empty_description():
    p = HeliusProvider(api_key="test")
    txs = [{"signature": "a", "timestamp": 100, "description": None}]
    result = p._parse_drift_events(txs)
    assert result["liquidations"] == []


def test_parse_drift_events_empty_list():
    p = HeliusProvider(api_key="test")
    result = p._parse_drift_events([])
    assert result == {"liquidations": []}


# ---------------------------------------------------------------------------
# fetch_drift_liquidations
# ---------------------------------------------------------------------------

_LIQ_TX_1 = {
    "signature": "liq1",
    "timestamp": 1700050000,
    "description": "Liquidation of long position",
}

_LIQ_TX_2 = {
    "signature": "liq2",
    "timestamp": 1700060000,
    "description": "Liquidated short position",
}

_NON_LIQ_TX = {
    "signature": "nope",
    "timestamp": 1700055000,
    "description": "Placed order",
}


@patch("flint.providers.helius.time.sleep")
def test_fetch_drift_liquidations(mock_sleep):
    """End-to-end: fetches txs, parses, filters by time range."""
    client = MagicMock(spec=httpx.Client)
    resp1 = MagicMock(spec=httpx.Response)
    resp1.status_code = 200
    resp1.json.return_value = [_LIQ_TX_2, _NON_LIQ_TX, _LIQ_TX_1]

    resp2 = MagicMock(spec=httpx.Response)
    resp2.status_code = 200
    resp2.json.return_value = []  # second page is empty -> stop

    client.get.side_effect = [resp1, resp2]

    p = HeliusProvider(api_key="key", client=client)
    liqs = p.fetch_drift_liquidations(
        start_ts=1700040000,
        end_ts=1700070000,
        market="SOL-PERP",
    )

    assert len(liqs) == 2
    sigs = {l.tx_sig for l in liqs}
    assert "liq1" in sigs
    assert "liq2" in sigs


@patch("flint.providers.helius.time.sleep")
def test_fetch_drift_liquidations_time_filter(mock_sleep):
    """Liquidations outside the time window are excluded."""
    client = MagicMock(spec=httpx.Client)
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = [_LIQ_TX_1, _LIQ_TX_2]
    client.get.side_effect = [resp, MagicMock(spec=httpx.Response, status_code=200, json=MagicMock(return_value=[]))]

    p = HeliusProvider(api_key="key", client=client)
    # Only liq1 (ts=1700050000) falls in this range
    liqs = p.fetch_drift_liquidations(
        start_ts=1700045000,
        end_ts=1700055000,
    )

    assert len(liqs) == 1
    assert liqs[0].tx_sig == "liq1"


# ---------------------------------------------------------------------------
# fetch_token_transfers
# ---------------------------------------------------------------------------

_TRANSFER_TX = {
    "signature": "tx_whale",
    "timestamp": 1700000500,
    "tokenTransfers": [
        {
            "fromUserAccount": "WalletA",
            "toUserAccount": "WalletB",
            "tokenAmount": 50000.0,
            "mint": "So11111111111111111111111111111111111111112",
        }
    ],
}

_TRANSFER_TX_SMALL = {
    "signature": "tx_small",
    "timestamp": 1700000600,
    "tokenTransfers": [
        {
            "fromUserAccount": "WalletC",
            "toUserAccount": "WalletD",
            "tokenAmount": 5.0,
            "mint": "So11111111111111111111111111111111111111112",
        }
    ],
}


@patch("flint.providers.helius.time.sleep")
def test_fetch_token_transfers_basic(mock_sleep):
    client = MagicMock(spec=httpx.Client)
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = [_TRANSFER_TX]
    client.get.side_effect = [resp, MagicMock(spec=httpx.Response, status_code=200, json=MagicMock(return_value=[]))]

    p = HeliusProvider(api_key="test-key", client=client)
    transfers = p.fetch_token_transfers(
        mint="So11111111111111111111111111111111111111112",
        start_ts=1700000000,
        end_ts=1700086400,
    )

    # Each transfer creates two WhaleTransfer objects (out + in)
    assert len(transfers) == 2
    directions = {t.direction for t in transfers}
    assert directions == {"in", "out"}
    assert all(t.amount == 50000.0 for t in transfers)
    assert all(t.tx_sig == "tx_whale" for t in transfers)


@patch("flint.providers.helius.time.sleep")
def test_fetch_token_transfers_min_amount_filter(mock_sleep):
    client = MagicMock(spec=httpx.Client)
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = [_TRANSFER_TX, _TRANSFER_TX_SMALL]
    client.get.side_effect = [resp, MagicMock(spec=httpx.Response, status_code=200, json=MagicMock(return_value=[]))]

    p = HeliusProvider(api_key="test-key", client=client)
    transfers = p.fetch_token_transfers(
        mint="So11111111111111111111111111111111111111112",
        start_ts=1700000000,
        end_ts=1700086400,
        min_amount=1000.0,
    )

    # Only the 50k transfer passes the threshold (the 5.0 one is filtered)
    assert len(transfers) == 2
    assert all(t.amount == 50000.0 for t in transfers)


@patch("flint.providers.helius.time.sleep")
def test_fetch_token_transfers_time_filter(mock_sleep):
    """Transfers outside the time window are excluded."""
    client = MagicMock(spec=httpx.Client)
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = [_TRANSFER_TX]
    client.get.side_effect = [resp, MagicMock(spec=httpx.Response, status_code=200, json=MagicMock(return_value=[]))]

    p = HeliusProvider(api_key="test-key", client=client)
    # Window does not contain the tx (ts=1700000500)
    transfers = p.fetch_token_transfers(
        mint="So11111111111111111111111111111111111111112",
        start_ts=1700001000,
        end_ts=1700002000,
    )
    assert transfers == []


@patch("flint.providers.helius.time.sleep")
def test_fetch_token_transfers_api_error(mock_sleep):
    client = MagicMock(spec=httpx.Client)
    client.get.side_effect = httpx.ConnectError("unreachable")

    p = HeliusProvider(api_key="test-key", client=client)
    transfers = p.fetch_token_transfers(
        mint="SomeMint",
        start_ts=1700000000,
        end_ts=1700086400,
    )
    assert transfers == []


@patch("flint.providers.helius.time.sleep")
def test_fetch_token_transfers_non_200(mock_sleep):
    client = MagicMock(spec=httpx.Client)
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 500
    client.get.return_value = resp

    p = HeliusProvider(api_key="test-key", client=client)
    transfers = p.fetch_token_transfers(
        mint="SomeMint",
        start_ts=1700000000,
        end_ts=1700086400,
    )
    assert transfers == []


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------

def test_close_owned_client():
    """When no external client is passed, close() shuts down the internal one."""
    p = HeliusProvider(api_key="key")
    inner = MagicMock()
    p._client = inner
    p._owns_client = True
    p.close()
    inner.close.assert_called_once()


def test_close_external_client():
    """When an external client is injected, close() does NOT close it."""
    ext = MagicMock(spec=httpx.Client)
    p = HeliusProvider(api_key="key", client=ext)
    p.close()
    ext.close.assert_not_called()
