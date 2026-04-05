"""Tests for DuneBorrowBackfill (Dune Analytics historical backfill)."""
from __future__ import annotations

from unittest.mock import MagicMock

from flint.providers.jupiter_borrow import DuneBorrowBackfill

MOCK_DUNE_RESPONSE = {
    "result": {
        "rows": [
            {
                "market": "SOL-PERP",
                "block_time": "2024-06-01T00:00:00Z",
                "hourly_funding_dbps": 80,
                "cumulative_interest_rate": 1000100000000,
                "utilization": 0.65,
            },
            {
                "market": "SOL-PERP",
                "block_time": "2024-06-01T01:00:00Z",
                "hourly_funding_dbps": 85,
                "cumulative_interest_rate": 1000200000000,
                "utilization": 0.68,
            },
        ],
    },
}


def test_parse_dune_response():
    backfill = DuneBorrowBackfill(api_key="fake_key")
    snapshots = backfill._parse_response(MOCK_DUNE_RESPONSE)
    assert len(snapshots) == 2
    assert snapshots[0].market == "SOL-PERP"
    assert snapshots[0].rate_hourly == 0.00008
    assert snapshots[0].utilization == 0.65
    assert snapshots[0].source == "dune"
    assert snapshots[1].rate_hourly == 0.000085


def test_build_query():
    backfill = DuneBorrowBackfill(api_key="fake_key")
    query = backfill._build_query("SOL-PERP", 1000, 2000)
    assert isinstance(query, str)
    assert "SOL" in query


def test_backfill_requires_api_key():
    assert DuneBorrowBackfill(api_key="").is_available() is False
    assert DuneBorrowBackfill(api_key="dune_abc123").is_available() is True


def test_parse_empty_response():
    backfill = DuneBorrowBackfill(api_key="fake_key")
    empty = {"result": {"rows": []}}
    assert backfill._parse_response(empty) == []


def test_parse_missing_result_key():
    backfill = DuneBorrowBackfill(api_key="fake_key")
    assert backfill._parse_response({}) == []


def test_build_query_contains_timestamps():
    backfill = DuneBorrowBackfill(api_key="fake_key")
    query = backfill._build_query("ETH-PERP", 1717200000, 1717286400)
    assert isinstance(query, str)
    assert len(query) > 0


def test_close_does_not_raise():
    """When client is injected (not owned), close() must not propagate to it."""
    mock_client = MagicMock()
    backfill = DuneBorrowBackfill(api_key="fake_key", client=mock_client)
    backfill.close()  # should not raise
    mock_client.close.assert_not_called()


def test_close_owned_client():
    """When no client is injected, close() should release the owned client."""
    backfill = DuneBorrowBackfill(api_key="fake_key")
    mock_client = MagicMock()
    backfill._client = mock_client
    backfill._owns_client = True
    backfill.close()
    mock_client.close.assert_called_once()
