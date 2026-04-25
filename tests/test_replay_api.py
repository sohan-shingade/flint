"""D-6.4-replay slice 5 — replay API endpoint tests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flint.portfolio.event_log import EventKind


@pytest.fixture
def client(tmp_path):
    """Each test gets its own FlintStore swapped onto app.state. We
    can't rely on `FLINT_DB_PATH` env override because the FastAPI
    `app` is a module-level singleton and the store created by an
    earlier test's lifespan can leak forward."""
    from flint.api.main import app
    from flint.store import FlintStore

    db = tmp_path / "test_replay_api.duckdb"
    with TestClient(app) as c:
        # Replace the lifespan-created store with a fresh per-test one.
        old_store = getattr(app.state, "store", None)
        if old_store is not None:
            try:
                old_store.close()
            except Exception:
                pass
        app.state.store = FlintStore(str(db))
        try:
            yield c
        finally:
            try:
                app.state.store.close()
            except Exception:
                pass


@pytest.fixture
def writer(client):
    from flint.api.main import app
    from flint.portfolio.event_log import EventLogWriter
    return EventLogWriter(app.state.store)


@pytest.fixture
def snaps(client):
    from flint.api.main import app
    from flint.portfolio.snapshots import SnapshotStore
    return SnapshotStore(app.state.store)


class TestEventsEndpoint:
    def test_empty_session(self, client):
        r = client.get("/api/v1/replay/empty/events")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 0
        assert body["events"] == []
        assert body["has_more"] is False

    def test_returns_events_in_seq_order(self, client, writer):
        for i in range(5):
            writer.append("s1", ts=100 + i, kind=EventKind.FILL, payload={"i": i})
        r = client.get("/api/v1/replay/s1/events")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 5
        assert [e["seq"] for e in body["events"]] == [0, 1, 2, 3, 4]

    def test_pagination_via_since(self, client, writer):
        for i in range(5):
            writer.append("s1", ts=100 + i, kind=EventKind.FILL, payload={"i": i})
        r = client.get("/api/v1/replay/s1/events?since=2")
        body = r.json()
        seqs = [e["seq"] for e in body["events"]]
        assert all(s > 2 for s in seqs)
        assert seqs == [3, 4]

    def test_limit_caps_results_and_sets_has_more(self, client, writer):
        for i in range(10):
            writer.append("s1", ts=100 + i, kind=EventKind.FILL, payload={"i": i})
        r = client.get("/api/v1/replay/s1/events?limit=3")
        body = r.json()
        assert body["count"] == 3
        assert body["has_more"] is True

    def test_session_isolation(self, client, writer):
        writer.append("a", ts=1, kind=EventKind.FILL, payload={})
        writer.append("b", ts=2, kind=EventKind.FILL, payload={})
        a_count = client.get("/api/v1/replay/a/events").json()["count"]
        b_count = client.get("/api/v1/replay/b/events").json()["count"]
        assert a_count == 1 and b_count == 1


class TestStateEndpoint:
    def test_replay_returns_book_state(self, client, writer):
        writer.append("s1", ts=100, kind=EventKind.FILL, payload={
            "market": "SOL-PERP", "venue": "drift", "side": "long",
            "size": 1.0, "price": 100.0, "fee": 0.05,
        })
        writer.append("s1", ts=200, kind=EventKind.FILL, payload={
            "market": "SOL-PERP", "venue": "drift", "side": "short",
            "size": 1.0, "price": 110.0, "fee": 0.05,
        })
        r = client.get("/api/v1/replay/s1/state?target_ts=300&initial_capital=10000")
        assert r.status_code == 200
        body = r.json()
        # Open then close for +10 PnL minus 0.10 fees = 9.90 net
        assert body["realized_pnl"] == pytest.approx(10.0)
        assert body["fill_count"] == 2
        assert body["positions"] == {}
        assert body["cash"] == pytest.approx(10_000.0 + 10.0 - 0.10)

    def test_partial_replay_at_intermediate_ts(self, client, writer):
        writer.append("s1", ts=100, kind=EventKind.FILL, payload={
            "market": "SOL-PERP", "venue": "drift", "side": "long",
            "size": 1.0, "price": 100.0,
        })
        writer.append("s1", ts=200, kind=EventKind.FILL, payload={
            "market": "SOL-PERP", "venue": "drift", "side": "short",
            "size": 1.0, "price": 110.0,
        })
        r = client.get("/api/v1/replay/s1/state?target_ts=150&initial_capital=10000")
        body = r.json()
        # Only the first fill folded; position open
        assert body["fill_count"] == 1
        assert "drift|SOL-PERP" in body["positions"]
        pos = body["positions"]["drift|SOL-PERP"]
        assert pos["side"] == "long"
        assert pos["size"] == 1.0

    def test_unknown_session_returns_initial(self, client):
        r = client.get("/api/v1/replay/missing/state?target_ts=100&initial_capital=5000")
        assert r.status_code == 200
        body = r.json()
        assert body["cash"] == 5_000.0
        assert body["fill_count"] == 0


class TestSummaryEndpoint:
    def test_empty_session(self, client):
        r = client.get("/api/v1/replay/empty/summary")
        assert r.status_code == 200
        body = r.json()
        assert body["event_count"] == 0
        assert body["latest_seq"] is None
        assert body["snapshot_count"] == 0

    def test_with_events_and_snapshot(self, client, writer, snaps):
        from flint.portfolio.replay import BookState
        writer.append("s1", ts=100, kind=EventKind.FILL, payload={"x": 1})
        writer.append("s1", ts=200, kind=EventKind.FILL, payload={"x": 2})
        snaps.write("s1", seq=0, ts=100, state=BookState(cash=1.0, initial_capital=1.0))

        r = client.get("/api/v1/replay/s1/summary")
        body = r.json()
        assert body["event_count"] == 2
        assert body["latest_seq"] == 1
        assert body["snapshot_count"] == 1
