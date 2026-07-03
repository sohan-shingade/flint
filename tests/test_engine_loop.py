"""The per-bar loop: locked ordering, T+1 execution, intrabar policy (§6.1).

The centrepiece is the **funding-before-liquidation** golden from §6.1: a funding
receipt landing mid-bar rescues a position a naive close-price check would have
liquidated, and the opposite debit correctly pushes one under. These are
hand-authored unit inputs (D26 — never generated "market-like" data) with exact
expected outputs.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from flint.adapters import InMemoryUserData
from flint.core.models import (
    Candle,
    FundingRate,
    MarkSnapshot,
    OrderbookSnapshot,
    Position,
    Side,
    Signal,
)
from flint.engine import BacktestEngine, EngineConfig, PortfolioState
from flint.engine.fills import NaiveFillModel, TradePrint
from flint.engine.funding.settlement import FundingCoverageError
from flint.engine.portfolio import FILL, FUNDING, LIQUIDATION, EventLog
from flint.ports import TenantContext

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
HOUR_S = 3600
HOUR_MS = 3600 * 1000
T0 = 1_700_000_000_000  # a bar start (unix ms)
SETTLE_TS = T0 + 12 * 60 * 1000  # a funding settlement at minute 12 of the bar


def _engine(state=None, config=None, fill_model=None):
    store = InMemoryUserData()
    log = EventLog(store, TenantContext.local(), run_id="run-3.1")
    engine = BacktestEngine(
        log,
        config=config or EngineConfig(),
        state=state or PortfolioState(),
        fill_model=fill_model,
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


# --- §6.4 funding engine: worked example, predicted/final, cap, coverage ---


def _quiet_bar() -> Candle:
    """A bar with the mark pinned to entry — no liquidation pressure, so a test
    isolates the funding payment."""
    return _bar(high=101.0, low=99.0, close=100.0)


def test_funding_6_4_golden_long_pays_at_plus_one_bp():
    # §6.4 worked example: long 10 SOL, settlement oracle 100.00, final +0.01%/hr.
    # Payment = size × oracle × rate = 10 × 100 × 0.0001 = $0.10; rate positive →
    # long PAYS → −$0.10. NOTE: the spec's prose states "$1.00", but that is a 10×
    # arithmetic slip (0.01% = 0.0001, and 10 × 100 × 0.0001 = 0.10). The engine
    # implements the stated formula on the stated inputs and is correct at $0.10;
    # the spec number is flagged to team-lead (D14 — verify the arithmetic, don't
    # propagate a mis-stated result).
    state = _long_10_at_100("1000")
    engine, log = _engine(state=state)
    engine.run(
        [_quiet_bar()],
        marks={MARKET: [_mark(mark_price=100.0, index_price=100.0)]},
        funding={MARKET: [_funding(rate_hourly=0.0001)]},
    )
    ev = _first(log, FUNDING)
    assert Decimal(ev.payload["amount"]) == Decimal("-0.10")
    assert state.account(VENUE).cash == Decimal("999.90")
    assert state.account(VENUE).funding_paid == Decimal("-0.10")


class _FundingProbe:
    """Records what ``ctx.funding_rate`` returns on the bar it runs."""

    def __init__(self) -> None:
        self.seen = None

    def on_candle(self, candle, ctx):
        self.seen = ctx.funding_rate(MARKET)
        return []


def test_settlement_uses_final_while_strategy_sees_only_predicted():
    # The predicted/final split (§6.4): a predicted rate published BEFORE the bar
    # (+2%/hr) and a materially different final rate that settles IN the bar
    # (+0.01%/hr). The strategy must see PREDICTED; the payment must use FINAL —
    # leaking the settled rate would inflate a funding-arb backtest.
    predicted = FundingRate(
        market=MARKET,
        ts=T0 - 30 * 60 * 1000,  # published half an hour before bar start
        rate_hourly=0.02,
        interval_s=HOUR_S,
        price_basis="oracle",
        rate_type="predicted",
        venue=VENUE,
    )
    state = _long_10_at_100("1000")
    engine, log = _engine(state=state)
    probe = _FundingProbe()
    engine.run(
        [_quiet_bar()],
        marks={MARKET: [_mark(mark_price=100.0, index_price=100.0)]},
        funding={MARKET: [predicted, _funding(rate_hourly=0.0001)]},
        strategy=probe,
    )
    # Strategy knew only the predicted rate.
    assert probe.seen is not None
    assert probe.seen.rate_type == "predicted"
    assert probe.seen.rate_hourly == 0.02
    # The payment used the FINAL rate (−$0.10 = 10×100×0.0001), not predicted 2%.
    ev = _first(log, FUNDING)
    assert ev.payload["rate_type"] == "final"
    assert Decimal(ev.payload["amount"]) == Decimal("-0.10")


def test_predicted_rate_in_the_bar_is_never_settled():
    # A predicted rate whose ts lands inside the bar must not move cash — only
    # final rates settle (§6.4). No final row → no FUNDING event, no cash change.
    state = _long_10_at_100("1000")
    engine, log = _engine(state=state)
    engine.run(
        [_quiet_bar()],
        marks={MARKET: [_mark(mark_price=100.0, index_price=100.0)]},
        funding={MARKET: [_funding(rate_hourly=0.02, rate_type="predicted")]},
    )
    assert FUNDING not in _kinds(log)
    assert state.account(VENUE).cash == Decimal("1000")


def test_funding_rate_is_clamped_to_the_venue_cap():
    # A fixture final rate of +10%/hr exceeds HL's 4%/hr cap; the settlement clamps
    # to 0.04, flags it, and pays 10 × 100 × 0.04 = −$40 (not −$100).
    state = _long_10_at_100("1000")
    engine, log = _engine(state=state)
    engine.run(
        [_quiet_bar()],
        marks={MARKET: [_mark(mark_price=100.0, index_price=100.0)]},
        funding={MARKET: [_funding(rate_hourly=0.10)]},
    )
    ev = _first(log, FUNDING)
    assert ev.payload["rate_capped"] is True
    assert ev.payload["settled_rate_hourly"] == 0.04
    assert Decimal(ev.payload["amount"]) == Decimal("-40")


def test_mark_basis_8h_funding_prices_on_mark_and_scales_by_interval():
    # A Binance-style row: price_basis="mark", 8h interval. The payment prices on
    # the MARK (not the index) and scales the hourly-normalized rate by 8h/1h = 8.
    mark = MarkSnapshot(
        market=MARKET, ts=SETTLE_TS, mark_price=100.0, index_price=90.0, venue=VENUE
    )
    rate = FundingRate(
        market=MARKET,
        ts=SETTLE_TS,
        rate_hourly=0.001,
        interval_s=8 * HOUR_S,
        price_basis="mark",
        rate_type="final",
        venue=VENUE,
    )
    state = _long_10_at_100("100000")
    engine, log = _engine(state=state)
    engine.run([_quiet_bar()], marks={MARKET: [mark]}, funding={MARKET: [rate]})
    ev = _first(log, FUNDING)
    assert ev.payload["oracle_price"] == 100.0  # priced on mark 100, not index 90
    assert ev.payload["interval_s"] == 8 * HOUR_S
    assert Decimal(ev.payload["amount"]) == Decimal("-8.00")  # 10×100×0.001×8


def test_missing_oracle_snapshot_hard_gates_instead_of_pricing_on_close():
    # §6.4/§16: an oracle-priced settlement with no mark snapshot to read must fail
    # loud, not silently price on the bar close (which can differ by percent).
    state = _long_10_at_100("1000")
    engine, _ = _engine(state=state)
    with pytest.raises(FundingCoverageError):
        engine.run(
            [_quiet_bar()],  # no marks supplied
            funding={MARKET: [_funding(rate_hourly=0.0001)]},
        )


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
    # Pin the naive fill so this test isolates T+1 *timing* from fill fidelity.
    engine, _ = _engine(state=state, fill_model=NaiveFillModel())

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
    engine, _ = _engine(state=state, fill_model=NaiveFillModel())
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


def test_loop_market_order_walks_a_provided_book_as_tier_a():
    # With a recorded book + trades, the default CLOB model walks real depth and
    # records the fill as Tier A — end-to-end through the loop's T+1 path.
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, log = _engine(state=state)  # default ClobFillModel
    bar0 = Candle(T0, 100.0, 101.0, 99.0, 100.5, 500.0, MARKET, HOUR_S, VENUE)
    bar1 = Candle(T0 + HOUR_MS, 100.0, 101.0, 99.0, 100.5, 500.0, MARKET, HOUR_S, VENUE)
    book = OrderbookSnapshot(
        MARKET, T0 + HOUR_MS, ((99.9, 10.0),), ((100.0, 5.0), (100.1, 5.0)), VENUE
    )
    engine.run(
        [bar0, bar1],
        books={MARKET: [book]},
        trades={MARKET: [TradePrint(100.0, 1.0, T0 + HOUR_MS)]},
        strategy=_OpenOnceStrategy(),
    )
    pos = state.position(VENUE, MARKET)
    assert pos is not None
    assert pos.entry_price == pytest.approx(100.0)  # walked the 100.0 ask level
    fill_ev = next(e for e in log.read() if e.kind == FILL)
    assert fill_ev.payload["fidelity_tier"] == "A"
    assert fill_ev.payload["liquidity"] == "taker"


def test_loop_market_order_without_a_book_is_tier_c_and_flags_flow_to_the_event():
    # No book fixture → Tier-C parametric fill; the uncalibrated flag reaches the
    # FILL event payload so the tearsheet can show it.
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, log = _engine(state=state)  # default ClobFillModel
    bar0 = Candle(T0, 100.0, 101.0, 99.0, 100.5, 500.0, MARKET, HOUR_S, VENUE)
    bar1 = Candle(T0 + HOUR_MS, 100.0, 101.0, 99.0, 100.5, 500.0, MARKET, HOUR_S, VENUE)
    engine.run([bar0, bar1], strategy=_OpenOnceStrategy())
    fill_ev = next(e for e in log.read() if e.kind == FILL)
    assert fill_ev.payload["fidelity_tier"] == "C"
    assert "uncalibrated" in fill_ev.payload["flags"]


def test_run_brackets_events_with_run_started_and_finished():
    engine, log = _engine()
    engine.run([_bar()])
    kinds = _kinds(log)
    assert kinds[0] == "run_started"
    assert kinds[-1] == "run_finished"


def test_close_signal_routes_a_reduce_only_fill_next_bar():
    # Open a position, then a close signal reduces it to flat via the T+1 path.
    state = _long_10_at_100("1000")
    engine, log = _engine(state=state, fill_model=NaiveFillModel())

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
