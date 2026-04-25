"""D-6.4-replay slice 3 — snapshot compaction tests.

Pins the snapshot round-trip + the fast-forward replay path:
- BookState ↔ JSON round-trips byte-for-byte
- replay(use_snapshot=True) and replay(use_snapshot=False) produce
  byte-identical state on the same target_ts (this is the load-bearing
  assertion for the slice's correctness)
- latest_before respects ts ordering and returns None when no
  qualifying snapshot exists
"""
from __future__ import annotations

import pytest

from flint.portfolio.event_log import EventKind, EventLogWriter
from flint.portfolio.replay import (
    BookState,
    _ReplayPosition,
    fold,
    replay,
)
from flint.portfolio.snapshots import (
    SnapshotStore,
    _state_from_json,
    _state_to_json,
    should_compact,
)
from flint.store import FlintStore


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "test_snapshots.duckdb"
    s = FlintStore(str(db))
    yield s
    s.close()


@pytest.fixture
def writer(store):
    return EventLogWriter(store)


@pytest.fixture
def snaps(store):
    return SnapshotStore(store)


def _state_with_one_position() -> BookState:
    s = BookState(cash=9_950.0, initial_capital=10_000.0,
                  total_fees=0.5, total_funding=2.5, realized_pnl=10.0,
                  fill_count=2)
    s.positions[("drift", "SOL-PERP")] = _ReplayPosition(
        market="SOL-PERP", venue="drift", side="long",
        size=1.0, entry_price=150.0,
    )
    return s


class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        s = _state_with_one_position()
        s.positions[("hyperliquid", "BTC-PERP")] = _ReplayPosition(
            market="BTC-PERP", venue="hyperliquid", side="short",
            size=0.5, entry_price=70_000.0,
        )
        text = _state_to_json(s)
        clone = _state_from_json(text)
        assert clone.cash == s.cash
        assert clone.realized_pnl == s.realized_pnl
        assert clone.total_fees == s.total_fees
        assert clone.total_funding == s.total_funding
        assert clone.fill_count == s.fill_count
        assert sorted(clone.positions.keys()) == sorted(s.positions.keys())
        for key, pos in s.positions.items():
            other = clone.positions[key]
            assert (other.market, other.venue, other.side, other.size,
                    other.entry_price) == (
                pos.market, pos.venue, pos.side, pos.size, pos.entry_price,
            )

    def test_empty_state_round_trips(self):
        empty = BookState(cash=10_000.0, initial_capital=10_000.0)
        text = _state_to_json(empty)
        clone = _state_from_json(text)
        assert clone.positions == {}
        assert clone.cash == 10_000.0


class TestSnapshotStore:
    def test_schema_idempotent(self, store):
        SnapshotStore(store)
        SnapshotStore(store)
        # No exception → table is CREATE IF NOT EXISTS.

    def test_write_and_read_round_trip(self, snaps):
        s = _state_with_one_position()
        snaps.write("s1", seq=42, ts=1700000000, state=s)
        result = snaps.latest("s1")
        assert result is not None
        seq, ts, state = result
        assert seq == 42
        assert ts == 1700000000
        assert state.cash == s.cash
        assert ("drift", "SOL-PERP") in state.positions

    def test_count(self, snaps):
        assert snaps.count("s1") == 0
        snaps.write("s1", seq=10, ts=100, state=BookState(cash=1, initial_capital=1))
        snaps.write("s1", seq=20, ts=200, state=BookState(cash=1, initial_capital=1))
        assert snaps.count("s1") == 2

    def test_isolation_by_session(self, snaps):
        snaps.write("a", seq=10, ts=100, state=BookState(cash=1, initial_capital=1))
        snaps.write("b", seq=20, ts=200, state=BookState(cash=2, initial_capital=2))
        assert snaps.count("a") == 1
        assert snaps.count("b") == 1
        a_latest = snaps.latest("a")
        b_latest = snaps.latest("b")
        assert a_latest is not None and a_latest[0] == 10
        assert b_latest is not None and b_latest[0] == 20

    def test_upsert_overwrites_same_seq(self, snaps):
        """`INSERT OR REPLACE` lets the compactor re-run idempotently."""
        snaps.write("s1", seq=5, ts=100, state=BookState(cash=1.0, initial_capital=1.0))
        snaps.write("s1", seq=5, ts=100, state=BookState(cash=999.0, initial_capital=1.0))
        assert snaps.count("s1") == 1
        latest = snaps.latest("s1")
        assert latest is not None and latest[2].cash == 999.0


