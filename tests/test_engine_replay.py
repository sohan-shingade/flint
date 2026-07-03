"""Slice 3.5 — accounts, the persisted order state machine, and replay/fold.

Three things get proven here, all on hand-built inputs (D26 — never generated
"market-like" data):

1. **The order state machine (§6.2)** moves through pending→placed→partial/
   filled/rejected/cancelled and each transition is persisted as an event.
2. **``client_order_id`` idempotency (§6.2)** — a re-submitted id is recognized
   and never double-filled (the paper-reconnect guard).
3. **Replay/fold (§2.10)** — ``fold(events)`` rebuilds the whole book from the
   log alone, *identical* to the engine's live state, and the same seed replays
   the same event stream byte-for-byte.
"""

from __future__ import annotations

from decimal import Decimal

from flint.adapters import InMemoryUserData
from flint.core.models import (
    Candle,
    FundingRate,
    MarkSnapshot,
    Order,
    OrderbookSnapshot,
    OrderType,
    Position,
    Side,
    Signal,
    TimeInForce,
)
from flint.engine import (
    BacktestEngine,
    EngineConfig,
    OrderStatus,
    PortfolioState,
    fold,
)
from flint.engine.fills import NaiveFillModel
from flint.engine.portfolio import (
    FILL,
    ORDER_CANCELLED,
    ORDER_PLACED,
    ORDER_REJECTED,
    REGISTRY,
    Event,
    EventLog,
)
from flint.ports import TenantContext

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
HOUR_S = 3600
HOUR_MS = 3600 * 1000
T0 = 1_700_000_000_000


def _engine(state=None, config=None, fill_model=None):
    store = InMemoryUserData()
    log = EventLog(store, TenantContext.local(), run_id="run-3.5")
    engine = BacktestEngine(
        log,
        config=config or EngineConfig(),
        state=state or PortfolioState(),
        fill_model=fill_model,
    )
    return engine, log


def _bar(ts: int, open_: float, *, volume: float = 1000.0) -> Candle:
    return Candle(
        ts=ts,
        open=open_,
        high=open_ + 1.0,
        low=open_ - 1.0,
        close=open_,
        volume=volume,
        market=MARKET,
        resolution_s=HOUR_S,
        venue=VENUE,
    )


class _Script:
    """Emit predefined signals on specific bar timestamps (hand-built inputs)."""

    def __init__(self, by_ts: dict[int, list[Signal]]) -> None:
        self._by_ts = by_ts

    def on_candle(self, candle, ctx):
        return self._by_ts.get(candle.ts, [])


def _kinds(log: EventLog) -> list[str]:
    return [e.kind for e in log.read()]


# --- the order state machine (§6.2) ---------------------------------------


def test_market_order_walks_pending_placed_filled():
    # A market long placed on bar0 fills fully at bar1's open → its record ends
    # FILLED, and the log records exactly one placement and one fill.
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, log = _engine(state=state, fill_model=NaiveFillModel())
    script = _Script({T0: [Signal.long(MARKET, VENUE, size=2.0)]})
    engine.run([_bar(T0, 100.0), _bar(T0 + HOUR_MS, 105.0)], strategy=script)

    (coid,) = list(engine._orders)
    rec = engine._orders[coid]
    assert rec.status is OrderStatus.FILLED
    assert rec.filled_size == 2.0
    assert _kinds(log).count(ORDER_PLACED) == 1
    assert _kinds(log).count(FILL) == 1


def test_zero_volume_bar_rejects_the_order():
    # The T+1 bar has no volume and no book → the honest Tier-C model refuses to
    # invent liquidity (D26) and returns nothing → the order is REJECTED (§6.2),
    # not silently dropped.
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, log = _engine(state=state)  # default ClobFillModel
    script = _Script({T0: [Signal.long(MARKET, VENUE, size=1.0)]})
    engine.run(
        [_bar(T0, 100.0), _bar(T0 + HOUR_MS, 100.0, volume=0.0)], strategy=script
    )

    (rec,) = list(engine._orders.values())
    assert rec.status is OrderStatus.REJECTED
    assert rec.reason == "no_fill"
    assert ORDER_REJECTED in _kinds(log)
    assert state.position(VENUE, MARKET) is None


