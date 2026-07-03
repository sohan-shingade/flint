"""The per-bar loop: locked ordering, T+1 execution, intrabar policy (§6.1).

The centrepiece is the **funding-before-liquidation** golden from §6.1: a funding
receipt landing mid-bar rescues a position a naive close-price check would have
liquidated, and the opposite debit correctly pushes one under. These are
hand-authored unit inputs (D26 — never generated "market-like" data) with exact
expected outputs.
"""

from __future__ import annotations

from decimal import Decimal

from flint.adapters import InMemoryUserData
from flint.core.models import (
    Candle,
    FundingRate,
    MarkSnapshot,
    Position,
    Side,
    Signal,
)
from flint.engine import BacktestEngine, EngineConfig, PortfolioState
from flint.engine.portfolio import FILL, FUNDING, LIQUIDATION, EventLog
from flint.ports import TenantContext

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
HOUR_S = 3600
HOUR_MS = 3600 * 1000
T0 = 1_700_000_000_000  # a bar start (unix ms)
SETTLE_TS = T0 + 12 * 60 * 1000  # a funding settlement at minute 12 of the bar


def _engine(state=None, config=None):
    store = InMemoryUserData()
    log = EventLog(store, TenantContext.local(), run_id="run-3.1")
    engine = BacktestEngine(
        log, config=config or EngineConfig(), state=state or PortfolioState()
    )
    return engine, log


def _long_10_at_100(cash: str) -> PortfolioState:
    state = PortfolioState()
    state.fund(VENUE, cash)
    state.positions[(VENUE, MARKET)] = Position(
        market=MARKET,
        venue=VENUE,
        side=Side.LONG,
        size=10.0,
        entry_price=100.0,
        margin_mode="cross",
    )
    return state


def _bar(open_=100.0, high=100.0, low=97.0, close=98.0) -> Candle:
    return Candle(
        ts=T0,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
        market=MARKET,
        resolution_s=HOUR_S,
        venue=VENUE,
    )


def _mark(mark_price=97.0, index_price=100.0, ts=SETTLE_TS) -> MarkSnapshot:
    return MarkSnapshot(
        market=MARKET,
        ts=ts,
        mark_price=mark_price,
        index_price=index_price,
        venue=VENUE,
    )


def _funding(rate_hourly: float, rate_type="final") -> FundingRate:
    return FundingRate(
        market=MARKET,
        ts=SETTLE_TS,
        rate_hourly=rate_hourly,
        interval_s=HOUR_S,
        price_basis="oracle",
        rate_type=rate_type,
        venue=VENUE,
    )


def _kinds(log: EventLog) -> list[str]:
    return [e.kind for e in log.read()]


def _first(log: EventLog, kind: str):
    return next(e for e in log.read() if e.kind == kind)


# --- §6.1 funding-saves-position golden -----------------------------------


def test_funding_receipt_rescues_a_position_that_would_otherwise_liquidate():
    # long 10 @ 100, cash 50; the bar's adverse mark dips to 97. Without funding
    # the position is under maintenance; a +$30 receipt at minute 12 saves it.
    state = _long_10_at_100("50")
    engine, log = _engine(state=state)

    # Counterfactual the lock protects: pre-funding equity is BELOW maintenance.
    pre_funding_equity = 50.0 + (97.0 - 100.0) * 10.0  # = 20.0
    maintenance = 0.025 * 97.0 * 10.0  # = 24.25
    assert pre_funding_equity < maintenance  # would liquidate if checked first

    engine.run(
        [_bar()],
        marks={MARKET: [_mark()]},
        funding={MARKET: [_funding(rate_hourly=-0.03)]},  # negative → long receives
    )

    # Funding settled first (+$30) → equity 50 ≥ maintenance → position SURVIVES.
    pos = state.position(VENUE, MARKET)
    assert pos is not None
    assert state.account(VENUE).cash == Decimal("80")
    assert LIQUIDATION not in _kinds(log)
    funding_ev = _first(log, FUNDING)
    assert Decimal(funding_ev.payload["amount"]) == Decimal("30")


def test_funding_debit_pushes_the_same_position_under_and_it_liquidates():
    state = _long_10_at_100("50")
    engine, log = _engine(state=state)

    engine.run(
        [_bar()],
        marks={MARKET: [_mark()]},
        funding={MARKET: [_funding(rate_hourly=+0.03)]},  # positive → long pays
    )

    # −$30 debit settles first → equity −10 < maintenance → LIQUIDATED.
    assert state.position(VENUE, MARKET) is None
    assert LIQUIDATION in _kinds(log)
    liq = _first(log, LIQUIDATION)
    assert liq.payload["mark_price"] == 97.0  # liquidation priced on the MARK


