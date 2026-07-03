"""Per-bar EQUITY event: golden components + replay-fold reproduction (§6.1, slice 7.3).

The engine emits one additive EQUITY snapshot at the end of every locked per-bar
sequence so the tearsheet can fold an equity curve without re-deriving it. This
pins the contract on a hand-authored fixture (D26): a pre-seeded long position
over two bars with one funding settlement, so the funding and unrealized
components are known exactly and asserted **separately**. It also checks the
event is purely additive — it moves no cash, replay ``fold`` ignores it, and no
existing kind or ordering changes — and that the folded ledger reproduces the
snapshot's cash/funding/equity.
"""

from __future__ import annotations

from decimal import Decimal

from flint.adapters import InMemoryUserData
from flint.core.models import Candle, FundingRate, MarkSnapshot, Position, Side, Signal
from flint.engine import BacktestEngine, EngineConfig, PortfolioState
from flint.engine.fills import NaiveFillModel
from flint.engine.portfolio import (
    FUNDING,
    RUN_FINISHED,
    RUN_STARTED,
    EventLog,
    fold,
)
from flint.engine.portfolio.events import EQUITY
from flint.ports import TenantContext


class _Script:
    """Minimal strategy: emit the signals scheduled for each candle's ts."""

    def __init__(self, by_ts):
        self._by_ts = by_ts

    def on_candle(self, candle, ctx):
        return self._by_ts.get(candle.ts, [])

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
HOUR_S = 3600
HOUR_MS = 3600 * 1000
T0 = 1_700_000_000_000


def _engine(state):
    store = InMemoryUserData()
    log = EventLog(store, TenantContext.local(), run_id="run-7.3")
    engine = BacktestEngine(log, config=EngineConfig(), state=state)
    return engine, log


def _long_10_at_100(cash: str) -> PortfolioState:
    state = PortfolioState()
    state.fund(VENUE, cash)
    state.positions[(VENUE, MARKET)] = Position(
        market=MARKET, venue=VENUE, side=Side.LONG, size=10.0,
        entry_price=100.0, margin_mode="cross",
    )
    return state


def _bar(ts: int, close: float) -> Candle:
    return Candle(ts=ts, open=100.0, high=max(100.0, close) + 1, low=min(100.0, close) - 1,
                  close=close, volume=1000.0, market=MARKET, resolution_s=HOUR_S, venue=VENUE)


def _mark(ts: int, price: float) -> MarkSnapshot:
    return MarkSnapshot(market=MARKET, ts=ts, mark_price=price, index_price=price, venue=VENUE)


def _funding(ts: int, rate_hourly: float) -> FundingRate:
    return FundingRate(market=MARKET, ts=ts, rate_hourly=rate_hourly, interval_s=HOUR_S,
                       price_basis="oracle", rate_type="final", venue=VENUE)


def _equity_events(log):
    return [e for e in log.read() if e.kind == EQUITY]


# --- the golden: two bars, one funding settlement, a mark move ---------------


def test_per_bar_equity_components_are_the_golden_values():
    # Long 10 @ 100, $10,000 cash. Bar 1: mark flat at 100, funding +1%/hr → long
    # pays 10×100×0.01 = $10. Bar 2: mark rises to 105 → unrealized (105-100)×10 = $50,
    # no funding. Funding and unrealized are distinct lines of equity.
    settle1 = T0 + 12 * 60 * 1000
    settle2 = T0 + HOUR_MS + 12 * 60 * 1000
    state = _long_10_at_100("10000")
    engine, log = _engine(state)
    engine.run(
        [_bar(T0, close=100.0), _bar(T0 + HOUR_MS, close=105.0)],
        marks={MARKET: [_mark(settle1, 100.0), _mark(settle2, 105.0)]},
        funding={MARKET: [_funding(settle1, 0.01)]},  # only bar 1 settles
    )

    eq = _equity_events(log)
    assert len(eq) == 2  # one per bar, unconditionally
    assert [e.ts for e in eq] == [T0, T0 + HOUR_MS]
    assert all(e.event_version == 1 for e in eq)

    b1, b2 = eq[0].payload, eq[1].payload
    # Bar 1 — funding paid, no price move.
    assert Decimal(b1["accrued_funding"]) == Decimal("-10")
    assert Decimal(b1["unrealized"]) == Decimal("0")
    assert Decimal(b1["cash"]) == Decimal("9990")  # 10000 − 10 funding
    assert Decimal(b1["equity"]) == Decimal("9990")
    # Bar 2 — mark rose, funding is cumulative (still −10, none this bar).
    assert Decimal(b2["accrued_funding"]) == Decimal("-10")
    assert Decimal(b2["unrealized"]) == Decimal("50")
    assert Decimal(b2["cash"]) == Decimal("9990")
    assert Decimal(b2["equity"]) == Decimal("10040")  # 9990 + 50


