"""D-6.4-replay auto-compaction — BacktestContext writes snapshots
every N events when wired to a SnapshotStore.

The snapshot loop runs inside `_emit`; we exercise it by submitting
N orders against a context with `snapshot_every=K` and asserting
SnapshotStore.count crosses the expected threshold.
"""
from __future__ import annotations

import pytest

from flint.execution.backtest_context import BacktestContext
from flint.models import Candle, Side
from flint.portfolio.event_log import EventLogReader, EventLogWriter
from flint.portfolio.replay import replay
from flint.portfolio.snapshots import SnapshotStore
from flint.store import FlintStore


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "test_auto_compact.duckdb"
    s = FlintStore(str(db))
    yield s
    s.close()


def _candle(ts: int, close: float = 100.0) -> Candle:
    return Candle(
        ts=ts, open=close, high=close, low=close, close=close,
        volume=1.0, market="SOL-PERP", resolution_s=60,
    )


class TestAutoCompactionDisabledByDefault:
    def test_no_snapshot_when_store_unset(self, store):
        writer = EventLogWriter(store)
        ctx = BacktestContext(
            initial_capital=10_000.0,
            event_log_writer=writer,
            event_session_id="s1",
        )
        ctx.set_candle(_candle(100))
        for _ in range(20):
            ctx.market_order("SOL-PERP", Side.LONG, 1.0)
        # No snapshot_store wired → no compaction
        assert SnapshotStore(store).count("s1") == 0


class TestAutoCompactionFiresAtThreshold:
    def test_snapshot_after_threshold_events(self, store):
        writer = EventLogWriter(store)
        snaps = SnapshotStore(store)
        ctx = BacktestContext(
            initial_capital=10_000.0,
            event_log_writer=writer,
            event_session_id="s1",
            snapshot_store=snaps,
            snapshot_every=5,  # tiny threshold for the test
        )
        ctx.set_candle(_candle(100))
        # Each market_order emits one `order.submit` event. The default
        # order pipeline doesn't auto-fill them, so 5 orders → 5 events
        # → exactly one compaction.
        for _ in range(5):
            ctx.market_order("SOL-PERP", Side.LONG, 1.0)
        assert snaps.count("s1") == 1

    def test_multiple_snapshots_at_each_threshold(self, store):
        writer = EventLogWriter(store)
        snaps = SnapshotStore(store)
        ctx = BacktestContext(
            initial_capital=10_000.0,
            event_log_writer=writer,
            event_session_id="s1",
            snapshot_store=snaps,
            snapshot_every=3,
        )
        ctx.set_candle(_candle(100))
        # 9 events → expect 3 snapshots
        for _ in range(9):
            ctx.market_order("SOL-PERP", Side.LONG, 1.0)
        assert snaps.count("s1") == 3

    def test_counter_resets_after_compaction(self, store):
        writer = EventLogWriter(store)
        snaps = SnapshotStore(store)
        ctx = BacktestContext(
            initial_capital=10_000.0,
            event_log_writer=writer,
            event_session_id="s1",
            snapshot_store=snaps,
            snapshot_every=4,
        )
        ctx.set_candle(_candle(100))
        for _ in range(4):
            ctx.market_order("SOL-PERP", Side.LONG, 1.0)
        assert snaps.count("s1") == 1
        # 3 more events shouldn't trigger another snapshot (3 < 4).
        for _ in range(3):
            ctx.market_order("SOL-PERP", Side.LONG, 1.0)
        assert snaps.count("s1") == 1
        # One more crosses the threshold again.
        ctx.market_order("SOL-PERP", Side.LONG, 1.0)
        assert snaps.count("s1") == 2


class TestSnapshotPreservesReplayCorrectness:
    """Auto-compaction must produce a snapshot that, when used as the
    fast-forward seed, replays to the same state as a full replay
    from seq=0."""

    def test_replay_with_and_without_compaction_match(self, store):
        writer = EventLogWriter(store)
        snaps = SnapshotStore(store)
        ctx = BacktestContext(
            initial_capital=10_000.0,
            event_log_writer=writer,
            event_session_id="s1",
            snapshot_store=snaps,
            snapshot_every=3,
        )
        ctx.set_candle(_candle(100))
        # Open + queue closing market orders; processing fills each.
        for i in range(6):
            ctx.market_order("SOL-PERP", Side.LONG, 1.0)
            ctx.process_market_orders(_candle(100 + i, 100.0 + i))

        # Replay over the live-emitted log, with and without snapshot.
        reader = EventLogReader(store)
        latest = reader.latest_seq("s1")
        assert latest is not None

        a = replay(store, "s1", target_ts=2**62,
                   initial_capital=10_000.0, use_snapshot=False)
        b = replay(store, "s1", target_ts=2**62,
                   initial_capital=10_000.0, use_snapshot=True)
        assert a.cash == pytest.approx(b.cash)
        assert a.realized_pnl == pytest.approx(b.realized_pnl)
        assert a.fill_count == b.fill_count
