"""D-6.4-replay slice 2 — replay primitive tests.

Pins the fold semantics: every event kind affects the BookState in a
predictable way, replay reads from the log correctly, and replay is
deterministic across two independent calls.
"""
from __future__ import annotations

import pytest

from flint.portfolio.event_log import (
    EventKind,
    EventLogWriter,
    PortfolioEvent,
)
from flint.portfolio.replay import fold, replay
from flint.store import FlintStore


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "test_replay.duckdb"
    s = FlintStore(str(db))
    yield s
    s.close()


def _ev(seq: int, ts: int, kind: str, payload: dict) -> PortfolioEvent:
    return PortfolioEvent(session_id="s", seq=seq, ts=ts, kind=kind, payload=payload)


class TestEmptyFold:
    def test_no_events_returns_initial(self):
        state = fold([], initial_capital=5_000.0)
        assert state.cash == 5_000.0
        assert state.initial_capital == 5_000.0
        assert state.positions == {}
        assert state.fill_count == 0


class TestFillFold:
    def test_open_position(self):
        state = fold([
            _ev(0, 1, EventKind.FILL, {
                "market": "SOL-PERP", "venue": "drift", "side": "long",
                "size": 2.0, "price": 100.0, "fee": 0.10,
            }),
        ], initial_capital=10_000.0)
        assert state.cash == 10_000.0 - 0.10
        assert state.total_fees == 0.10
        assert ("drift", "SOL-PERP") in state.positions
        pos = state.positions[("drift", "SOL-PERP")]
        assert pos.side == "long" and pos.size == 2.0 and pos.entry_price == 100.0

    def test_dca_averages_entry_price(self):
        state = fold([
            _ev(0, 1, EventKind.FILL, {
                "market": "SOL-PERP", "venue": "drift", "side": "long",
                "size": 1.0, "price": 100.0,
            }),
            _ev(1, 2, EventKind.FILL, {
                "market": "SOL-PERP", "venue": "drift", "side": "long",
                "size": 1.0, "price": 110.0,
            }),
        ], initial_capital=10_000.0)
        pos = state.positions[("drift", "SOL-PERP")]
        assert pos.size == 2.0
        assert pos.entry_price == pytest.approx(105.0)

    def test_partial_close_realizes_pnl(self):
        state = fold([
            _ev(0, 1, EventKind.FILL, {
                "market": "SOL-PERP", "venue": "drift", "side": "long",
                "size": 2.0, "price": 100.0,
            }),
            _ev(1, 2, EventKind.FILL, {
                "market": "SOL-PERP", "venue": "drift", "side": "short",
                "size": 1.0, "price": 110.0,
            }),
        ], initial_capital=10_000.0)
        # 1 unit @ 10 profit
        assert state.realized_pnl == pytest.approx(10.0)
        assert state.cash == pytest.approx(10_010.0)
        pos = state.positions[("drift", "SOL-PERP")]
        assert pos.size == 1.0
        assert pos.side == "long"

    def test_full_close_drops_position(self):
        state = fold([
            _ev(0, 1, EventKind.FILL, {
                "market": "SOL-PERP", "venue": "drift", "side": "long",
                "size": 1.0, "price": 100.0,
            }),
            _ev(1, 2, EventKind.FILL, {
                "market": "SOL-PERP", "venue": "drift", "side": "short",
                "size": 1.0, "price": 110.0,
            }),
        ], initial_capital=10_000.0)
        assert state.positions == {}
        assert state.realized_pnl == pytest.approx(10.0)

    def test_flip_keeps_remainder_on_opposite_side(self):
        state = fold([
            _ev(0, 1, EventKind.FILL, {
                "market": "SOL-PERP", "venue": "drift", "side": "long",
                "size": 1.0, "price": 100.0,
            }),
            _ev(1, 2, EventKind.FILL, {
                "market": "SOL-PERP", "venue": "drift", "side": "short",
                "size": 3.0, "price": 110.0,
            }),
        ], initial_capital=10_000.0)
        # +10 realized on the closed unit
        assert state.realized_pnl == pytest.approx(10.0)
        # 2 units short remain at the new entry price
        pos = state.positions[("drift", "SOL-PERP")]
        assert pos.side == "short"
        assert pos.size == pytest.approx(2.0)
        assert pos.entry_price == pytest.approx(110.0)

    def test_short_pnl_sign(self):
        """Short profits when price drops."""
        state = fold([
            _ev(0, 1, EventKind.FILL, {
                "market": "SOL-PERP", "venue": "drift", "side": "short",
                "size": 1.0, "price": 100.0,
            }),
            _ev(1, 2, EventKind.FILL, {
                "market": "SOL-PERP", "venue": "drift", "side": "long",
                "size": 1.0, "price": 90.0,
            }),
        ], initial_capital=10_000.0)
        assert state.realized_pnl == pytest.approx(10.0)


