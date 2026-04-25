"""D-6.4-replay foundation — event log writer + reader tests."""
from __future__ import annotations

import threading

import pytest

from flint.portfolio.event_log import (
    EventKind,
    EventLogReader,
    EventLogWriter,
    PortfolioEvent,
)
from flint.store import FlintStore


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "test_events.duckdb"
    s = FlintStore(str(db))
    yield s
    s.close()


@pytest.fixture
def writer(store):
    return EventLogWriter(store)


@pytest.fixture
def reader(store):
    return EventLogReader(store)


class TestSchema:
    def test_table_created_on_writer_init(self, store):
        EventLogWriter(store)
        # Table exists after writer construction
        rows = store._conn.execute(
            "SELECT COUNT(*) FROM portfolio_events"
        ).fetchone()
        assert rows[0] == 0

    def test_writer_constructs_idempotent(self, store):
        # Calling twice must not raise (CREATE IF NOT EXISTS).
        EventLogWriter(store)
        EventLogWriter(store)


class TestAppend:
    def test_seq_starts_at_zero_per_session(self, writer):
        seq = writer.append("s1", ts=100, kind=EventKind.ORDER_SUBMIT,
                            payload={"oid": "o1"})
        assert seq == 0

    def test_seq_monotonic_within_session(self, writer):
        s1 = writer.append("s1", ts=100, kind=EventKind.ORDER_SUBMIT, payload={})
        s2 = writer.append("s1", ts=101, kind=EventKind.FILL, payload={})
        s3 = writer.append("s1", ts=102, kind=EventKind.ORDER_CANCEL, payload={})
        assert (s1, s2, s3) == (0, 1, 2)

    def test_seq_independent_across_sessions(self, writer):
        a0 = writer.append("a", ts=100, kind=EventKind.FILL, payload={})
        b0 = writer.append("b", ts=101, kind=EventKind.FILL, payload={})
        a1 = writer.append("a", ts=102, kind=EventKind.FILL, payload={})
        b1 = writer.append("b", ts=103, kind=EventKind.FILL, payload={})
        assert (a0, a1) == (0, 1)
        assert (b0, b1) == (0, 1)

    def test_payload_round_trips_through_json(self, writer, reader):
        payload = {"oid": "x1", "price": 150.5, "size": 1.0,
                   "nested": {"venue": "drift", "tags": ["a", "b"]}}
        writer.append("s1", ts=100, kind=EventKind.FILL, payload=payload)
        events = reader.read_all("s1")
        assert events[0].payload == payload


class TestAppendMany:
    def test_assigns_consecutive_seqs(self, writer, reader):
        events = [
            {"ts": 100, "kind": EventKind.ORDER_SUBMIT, "payload": {"i": 0}},
            {"ts": 101, "kind": EventKind.ORDER_SUBMIT, "payload": {"i": 1}},
            {"ts": 102, "kind": EventKind.FILL, "payload": {"i": 2}},
        ]
        seqs = writer.append_many("s1", events)
        assert seqs == [0, 1, 2]
        assert reader.count("s1") == 3

    def test_empty_list_returns_empty(self, writer):
        assert writer.append_many("s1", []) == []

    def test_continues_seq_after_individual_appends(self, writer, reader):
        writer.append("s1", ts=10, kind=EventKind.FILL, payload={})
        seqs = writer.append_many("s1", [
            {"ts": 11, "kind": EventKind.FILL, "payload": {}},
            {"ts": 12, "kind": EventKind.FILL, "payload": {}},
        ])
        assert seqs == [1, 2]


class TestSeqRecoveryAcrossWriters:
    def test_new_writer_picks_up_max_seq(self, store):
        w1 = EventLogWriter(store)
        w1.append("s1", ts=100, kind=EventKind.FILL, payload={})
        w1.append("s1", ts=101, kind=EventKind.FILL, payload={})

        # Simulate process restart — fresh writer instance.
        w2 = EventLogWriter(store)
        seq = w2.append("s1", ts=102, kind=EventKind.FILL, payload={})
        assert seq == 2


class TestReader:
    def test_read_all_orders_by_seq(self, writer, reader):
        # Append in non-ts order to confirm seq drives the sort.
        writer.append("s1", ts=200, kind=EventKind.FILL, payload={"i": 0})
        writer.append("s1", ts=100, kind=EventKind.FILL, payload={"i": 1})
        events = reader.read_all("s1")
        assert [e.seq for e in events] == [0, 1]

    def test_read_until_filters_by_ts(self, writer, reader):
        writer.append("s1", ts=100, kind=EventKind.FILL, payload={"i": 0})
        writer.append("s1", ts=200, kind=EventKind.FILL, payload={"i": 1})
        writer.append("s1", ts=300, kind=EventKind.FILL, payload={"i": 2})
        events = reader.read_until("s1", target_ts=200)
        assert len(events) == 2
        assert [e.payload["i"] for e in events] == [0, 1]

    def test_count_and_latest_seq(self, writer, reader):
        assert reader.count("s1") == 0
        assert reader.latest_seq("s1") is None
        writer.append("s1", ts=100, kind=EventKind.FILL, payload={})
        writer.append("s1", ts=101, kind=EventKind.FILL, payload={})
        assert reader.count("s1") == 2
        assert reader.latest_seq("s1") == 1


class TestThreadSafety:
    """Multiple threads appending concurrently must produce a clean
    monotonic sequence per session — no duplicates, no gaps."""

    def test_concurrent_appends_to_same_session(self, writer, reader):
        n_threads = 8
        per_thread = 25

        def worker(idx: int):
            for j in range(per_thread):
                writer.append(
                    "shared", ts=100 + idx * per_thread + j,
                    kind=EventKind.FILL, payload={"thread": idx, "j": j},
                )

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total = n_threads * per_thread
        assert reader.count("shared") == total
        events = reader.read_all("shared")
        # Sequence is dense: 0..total-1 with no gaps
        assert [e.seq for e in events] == list(range(total))


class TestPortfolioEventDataclass:
    def test_to_row_round_trips(self):
        ev = PortfolioEvent(
            session_id="s1", seq=42, ts=1700000000,
            kind=EventKind.FILL,
            payload={"oid": "o1", "price": 150.0},
        )
        row = ev.to_row()
        clone = PortfolioEvent.from_row(row)
        assert clone == ev
