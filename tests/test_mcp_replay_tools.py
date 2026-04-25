"""D-6.4-replay slice 5 — MCP replay tool tests.

Mirrors the API endpoint test fixtures: each test gets a fresh
FlintStore + EventLogWriter; the MCP tools call into the same
service layer, so we verify the JSON envelopes round-trip.
"""
from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest


# Mock the mcp package before importing flint.mcp_server. FastMCP
# must behave as a class whose instances have `.tool()` and
# `.resource()` decorators that pass through.
_mock_mcp = MagicMock()
_mock_fastmcp = MagicMock()
_mock_fastmcp.return_value.tool.return_value = lambda fn: fn
_mock_fastmcp.return_value.resource.return_value = lambda fn: fn
_mock_fastmcp.return_value.name = "Flint"
_mock_mcp.server.fastmcp.FastMCP = _mock_fastmcp

sys.modules.setdefault("mcp", _mock_mcp)
sys.modules.setdefault("mcp.server", _mock_mcp.server)
sys.modules.setdefault("mcp.server.fastmcp", _mock_mcp.server.fastmcp)


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    """Point the MCP server at an isolated DuckDB by overriding
    `_store` directly with a fresh FlintStore on the temp path."""
    import flint.mcp_server as ms
    from flint.store import FlintStore

    db = tmp_path / "test_mcp_replay.duckdb"
    fresh_store = FlintStore(str(db))
    monkeypatch.setattr(ms, "_store", fresh_store)
    monkeypatch.setattr(ms, "_config", MagicMock(db_path=str(db)))
    yield str(db)
    try:
        fresh_store.close()
    except Exception:
        pass


@pytest.fixture
def writer(store_path):
    from flint.mcp_server import _get_store
    from flint.portfolio.event_log import EventLogWriter
    return EventLogWriter(_get_store())


class TestReplaySummary:
    def test_empty_session(self, store_path):
        from flint.mcp_server import replay_summary
        result = json.loads(replay_summary("missing"))
        assert result["session_id"] == "missing"
        assert result["event_count"] == 0
        assert result["latest_seq"] is None
        assert result["snapshot_count"] == 0

    def test_with_events(self, writer):
        from flint.mcp_server import replay_summary
        from flint.portfolio.event_log import EventKind
        writer.append("s1", ts=100, kind=EventKind.FILL, payload={"i": 1})
        writer.append("s1", ts=200, kind=EventKind.FILL, payload={"i": 2})
        result = json.loads(replay_summary("s1"))
        assert result["event_count"] == 2
        assert result["latest_seq"] == 1


class TestReplayState:
    def test_empty_session_returns_initial(self, store_path):
        from flint.mcp_server import replay_state
        result = json.loads(replay_state("missing", target_ts=100, initial_capital=5_000))
        assert result["cash"] == 5_000.0
        assert result["fill_count"] == 0
        assert result["positions"] == {}

    def test_open_close_round_trip(self, writer):
        from flint.mcp_server import replay_state
        from flint.portfolio.event_log import EventKind
        writer.append("s1", ts=100, kind=EventKind.FILL, payload={
            "market": "SOL-PERP", "venue": "drift", "side": "long",
            "size": 1.0, "price": 100.0, "fee": 0.05,
        })
        writer.append("s1", ts=200, kind=EventKind.FILL, payload={
            "market": "SOL-PERP", "venue": "drift", "side": "short",
            "size": 1.0, "price": 110.0, "fee": 0.05,
        })
        result = json.loads(replay_state("s1", target_ts=300, initial_capital=10_000))
        assert result["realized_pnl"] == pytest.approx(10.0)
        assert result["fill_count"] == 2
        assert result["positions"] == {}


class TestListReplayEvents:
    def test_pagination_via_since(self, writer):
        from flint.mcp_server import list_replay_events
        from flint.portfolio.event_log import EventKind
        for i in range(5):
            writer.append("s1", ts=100 + i, kind=EventKind.FILL, payload={"i": i})
        result = json.loads(list_replay_events("s1", since=2))
        seqs = [e["seq"] for e in result["events"]]
        assert seqs == [3, 4]

    def test_limit_caps_results(self, writer):
        from flint.mcp_server import list_replay_events
        from flint.portfolio.event_log import EventKind
        for i in range(10):
            writer.append("s1", ts=100 + i, kind=EventKind.FILL, payload={"i": i})
        result = json.loads(list_replay_events("s1", limit=3))
        assert result["count"] == 3
        assert result["has_more"] is True

    def test_empty_session(self, store_path):
        from flint.mcp_server import list_replay_events
        result = json.loads(list_replay_events("missing"))
        assert result["count"] == 0
        assert result["events"] == []
