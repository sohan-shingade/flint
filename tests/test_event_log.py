"""Event log: emit seam, upcast-on-read, and no-op events end-to-end (§2.10).

The persistence tests are **parametrized over UserDataPort adapters** so the
exact same seam proof runs against the Phase-1 in-memory adapter now and the
durable DuckDB adapter later (task #2.1) with no test rewrite — mirroring the
port contract suite.
"""

from __future__ import annotations

import pytest

from flint.adapters import InMemoryUserData
from flint.data.store import DuckDBUserData
from flint.engine.portfolio import (
    NOOP,
    RUN_FINISHED,
    RUN_STARTED,
    Event,
    EventLog,
    UpcasterRegistry,
)
from flint.ports import TenantContext, UserDataPort

ALICE = TenantContext(tenant_id="alice")
BOB = TenantContext(tenant_id="bob")


# Factories for every UserDataPort adapter the event log must work against.
# Task #2.1 appends its DuckDB factory here; the tests below do not change.
USERDATA_ADAPTERS = [
    ("in_memory", InMemoryUserData),
    ("duckdb", DuckDBUserData),
]


@pytest.fixture(params=[f for _, f in USERDATA_ADAPTERS], ids=[n for n, _ in USERDATA_ADAPTERS])
def store(request) -> UserDataPort:
    return request.param()


# --- no-op events flow end-to-end (§18 item 1) -----------------------------


def test_noop_events_round_trip_through_the_port(store: UserDataPort):
    log = EventLog(store, ALICE, run_id="run-1")
    log.emit(RUN_STARTED, {"strategy": "noop"})
    log.emit(NOOP, ts=1_700_000_000_000)
    log.emit(NOOP, ts=1_700_000_060_000)
    log.emit(RUN_FINISHED, {"status": "done"})

    events = log.read()
    assert [e.kind for e in events] == [RUN_STARTED, NOOP, NOOP, RUN_FINISHED]
    # seq is monotonic from 0, assigned by the writer (the sequencer).
    assert [e.seq for e in events] == [0, 1, 2, 3]
    assert events[0].payload == {"strategy": "noop"}
    assert events[1].ts == 1_700_000_000_000


def test_append_only_sequence_survives_resumption(store: UserDataPort):
    EventLog(store, ALICE, "run-1").emit(RUN_STARTED)
    # A fresh EventLog for the same run resumes seq where the log left off.
    resumed = EventLog(store, ALICE, "run-1")
    resumed.emit(NOOP)
    seqs = [e.seq for e in resumed.read()]
    assert seqs == [0, 1]


def test_events_are_tenant_scoped(store: UserDataPort):
    EventLog(store, ALICE, "run-1").emit(NOOP, {"who": "alice"})
    # Bob reading the same run_id sees nothing — same run_id, different tenant.
    assert EventLog(store, BOB, "run-1").read() == []


# --- upcast-on-read over an old-version corpus (§2.10) ---------------------


def test_upcast_on_read_promotes_old_events():
    # A registry with a fill v1 -> v2 upcaster that adds a defaulted field.
    reg = UpcasterRegistry()

    @reg.register("fill", 1)
    def _v1_to_v2(payload):
        return {**payload, "fidelity_tier": payload.get("fidelity_tier", "C")}

    # Corpus of already-stored v1 events (as they would be on disk).
    store = InMemoryUserData()
    old_rows = [
        {"kind": "fill", "event_version": 1, "ts": 10, "seq": 0,
         "payload": {"price": 100.0}},
        {"kind": "fill", "event_version": 1, "ts": 20, "seq": 1,
         "payload": {"price": 101.0, "fidelity_tier": "A"}},
    ]
    store.append_events(ALICE, "old-run", old_rows)

    log = EventLog(store, ALICE, "old-run", registry=reg)
    upcast = log.read()

    # Every event is now v2 with the new field defaulted (or preserved).
    assert [e.event_version for e in upcast] == [2, 2]
    assert upcast[0].payload["fidelity_tier"] == "C"  # defaulted
    assert upcast[1].payload["fidelity_tier"] == "A"  # preserved


def test_on_disk_rows_are_never_rewritten():
    reg = UpcasterRegistry()

    @reg.register("fill", 1)
    def _v1_to_v2(payload):
        return {**payload, "added": True}

    store = InMemoryUserData()
    store.append_events(
        ALICE, "r",
        [{"kind": "fill", "event_version": 1, "ts": 0, "seq": 0, "payload": {"p": 1}}],
    )
    log = EventLog(store, ALICE, "r", registry=reg)

    _ = log.read()  # upcast happens on read...
    raw = log.read_raw()  # ...but the stored row is untouched (still v1)
    assert raw[0]["event_version"] == 1
    assert "added" not in raw[0]["payload"]


def test_upcaster_chain_walks_multiple_versions():
    reg = UpcasterRegistry()

    @reg.register("x", 1)
    def _1(p):
        return {**p, "a": 1}

    @reg.register("x", 2)
    def _2(p):
        return {**p, "b": 2}

    assert reg.latest_version("x") == 3
    row = reg.upcast_to_latest({"kind": "x", "event_version": 1, "payload": {}})
    assert row["event_version"] == 3
    assert row["payload"] == {"a": 1, "b": 2}


def test_duplicate_upcaster_registration_is_rejected():
    reg = UpcasterRegistry()
    reg.register("x", 1)(lambda p: p)
    with pytest.raises(ValueError):
        reg.register("x", 1)(lambda p: p)


def test_event_row_round_trips_through_dict_form():
    ev = Event(kind=NOOP, payload={"k": "v"}, ts=5, event_version=1, seq=7)
    assert Event.from_row(ev.to_row()) == ev