def test_ioc_market_partial_fill_cancels_the_remainder():
    # A size-20 market buy against only 10 units of visible ask depth fills 10 and
    # cancels the IOC remainder → the record ends CANCELLED with 10 filled (§6.2).
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, log = _engine(state=state)  # default ClobFillModel (Tier A with a book)
    book = OrderbookSnapshot(
        MARKET,
        T0 + HOUR_MS,
        ((99.9, 50.0),),
        ((100.0, 5.0), (100.1, 5.0)),  # 10 units of ask depth total
        VENUE,
    )
    script = _Script({T0: [Signal.long(MARKET, VENUE, size=20.0)]})
    engine.run(
        [_bar(T0, 100.0), _bar(T0 + HOUR_MS, 100.0)],
        books={MARKET: [book]},
        strategy=script,
    )

    (rec,) = list(engine._orders.values())
    assert rec.status is OrderStatus.CANCELLED
    assert rec.reason == "ioc_remainder"
    assert rec.filled_size == 10.0
    assert ORDER_CANCELLED in _kinds(log)
    fill_ev = next(e for e in log.read() if e.kind == FILL)
    assert fill_ev.payload["is_partial"] is True


def test_resting_limit_is_cancelled_at_run_end():
    # A limit far below the market never triggers, rests as GTC, and is cancelled
    # when the run ends (§8.1: GTC until cancelled or the run ends) — never left
    # dangling in the state machine.
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, log = _engine(state=state, fill_model=NaiveFillModel())
    script = _Script(
        {T0: [Signal.long(MARKET, VENUE, size=1.0, limit_price=50.0)]}
    )
    engine.run([_bar(T0, 100.0), _bar(T0 + HOUR_MS, 100.0)], strategy=script)

    (rec,) = list(engine._orders.values())
    assert rec.status is OrderStatus.CANCELLED
    assert rec.reason == "run_ended"
    assert engine._resting == []
    assert ORDER_CANCELLED in _kinds(log)


# --- client_order_id idempotency (§6.2) -----------------------------------


def test_duplicate_client_order_id_is_recognized_and_never_double_filled():
    # The same id submitted twice (a paper reconnect) is recognized: the second
    # submit returns the same record, queues nothing new, and only one fill lands.
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, log = _engine(state=state, fill_model=NaiveFillModel())

    o1 = Order(
        market=MARKET,
        side=Side.LONG,
        type=OrderType.MARKET,
        size=3.0,
        client_order_id="reconnect-1",
        venue=VENUE,
        tif=TimeInForce.IOC,
    )
    o2 = Order(
        market=MARKET,
        side=Side.LONG,
        type=OrderType.MARKET,
        size=3.0,
        client_order_id="reconnect-1",  # same id — the duplicate
        venue=VENUE,
        tif=TimeInForce.IOC,
    )
    assert engine._submit(o1) is engine._submit(o2)
    assert len(engine._pending_market) == 1  # queued once, not twice

    engine.run([_bar(T0, 100.0)], strategy=_Script({}))

    assert _kinds(log).count(ORDER_PLACED) == 1
    assert _kinds(log).count(FILL) == 1
    pos = state.position(VENUE, MARKET)
    assert pos is not None
    assert pos.size == 3.0  # filled once, not 6.0


# --- replay/fold reconstructs the whole book (§2.10) -----------------------


def _rich_run():
    """Open, take funding, then a partial-reduce that realizes PnL — one run that
    exercises ORDER_PLACED, FILL (open + reduce), and FUNDING for the fold to
    reconstruct. Returns (engine, log, initial_cash)."""
    initial = Decimal("100000")
    state = PortfolioState()
    state.fund(VENUE, initial)
    engine, log = _engine(state=state, fill_model=NaiveFillModel())

    settle_ts = T0 + HOUR_MS + 12 * 60 * 1000  # inside bar1, after the open fills
    marks = {
        MARKET: [
            MarkSnapshot(
                market=MARKET,
                ts=settle_ts,
                mark_price=105.0,
                index_price=105.0,
                venue=VENUE,
            )
        ]
    }
    funding = {
        MARKET: [
            FundingRate(
                market=MARKET,
                ts=settle_ts,
                rate_hourly=0.0001,
                interval_s=HOUR_S,
                price_basis="oracle",
                rate_type="final",
                venue=VENUE,
            )
        ]
    }
    script = _Script(
        {
            T0: [Signal.long(MARKET, VENUE, size=2.0)],  # fills bar1 @ 105
            T0 + 2 * HOUR_MS: [Signal.short(MARKET, VENUE, size=1.0)],  # reduces bar3 @ 108
        }
    )
    candles = [
        _bar(T0, 100.0),
        _bar(T0 + HOUR_MS, 105.0),
        _bar(T0 + 2 * HOUR_MS, 110.0),
        _bar(T0 + 3 * HOUR_MS, 108.0),
    ]
    engine.run(candles, marks=marks, funding=funding, strategy=script)
    return engine, log, initial