def test_funding_is_priced_on_the_oracle_not_the_mark():
    # index (oracle) 100, mark 97 in the same snapshot: funding uses 100, the
    # liquidation check uses 97 — the two-price separation (§2.4/§6.4).
    state = _long_10_at_100("50")
    engine, log = _engine(state=state)
    engine.run(
        [_bar()],
        marks={MARKET: [_mark(mark_price=97.0, index_price=100.0)]},
        funding={MARKET: [_funding(rate_hourly=-0.03)]},
    )
    funding_ev = _first(log, FUNDING)
    assert funding_ev.payload["oracle_price"] == 100.0
    assert funding_ev.ts == SETTLE_TS  # charged at the settlement second


# --- T+1 execution --------------------------------------------------------


class _OpenOnceStrategy:
    """Emits a single market long on the first bar, nothing after."""

    def __init__(self) -> None:
        self._fired = False

    def on_candle(self, candle, ctx):
        if self._fired:
            return []
        self._fired = True
        return [Signal.long(MARKET, VENUE, size=1.0)]


def test_market_order_fills_at_the_next_bars_open_not_this_bars_close():
    state = PortfolioState()
    state.fund(VENUE, "1000")
    engine, _ = _engine(state=state)

    bar0 = Candle(T0, 100.0, 101.0, 99.0, 100.5, 500.0, MARKET, HOUR_S, VENUE)
    bar1 = Candle(T0 + HOUR_MS, 105.0, 106.0, 104.0, 105.5, 500.0, MARKET, HOUR_S, VENUE)
    engine.run([bar0, bar1], strategy=_OpenOnceStrategy())

    pos = state.position(VENUE, MARKET)
    assert pos is not None
    # Filled at bar1's OPEN (105.0), never bar0's close (100.5) or open (100.0).
    assert pos.entry_price == 105.0
    assert pos.size == 1.0


def test_market_order_is_not_filled_on_its_own_decision_bar():
    state = PortfolioState()
    state.fund(VENUE, "1000")
    engine, _ = _engine(state=state)
    bar0 = Candle(T0, 100.0, 101.0, 99.0, 100.5, 500.0, MARKET, HOUR_S, VENUE)
    engine.run([bar0], strategy=_OpenOnceStrategy())
    # Only one bar: the order is queued for a T+1 that never comes → no position.
    assert state.position(VENUE, MARKET) is None


# --- intrabar adverse-extreme-first policy + flagging (Tier C) -------------


def test_tier_c_liquidation_uses_the_adverse_extreme_and_is_flagged_ambiguous():
    # OHLCV-only bar (no mark snapshots): the liquidation is evaluated on the
    # candle's adverse extreme (low, for a long) and flagged intrabar_ambiguous.
    state = _long_10_at_100("30")
    engine, log = _engine(state=state)
    engine.run([_bar(low=96.0)])  # no marks → Tier C

    assert state.position(VENUE, MARKET) is None
    liq = _first(log, LIQUIDATION)
    assert liq.payload["mark_price"] == 96.0  # the adverse extreme, not close
    assert liq.payload["intrabar_ambiguous"] is True


def test_liquidation_with_recorded_marks_is_not_flagged_ambiguous():
    state = _long_10_at_100("30")
    engine, log = _engine(state=state)
    # A mark snapshot in the bar → Tier A/B → path known enough → not flagged.
    engine.run([_bar(low=96.0)], marks={MARKET: [_mark(mark_price=96.0)]})
    liq = _first(log, LIQUIDATION)
    assert liq.payload["intrabar_ambiguous"] is False


# --- lifecycle + shared fill path ------------------------------------------


def test_run_brackets_events_with_run_started_and_finished():
    engine, log = _engine()
    engine.run([_bar()])
    kinds = _kinds(log)
    assert kinds[0] == "run_started"
    assert kinds[-1] == "run_finished"


def test_close_signal_routes_a_reduce_only_fill_next_bar():
    # Open a position, then a close signal reduces it to flat via the T+1 path.
    state = _long_10_at_100("1000")
    engine, log = _engine(state=state)

    class _CloseOnce:
        def __init__(self):
            self._fired = False

        def on_candle(self, candle, ctx):
            if self._fired:
                return []
            self._fired = True
            return [Signal.close(MARKET, VENUE)]

    bar0 = Candle(T0, 100.0, 100.0, 100.0, 100.0, 10.0, MARKET, HOUR_S, VENUE)
    bar1 = Candle(T0 + HOUR_MS, 100.0, 100.0, 100.0, 100.0, 10.0, MARKET, HOUR_S, VENUE)
    engine.run([bar0, bar1], strategy=_CloseOnce())

    assert state.position(VENUE, MARKET) is None
    assert FILL in _kinds(log)