def test_equity_identity_holds_every_bar():
    settle1 = T0 + 12 * 60 * 1000
    settle2 = T0 + HOUR_MS + 12 * 60 * 1000
    state = _long_10_at_100("10000")
    engine, log = _engine(state)
    engine.run(
        [_bar(T0, 100.0), _bar(T0 + HOUR_MS, 105.0)],
        marks={MARKET: [_mark(settle1, 100.0), _mark(settle2, 105.0)]},
        funding={MARKET: [_funding(settle1, 0.01)]},
    )
    for e in _equity_events(log):
        p = e.payload
        assert Decimal(p["equity"]) == Decimal(p["cash"]) + Decimal(p["unrealized"])


# --- additive: no cash moved, no kind/ordering changed ----------------------


def test_equity_event_is_additive_and_ordered():
    settle1 = T0 + 12 * 60 * 1000
    settle2 = T0 + HOUR_MS + 12 * 60 * 1000
    state = _long_10_at_100("10000")
    engine, log = _engine(state)
    engine.run(
        [_bar(T0, 100.0), _bar(T0 + HOUR_MS, 105.0)],
        marks={MARKET: [_mark(settle1, 100.0), _mark(settle2, 105.0)]},
        funding={MARKET: [_funding(settle1, 0.01)]},
    )
    kinds = [e.kind for e in log.read()]
    assert kinds[0] == RUN_STARTED
    assert kinds[-1] == RUN_FINISHED  # EQUITY emits inside the run, never last
    assert kinds.count(FUNDING) == 1  # unchanged by the EQUITY addition
    assert kinds.count(EQUITY) == 2


def test_replay_fold_reproduces_the_snapshot():
    # Event-source the whole run (open via a T+1 market order) so replay fold can
    # rebuild the full ledger AND position from the log alone — EQUITY itself is
    # ignored by fold. The seed cash is an initial condition, not an event, so the
    # curve is reproduced *given* it:
    #   snapshot.cash    == seed + folded cash deltas (fee + funding)
    #   snapshot.funding == folded funding_paid                     (exact)
    #   snapshot.equity  == seed + folded cash + unrealized(folded position, mark)
    seed = Decimal("100000")
    settle1 = T0 + HOUR_MS + 12 * 60 * 1000  # funding lands in bar 2, after the fill
    last_mark = 105.0
    state = PortfolioState()
    state.fund(VENUE, str(seed))
    store = InMemoryUserData()
    log = EventLog(store, TenantContext.local(), run_id="run-7.3-fold")
    engine = BacktestEngine(log, config=EngineConfig(), state=state,
                            fill_model=NaiveFillModel())
    script = _Script({T0: [Signal.long(MARKET, VENUE, size=10.0)]})
    engine.run(
        [_bar(T0, 100.0), _bar(T0 + HOUR_MS, 100.0), _bar(T0 + 2 * HOUR_MS, last_mark)],
        marks={MARKET: [_mark(settle1, 100.0), _mark(T0 + 2 * HOUR_MS + 60_000, last_mark)]},
        funding={MARKET: [_funding(settle1, 0.01)]},
        strategy=script,
    )

    book = fold(log.read())
    last = _equity_events(log)[-1].payload
    assert seed + book.accounts[VENUE].cash == Decimal(last["cash"])
    assert book.accounts[VENUE].funding_paid == Decimal(last["accrued_funding"])
    pos = book.positions[(VENUE, MARKET)]
    rebuilt = seed + book.accounts[VENUE].cash + Decimal(str(pos.unrealized_pnl(last_mark)))
    assert rebuilt == Decimal(last["equity"])


# --- per-bar guarantee on an idle portfolio ---------------------------------


def test_equity_emitted_even_on_a_flat_bar():
    # No position, no funding: a bar still emits one EQUITY snapshot = pure cash.
    state = PortfolioState()
    state.fund(VENUE, "5000")
    engine, log = _engine(state)
    engine.run([_bar(T0, 100.0)], marks={MARKET: [_mark(T0 + 60_000, 100.0)]})
    eq = _equity_events(log)
    assert len(eq) == 1
    p = eq[0].payload
    assert Decimal(p["cash"]) == Decimal("5000")
    assert Decimal(p["unrealized"]) == Decimal("0")
    assert Decimal(p["accrued_funding"]) == Decimal("0")
    assert Decimal(p["equity"]) == Decimal("5000")