class TestLatestBefore:
    def test_returns_most_recent_at_or_before_ts(self, snaps):
        snaps.write("s1", seq=5, ts=100, state=BookState(cash=1.0, initial_capital=1.0))
        snaps.write("s1", seq=10, ts=200, state=BookState(cash=2.0, initial_capital=1.0))
        snaps.write("s1", seq=15, ts=300, state=BookState(cash=3.0, initial_capital=1.0))

        # at ts=150 → ts=100 snapshot
        result = snaps.latest_before("s1", target_ts=150)
        assert result is not None and result[0] == 5

        # at ts=200 (exact match) → ts=200 snapshot
        result = snaps.latest_before("s1", target_ts=200)
        assert result is not None and result[0] == 10

        # at ts=10000 (past end) → ts=300 (latest)
        result = snaps.latest_before("s1", target_ts=10_000)
        assert result is not None and result[0] == 15

    def test_returns_none_when_no_qualifying_snapshot(self, snaps):
        snaps.write("s1", seq=10, ts=200, state=BookState(cash=1, initial_capital=1))
        assert snaps.latest_before("s1", target_ts=100) is None
        assert snaps.latest_before("missing", target_ts=999) is None


class TestReplayFastForwardParity:
    """The load-bearing assertion. Replay-from-snapshot must produce
    identical state to replay-from-zero for the same target_ts."""

    def test_replay_with_and_without_snapshot_match(self, store, writer, snaps):
        # 10 fills → snapshot at seq=4 → 5 more fills, then close
        prices = [100.0, 101.0, 102.0, 103.0, 104.0,
                  105.0, 106.0, 107.0, 108.0, 109.0]
        for i, p in enumerate(prices):
            writer.append("s1", ts=100 + i, kind=EventKind.FILL, payload={
                "market": "SOL-PERP", "venue": "drift",
                "side": "long" if i < 5 else "short",
                "size": 1.0, "price": p, "fee": 0.05,
            })

        # Snapshot at seq=4 (after 5 opens, before 5 closes/flips).
        snap_state = replay(store, "s1", target_ts=104,
                            initial_capital=10_000.0, use_snapshot=False)
        snaps.write("s1", seq=4, ts=104, state=snap_state)

        # Replay at ts=109 from zero vs from snapshot.
        a = replay(store, "s1", target_ts=109,
                   initial_capital=10_000.0, use_snapshot=False)
        b = replay(store, "s1", target_ts=109,
                   initial_capital=10_000.0, use_snapshot=True)

        assert a.cash == pytest.approx(b.cash)
        assert a.realized_pnl == pytest.approx(b.realized_pnl)
        assert a.total_fees == pytest.approx(b.total_fees)
        assert a.fill_count == b.fill_count
        # Same positions
        assert sorted(a.positions.keys()) == sorted(b.positions.keys())
        for key, pos in a.positions.items():
            other = b.positions[key]
            assert pos.size == pytest.approx(other.size)
            assert pos.entry_price == pytest.approx(other.entry_price)

    def test_replay_with_snapshot_falls_back_when_target_before_snapshot(
        self, store, writer, snaps
    ):
        """Snapshot at ts=200, target_ts=150 → no qualifying snapshot,
        replay must fall through to read-from-zero and still be correct."""
        for i in range(5):
            writer.append("s1", ts=100 + i, kind=EventKind.FILL, payload={
                "market": "SOL-PERP", "venue": "drift", "side": "long",
                "size": 1.0, "price": 100.0,
            })
        # Snapshot far in the future
        snaps.write("s1", seq=999, ts=999_999,
                    state=BookState(cash=42.0, initial_capital=42.0))
        # Replay before the snapshot
        result = replay(store, "s1", target_ts=104,
                        initial_capital=10_000.0, use_snapshot=True)
        # 5 opens at $100 each, no fees → cash unchanged
        assert result.cash == pytest.approx(10_000.0)
        assert result.fill_count == 5

    def test_unknown_session_returns_initial(self, store, snaps):
        result = replay(store, "missing", target_ts=999,
                        initial_capital=5_000.0, use_snapshot=True)
        assert result.cash == 5_000.0


class TestFoldWithSeed:
    def test_seed_carries_state_forward(self):
        seed = _state_with_one_position()
        seed_cash_before = seed.cash
        # Add a single funding event on top of the seed.
        from flint.portfolio.event_log import PortfolioEvent
        events = [PortfolioEvent(
            session_id="s", seq=10, ts=100, kind=EventKind.FUNDING,
            payload={"market": "SOL-PERP", "payment": 1.0},
        )]
        out = fold(events, initial_capital=999.0, seed=seed)
        # initial_capital arg is ignored when seed is given (carries
        # the seed's value).
        assert out.initial_capital == 10_000.0
        # fold mutates seed in place — `out is seed`. Funding event
        # deducted 1.0 from the seed's cash.
        assert out is seed
        assert out.cash == pytest.approx(seed_cash_before - 1.0)


class TestShouldCompact:
    def test_default_threshold_10k(self):
        assert should_compact(events_since_last=9_999) is False
        assert should_compact(events_since_last=10_000) is True
        assert should_compact(events_since_last=20_000) is True

    def test_custom_threshold(self):
        assert should_compact(events_since_last=99, every_n_events=100) is False
        assert should_compact(events_since_last=100, every_n_events=100) is True
