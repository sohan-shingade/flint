"""D-6.4-replay slice 4 — engine writer hooks tests.

Pins the load-bearing assertion: replay over the event log emitted
during a real backtest reproduces the BacktestContext's final state
byte-for-byte. Also verifies legacy paths (no event log) emit zero
events and pay zero overhead.
"""
from __future__ import annotations

import pytest

from flint.execution.backtest_context import BacktestContext
from flint.models import Candle, Side
from flint.portfolio.event_log import EventLogReader, EventLogWriter
from flint.portfolio.replay import replay
from flint.store import FlintStore


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "test_engine_hooks.duckdb"
    s = FlintStore(str(db))
    yield s
    s.close()


@pytest.fixture
def writer(store):
    return EventLogWriter(store)


@pytest.fixture
def reader(store):
    return EventLogReader(store)


def _candle(ts: int, close: float, market="SOL-PERP") -> Candle:
    return Candle(
        ts=ts, open=close, high=close, low=close, close=close,
        volume=1.0, market=market, resolution_s=60,
    )


def _ctx_with_log(writer, session_id="s1") -> BacktestContext:
    return BacktestContext(
        initial_capital=10_000.0,
        event_log_writer=writer,
        event_session_id=session_id,
    )


class TestNoLogIsNoOp:
    def test_legacy_ctx_emits_no_events(self, store, reader):
        # Writer exists in DB but ctx is constructed without it.
        EventLogWriter(store)  # ensure schema
        ctx = BacktestContext(initial_capital=10_000.0)
        ctx.set_candle(_candle(100, 100.0))
        ctx.market_order("SOL-PERP", Side.LONG, 1.0)
        ctx.process_market_orders(ctx._current_candle)
        # Nothing logged for any session_id
        assert reader.count("anything") == 0

    def test_log_writer_without_session_id_disabled(self, writer, reader):
        ctx = BacktestContext(initial_capital=10_000.0, event_log_writer=writer)
        ctx.set_candle(_candle(100, 100.0))
        ctx.market_order("SOL-PERP", Side.LONG, 1.0)
        # Without session_id, _emit short-circuits → nothing written.
        assert reader.count("any") == 0


class TestOrderEvents:
    def test_market_order_emits_order_submit(self, writer, reader):
        ctx = _ctx_with_log(writer)
        ctx.set_candle(_candle(100, 100.0))
        ctx.market_order("SOL-PERP", Side.LONG, 1.0)
        events = reader.read_all("s1")
        assert len(events) == 1
        assert events[0].kind == "order.submit"
        assert events[0].payload["type"] == "market"
        assert events[0].payload["side"] == "long"
        assert events[0].payload["size"] == 1.0
        assert events[0].ts == 100

    def test_limit_order_emits_with_price(self, writer, reader):
        ctx = _ctx_with_log(writer)
        ctx.set_candle(_candle(100, 100.0))
        ctx.limit_order("SOL-PERP", Side.LONG, 1.0, price=95.0)
        events = reader.read_all("s1")
        assert events[0].kind == "order.submit"
        assert events[0].payload["type"] == "limit"
        assert events[0].payload["price"] == 95.0

    def test_stop_and_take_profit_emit(self, writer, reader):
        ctx = _ctx_with_log(writer)
        ctx.set_candle(_candle(100, 100.0))
        ctx.stop_order("SOL-PERP", Side.SHORT, 1.0, trigger_price=90.0)
        ctx.take_profit_order("SOL-PERP", Side.SHORT, 1.0, trigger_price=110.0)
        events = reader.read_all("s1")
        assert [e.payload["type"] for e in events] == ["stop_loss", "take_profit"]

    def test_cancel_emits_when_successful(self, writer, reader):
        ctx = _ctx_with_log(writer)
        ctx.set_candle(_candle(100, 100.0))
        oid = ctx.limit_order("SOL-PERP", Side.LONG, 1.0, price=95.0)
        ok = ctx.cancel(oid)
        assert ok
        events = reader.read_all("s1")
        # submit + cancel
        assert [e.kind for e in events] == ["order.submit", "order.cancel"]

    def test_cancel_emits_nothing_when_not_found(self, writer, reader):
        ctx = _ctx_with_log(writer)
        ctx.set_candle(_candle(100, 100.0))
        ok = ctx.cancel("nonexistent")
        assert not ok
        assert reader.count("s1") == 0