class TestFundingFold:
    def test_funding_payment_debits_cash(self):
        state = fold([
            _ev(0, 1, EventKind.FUNDING, {"market": "SOL-PERP", "payment": 5.0}),
            _ev(1, 2, EventKind.FUNDING, {"market": "SOL-PERP", "payment": -2.0}),
        ], initial_capital=10_000.0)
        # Net funding paid = 3
        assert state.total_funding == pytest.approx(3.0)
        assert state.cash == pytest.approx(10_000.0 - 3.0)


class TestLiquidationFold:
    def test_liquidation_drops_position_and_books_loss(self):
        state = fold([
            _ev(0, 1, EventKind.FILL, {
                "market": "SOL-PERP", "venue": "drift", "side": "long",
                "size": 1.0, "price": 100.0,
            }),
            _ev(1, 2, EventKind.LIQUIDATION, {
                "market": "SOL-PERP", "venue": "drift", "side": "long",
                "size": 1.0, "entry_price": 100.0, "liq_price": 80.0,
                "loss": -25.0, "penalty": 5.0,
            }),
        ], initial_capital=10_000.0)
        assert state.positions == {}
        assert state.liquidation_count == 1
        assert state.total_fees == pytest.approx(5.0)
        assert state.cash == pytest.approx(10_000.0 - 25.0)


class TestBorrowFold:
    def test_borrow_cost_debits_cash(self):
        state = fold([
            _ev(0, 1, EventKind.BORROW, {"market": "JUP-PERP", "cost": 1.5}),
            _ev(1, 2, EventKind.BORROW, {"market": "JUP-PERP", "cost": 0.5}),
        ], initial_capital=10_000.0)
        assert state.total_borrow_paid == pytest.approx(2.0)
        assert state.cash == pytest.approx(9_998.0)


class TestOrderCounters:
    def test_submit_and_cancel_counts(self):
        state = fold([
            _ev(0, 1, EventKind.ORDER_SUBMIT, {"oid": "o1"}),
            _ev(1, 2, EventKind.ORDER_SUBMIT, {"oid": "o2"}),
            _ev(2, 3, EventKind.ORDER_CANCEL, {"oid": "o2"}),
        ], initial_capital=10_000.0)
        assert state.order_submit_count == 2
        assert state.order_cancel_count == 1
        # No fills → cash unchanged
        assert state.cash == 10_000.0


class TestUnknownKindIgnored:
    def test_forward_compat_unknown_kind(self):
        state = fold([
            _ev(0, 1, "future.event", {"any": "payload"}),
            _ev(1, 2, EventKind.FILL, {
                "market": "SOL-PERP", "venue": "drift", "side": "long",
                "size": 1.0, "price": 100.0, "fee": 0.05,
            }),
        ], initial_capital=10_000.0)
        # Unknown kind didn't crash the fold
        assert state.fill_count == 1


class TestEquityAt:
    def test_equity_includes_unrealized_pnl(self):
        state = fold([
            _ev(0, 1, EventKind.FILL, {
                "market": "SOL-PERP", "venue": "drift", "side": "long",
                "size": 2.0, "price": 100.0,
            }),
        ], initial_capital=10_000.0)
        # Unrealized at 110 = +20
        assert state.equity_at({"SOL-PERP": 110.0}) == pytest.approx(10_020.0)
        # No price for that market — counts as 0 unrealized
        assert state.equity_at({}) == pytest.approx(state.cash)