def test_fold_reconstructs_positions_and_cash_identical_to_the_engine():
    engine, log, initial = _rich_run()
    book = fold(log.read())

    # Positions rebuilt from the fills alone — same residual long, same entry, tag.
    assert book.positions == engine.state.positions
    residual = book.positions[(VENUE, MARKET)]
    assert residual.side is Side.LONG
    assert residual.size == 1.0  # opened 2, reduced 1
    assert residual.entry_price == 105.0

    # Counters move only via events → they match exactly; cash matches up to the
    # initial funding (which is set directly, not via an event).
    acct = engine.state.account(VENUE)
    facct = book.accounts[VENUE]
    assert facct.fees_paid == acct.fees_paid
    assert facct.funding_paid == acct.funding_paid
    assert facct.realized_pnl == acct.realized_pnl
    assert facct.realized_pnl == Decimal("3.0")  # (108-105) × 1
    assert facct.cash == acct.cash - initial


def test_fold_reconstructs_the_order_state_machine():
    engine, log, _ = _rich_run()
    book = fold(log.read())

    assert set(book.orders) == set(engine._orders)
    for coid, rec in book.orders.items():
        live = engine._orders[coid]
        assert rec.status is live.status
        assert rec.filled_size == live.filled_size
    # both orders filled (the open in full, the reduce in full)
    assert all(r.status is OrderStatus.FILLED for r in book.orders.values())


def test_duplicate_order_placed_folds_once_idempotently():
    # A duplicated ORDER_PLACED (e.g. a persisted reconnect) must fold to a single
    # order, not two — the fold-side of the §6.2 idempotency guarantee.
    engine, log, _ = _rich_run()
    rows = [e.to_row() for e in log.read()]
    placed = next(r for r in rows if r["kind"] == ORDER_PLACED)
    doubled = [Event.from_row(r) for r in rows] + [Event.from_row(placed)]
    book = fold(doubled)
    assert len(book.orders) == len(engine._orders)


# --- deterministic reproducibility (§19.3) --------------------------------


def _seeded_run():
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, log = _engine(state=state, config=EngineConfig(rng_seed=7))
    book = OrderbookSnapshot(
        MARKET, T0 + HOUR_MS, ((99.9, 50.0),), ((100.0, 50.0),), VENUE
    )
    script = _Script({T0: [Signal.long(MARKET, VENUE, size=3.0)]})
    engine.run(
        [_bar(T0, 100.0), _bar(T0 + HOUR_MS, 100.0)],
        books={MARKET: [book]},
        strategy=script,
    )
    return log


def test_same_seed_replays_an_identical_event_stream():
    # The latency draws come from ctx.rng; the same seed must produce a byte-for-
    # byte identical persisted event stream (§19.3 reproducibility).
    rows_a = _seeded_run().read_raw()
    rows_b = _seeded_run().read_raw()
    assert rows_a == rows_b
    assert any(r["kind"] == FILL for r in rows_a)  # the run actually did something


# --- FILL v1→v2 upcaster (the replay substrate) ---------------------------


def test_fill_v1_row_upcasts_to_v2_with_margin_mode():
    # An old persisted FILL (v1, no margin_mode) upcasts on read to v2 with the
    # honest cross default — so pre-margin-mode runs stay foldable (§2.10).
    v1_row = {
        "kind": FILL,
        "event_version": 1,
        "ts": T0,
        "seq": 0,
        "payload": {"market": MARKET, "venue": VENUE, "size": 1.0},
    }
    upcast = REGISTRY.upcast_to_latest(v1_row)
    assert upcast["event_version"] == 2
    assert upcast["payload"]["margin_mode"] == "cross"


# --- account structure: cross pool vs isolated margin (§6.5/§6.6) ---------


def test_cross_margin_available_excludes_locked_isolated_margin():
    # Venue equity is shared by cross positions but an isolated position's
    # dedicated margin is walled off — cross_margin_available reflects that (§6.5).
    state = PortfolioState()
    state.fund(VENUE, "1000")
    state.positions[(VENUE, MARKET)] = Position(
        market=MARKET, venue=VENUE, side=Side.LONG, size=1.0,
        entry_price=100.0, margin_mode="isolated", isolated_margin=200.0,
    )
    marks = {MARKET: 100.0}
    # equity = 1000 cash + 0 unrealized; cross pool excludes the 200 isolated.
    assert state.equity(VENUE, marks) == 1000.0
    assert state.cross_margin_available(VENUE, marks) == 800.0
