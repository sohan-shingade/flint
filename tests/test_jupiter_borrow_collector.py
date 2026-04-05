"""Tests for JupiterBorrowCollector (forward RPC collection)."""
from __future__ import annotations

from unittest.mock import MagicMock

from flint.providers.jupiter_borrow import JupiterBorrowCollector

MOCK_CUSTODY_DATA = {
    "fundingRateState": {
        "hourlyFundingDbps": 80,
        "cumulativeInterestRate": 1002340000000,
        "lastUpdate": 1700000000,
    },
    "assets": {"owned": 1000000, "locked": 650000},
}


def test_parse_custody_account():
    collector = JupiterBorrowCollector(rpc_url="http://fake")
    snapshot = collector._parse_custody("SOL-PERP", MOCK_CUSTODY_DATA, 1700000000)
    assert snapshot.market == "SOL-PERP"
    assert snapshot.rate_hourly == 0.00008
    assert abs(snapshot.utilization - 0.65) < 0.01
    assert snapshot.cumulative_rate == 1002340000000
    assert snapshot.source == "rpc"


def test_collector_markets():
    collector = JupiterBorrowCollector(rpc_url="http://fake")
    assert "SOL-PERP" in collector.markets
    assert "ETH-PERP" in collector.markets
    assert "BTC-PERP" in collector.markets


def test_dbps_to_hourly_rate():
    collector = JupiterBorrowCollector(rpc_url="http://fake")
    assert collector._dbps_to_hourly(80) == 0.00008
    assert collector._dbps_to_hourly(100) == 0.0001


def test_parse_custody_zero_owned():
    """Utilization should be 0 when owned == 0 to avoid division by zero."""
    collector = JupiterBorrowCollector(rpc_url="http://fake")
    data = {
        "fundingRateState": {
            "hourlyFundingDbps": 50,
            "cumulativeInterestRate": 0,
            "lastUpdate": 1700000000,
        },
        "assets": {"owned": 0, "locked": 0},
    }
    snapshot = collector._parse_custody("ETH-PERP", data, 1700000000)
    assert snapshot.utilization == 0.0


def test_collect_uses_rpc(monkeypatch):
    """collect() calls _rpc_get_account for each market and returns snapshots."""
    collector = JupiterBorrowCollector(rpc_url="http://fake")

    call_count = {"n": 0}

    def fake_rpc(address):
        call_count["n"] += 1
        return MOCK_CUSTODY_DATA

    monkeypatch.setattr(collector, "_rpc_get_account", fake_rpc)
    results = collector.collect()
    assert len(results) == len(collector.markets)
    assert call_count["n"] == len(collector.markets)
    for snap in results:
        assert snap.source == "rpc"


def test_collect_skips_failed_markets(monkeypatch):
    """collect() should skip markets where RPC returns None, not raise."""
    collector = JupiterBorrowCollector(rpc_url="http://fake")

    def fake_rpc(address):
        return None

    monkeypatch.setattr(collector, "_rpc_get_account", fake_rpc)
    results = collector.collect()
    assert results == []


def test_close_does_not_raise():
    """When client is injected (not owned), close() must not propagate to it."""
    mock_client = MagicMock()
    collector = JupiterBorrowCollector(rpc_url="http://fake", client=mock_client)
    collector.close()  # should not raise
    # Injected clients are not owned — close() must not call client.close()
    mock_client.close.assert_not_called()


def test_close_owned_client():
    """When no client is injected, close() should release the owned client."""
    collector = JupiterBorrowCollector(rpc_url="http://fake")
    # Replace the auto-created client with a mock so we can verify the call
    mock_client = MagicMock()
    collector._client = mock_client
    collector._owns_client = True
    collector.close()
    mock_client.close.assert_called_once()