class TestReplayFromStore:
    def test_replay_reads_from_log_and_filters_by_ts(self, store):
        writer = EventLogWriter(store)
        # Three fills at ts 100, 200, 300
        writer.append("s1", ts=100, kind=EventKind.FILL, payload={
            "market": "SOL-PERP", "venue": "drift", "side": "long",
            "size": 1.0, "price": 100.0,
        })
        writer.append("s1", ts=200, kind=EventKind.FILL, payload={
            "market": "SOL-PERP", "venue": "drift", "side": "long",
            "size": 1.0, "price": 110.0,
        })
        writer.append("s1", ts=300, kind=EventKind.FILL, payload={
            "market": "SOL-PERP", "venue": "drift", "side": "short",
            "size": 2.0, "price": 120.0,
        })

        # Replay at ts=150 should see only the first fill
        state = replay(store, "s1", target_ts=150, initial_capital=10_000.0)
        assert state.fill_count == 1
        assert state.positions[("drift", "SOL-PERP")].size == 1.0

        # Replay at ts=250 should see DCA averaging two opens
        state = replay(store, "s1", target_ts=250, initial_capital=10_000.0)
        assert state.fill_count == 2
        pos = state.positions[("drift", "SOL-PERP")]
        assert pos.size == pytest.approx(2.0)
        assert pos.entry_price == pytest.approx(105.0)

        # Replay at ts=300 should see the close → no positions, +30 realized
        state = replay(store, "s1", target_ts=300, initial_capital=10_000.0)
        assert state.positions == {}
        assert state.realized_pnl == pytest.approx(30.0)

    def test_replay_isolates_by_session_id(self, store):
        writer = EventLogWriter(store)
        writer.append("a", ts=100, kind=EventKind.FILL, payload={
            "market": "SOL-PERP", "venue": "drift", "side": "long",
            "size": 1.0, "price": 100.0,
        })
        writer.append("b", ts=100, kind=EventKind.FILL, payload={
            "market": "SOL-PERP", "venue": "drift", "side": "short",
            "size": 5.0, "price": 100.0,
        })
        state_a = replay(store, "a", target_ts=200, initial_capital=10_000.0)
        state_b = replay(store, "b", target_ts=200, initial_capital=10_000.0)
        assert state_a.positions[("drift", "SOL-PERP")].size == 1.0
        assert state_b.positions[("drift", "SOL-PERP")].size == 5.0

    def test_replay_is_deterministic(self, store):
        """Two replays of the same log produce equal states."""
        writer = EventLogWriter(store)
        for i in range(20):
            writer.append("det", ts=100 + i, kind=EventKind.FILL, payload={
                "market": "SOL-PERP", "venue": "drift",
                "side": "long" if i % 3 != 2 else "short",
                "size": 0.5, "price": 100.0 + i,
                "fee": 0.05,
            })
        a = replay(store, "det", target_ts=200, initial_capital=10_000.0)
        b = replay(store, "det", target_ts=200, initial_capital=10_000.0)
        assert a.cash == b.cash
        assert a.realized_pnl == b.realized_pnl
        assert a.fill_count == b.fill_count
        assert {k: (p.side, p.size, p.entry_price) for k, p in a.positions.items()} == \
               {k: (p.side, p.size, p.entry_price) for k, p in b.positions.items()}


class TestComplexScenario:
    def test_full_lifecycle_open_funding_borrow_close(self, store):
        """Realistic single-trade lifecycle: open → funding payment →
        borrow cost → close. Verify final cash and counters."""
        writer = EventLogWriter(store)
        writer.append("life", ts=100, kind=EventKind.FILL, payload={
            "market": "JUP-PERP", "venue": "jupiter", "side": "long",
            "size": 1.0, "price": 100.0, "fee": 0.10,
        })
        writer.append("life", ts=200, kind=EventKind.FUNDING, payload={
            "market": "JUP-PERP", "payment": 0.5,
        })
        writer.append("life", ts=300, kind=EventKind.BORROW, payload={
            "market": "JUP-PERP", "cost": 1.0,
        })
        writer.append("life", ts=400, kind=EventKind.FILL, payload={
            "market": "JUP-PERP", "venue": "jupiter", "side": "short",
            "size": 1.0, "price": 105.0, "fee": 0.10,
        })

        state = replay(store, "life", target_ts=500, initial_capital=10_000.0)
        # Cash: -0.10 fee, -0.5 funding, -1.0 borrow, +5 PnL, -0.10 fee = +3.30
        assert state.cash == pytest.approx(10_003.30)
        assert state.realized_pnl == pytest.approx(5.0)
        assert state.total_fees == pytest.approx(0.20)
        assert state.total_funding == pytest.approx(0.5)
        assert state.total_borrow_paid == pytest.approx(1.0)
        assert state.positions == {}
