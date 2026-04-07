"""Tests for RpcBorrowBackfill (archival RPC slot-based backfill)."""
from __future__ import annotations

from unittest.mock import MagicMock, call

from flint.providers.jupiter_borrow import RpcBorrowBackfill

MOCK_CUSTODY_DATA = {
    "fundingRateState": {
        "hourlyFundingDbps": 80,
        "cumulativeInterestRate": 1002340000000,
        "lastUpdate": 1700000000,
    },
    "assets": {"owned": 1000000, "locked": 650000},
}


def test_is_available():
    assert RpcBorrowBackfill(rpc_url="").is_available() is False
    assert RpcBorrowBackfill(rpc_url="https://api.mainnet-beta.solana.com").is_available() is True


def test_slot_for_timestamp_binary_search():
    backfill = RpcBorrowBackfill(rpc_url="http://fake")
    mock_client = MagicMock()
    mock_client.post.return_value.json.return_value = {"jsonrpc": "2.0", "result": 1700000000, "id": 1}
    mock_client.post.return_value.raise_for_status = MagicMock()
    backfill._client = mock_client
    slot = backfill._slot_for_timestamp(1700000000, hint_slot=200_000_000)
    assert isinstance(slot, int)


def test_get_block_time_returns_int():
    backfill = RpcBorrowBackfill(rpc_url="http://fake")
    mock_client = MagicMock()
    mock_client.post.return_value.json.return_value = {"jsonrpc": "2.0", "result": 1700000000, "id": 1}
    mock_client.post.return_value.raise_for_status = MagicMock()
    backfill._client = mock_client
    ts = backfill._get_block_time(200_000_000)
    assert isinstance(ts, int)
    assert ts == 1700000000


def test_get_block_time_returns_none_on_error():
    backfill = RpcBorrowBackfill(rpc_url="http://fake")
    mock_client = MagicMock()
    mock_client.post.side_effect = Exception("network error")
    backfill._client = mock_client
    result = backfill._get_block_time(200_000_000)
    assert result is None


def test_get_account_at_slot_returns_none_on_error():
    backfill = RpcBorrowBackfill(rpc_url="http://fake")
    mock_client = MagicMock()
    mock_client.post.side_effect = Exception("rpc error")
    backfill._client = mock_client
    result = backfill._get_account_at_slot("someaddress", 200_000_000)
    assert result is None


def test_fetch_returns_empty_when_account_unavailable(monkeypatch):
    backfill = RpcBorrowBackfill(rpc_url="http://fake")
    monkeypatch.setattr(backfill, "_slot_for_timestamp", lambda ts, hint_slot=200_000_000: 200_000_000)
    monkeypatch.setattr(backfill, "_get_account_at_slot", lambda addr, slot: None)
    results = backfill.fetch("SOL-PERP", "CustodyAddressXYZ", [1700000000, 1700003600])
    assert results == []


def test_fetch_returns_snapshots(monkeypatch):
    backfill = RpcBorrowBackfill(rpc_url="http://fake")
    monkeypatch.setattr(backfill, "_slot_for_timestamp", lambda ts, hint_slot=200_000_000: 200_000_000)
    monkeypatch.setattr(backfill, "_get_account_at_slot", lambda addr, slot: MOCK_CUSTODY_DATA)
    results = backfill.fetch("SOL-PERP", "CustodyAddressXYZ", [1700000000, 1700003600])
    assert len(results) == 2
    for snap in results:
        assert snap.market == "SOL-PERP"
        assert snap.source == "rpc"
        assert snap.rate_hourly == 0.00008


def test_close_does_not_raise():
    """When client is injected (not owned), close() must not propagate to it."""
    mock_client = MagicMock()
    backfill = RpcBorrowBackfill(rpc_url="http://fake", client=mock_client)
    backfill.close()  # should not raise
    mock_client.close.assert_not_called()


def test_close_owned_client():
    """When no client is injected, close() should release the owned client."""
    backfill = RpcBorrowBackfill(rpc_url="http://fake")
    mock_client = MagicMock()
    backfill._client = mock_client
    backfill._owns_client = True
    backfill.close()
    mock_client.close.assert_called_once()