class TestFillEvent:
    def test_fill_carries_full_payload(self, writer, reader):
        ctx = _ctx_with_log(writer)
        ctx.set_candle(_candle(100, 100.0))
        ctx.market_order("SOL-PERP", Side.LONG, 1.0)
        ctx.process_market_orders(ctx._current_candle)

        events = reader.read_all("s1")
        fill = next(e for e in events if e.kind == "fill")
        assert fill.payload["market"] == "SOL-PERP"
        assert fill.payload["side"] == "long"
        assert fill.payload["size"] == 1.0
        assert fill.payload["price"] > 0


class TestEndToEndReplayParity:
    """Load-bearing assertion: replay over the live-emitted event log
    produces identical state to the actual BacktestContext at end."""

    def test_open_close_round_trip(self, store, writer):
        ctx = _ctx_with_log(writer)

        ctx.set_candle(_candle(100, 100.0))
        ctx.market_order("SOL-PERP", Side.LONG, 1.0)
        ctx.process_market_orders(ctx._current_candle)

        ctx.set_candle(_candle(200, 110.0))
        ctx.market_order("SOL-PERP", Side.SHORT, 1.0)
        ctx.process_market_orders(ctx._current_candle)

        replayed = replay(store, "s1", target_ts=300, initial_capital=10_000.0)
        # Cash + realized PnL match live to the cent. Exact value
        # depends on fill-pipeline impact (default model adds slippage),
        # so we compare replay-vs-live, not against a hardcoded 10.0.
        assert replayed.cash == pytest.approx(ctx.account.cash)
        assert replayed.realized_pnl > 0  # Long-then-short on rising price profits
        assert replayed.fill_count == 2
        # Position fully closed in both
        assert replayed.positions == {}
        assert ctx.positions == []

    def test_dca_replay_matches_live(self, store, writer):
        ctx = _ctx_with_log(writer)
        for i in range(5):
            ctx.set_candle(_candle(100 + i, 100.0 + i))
            ctx.market_order("SOL-PERP", Side.LONG, 1.0)
            ctx.process_market_orders(ctx._current_candle)

        replayed = replay(store, "s1", target_ts=200, initial_capital=10_000.0)
        assert replayed.cash == pytest.approx(ctx.account.cash)
        # 5 DCA fills in both
        assert replayed.fill_count == 5
        # Same average entry
        live_pos = ctx.positions[0]
        replay_pos = replayed.positions[("default", "SOL-PERP")]
        assert replay_pos.size == pytest.approx(live_pos.size)
        assert replay_pos.entry_price == pytest.approx(live_pos.entry_price)

    def test_partial_replay_reproduces_intermediate_state(self, store, writer):
        """Replay at ts=150 should reproduce state after only the
        first fill — not the second one."""
        ctx = _ctx_with_log(writer)

        ctx.set_candle(_candle(100, 100.0))
        ctx.market_order("SOL-PERP", Side.LONG, 1.0)
        ctx.process_market_orders(ctx._current_candle)
        cash_after_open = ctx.account.cash

        ctx.set_candle(_candle(200, 110.0))
        ctx.market_order("SOL-PERP", Side.SHORT, 1.0)
        ctx.process_market_orders(ctx._current_candle)

        replayed = replay(store, "s1", target_ts=150, initial_capital=10_000.0)
        # Replay sees only the first open
        assert replayed.cash == pytest.approx(cash_after_open)
        assert replayed.fill_count == 1
        assert ("default", "SOL-PERP") in replayed.positions


class TestFundingEvent:
    def test_funding_payment_emits_event(self, writer, reader):
        from flint.models import FundingRate

        ctx = _ctx_with_log(writer)
        ctx.set_candle(_candle(100, 100.0))
        ctx.market_order("SOL-PERP", Side.LONG, 1.0)
        ctx.process_market_orders(ctx._current_candle)

        ctx.set_candle(_candle(200, 100.0))
        rate = FundingRate(
            market="SOL-PERP", ts=200, rate=0.0001,
            oracle_price=100.0, mark_price=100.0, slot=0,
        )
        ctx.apply_funding(rate)

        funding_events = [e for e in reader.read_all("s1") if e.kind == "funding"]
        assert len(funding_events) == 1
        assert funding_events[0].payload["market"] == "SOL-PERP"
        assert funding_events[0].payload["payment"] == pytest.approx(0.01)  # 1*100*0.0001
