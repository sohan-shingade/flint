"""Bar-lane semantics: locked ordering, T+1 execution, intrabar policy (§6.1).

The centrepiece is the **funding-before-liquidation** golden from §6.1: a funding
receipt landing mid-bar rescues a position a naive close-price check would have
liquidated, and the opposite debit correctly pushes one under. These are
hand-authored unit inputs (D26 — never generated "market-like" data) with exact
expected outputs, run through the engine seam on the Nautilus substrate (the only
one since N10). Seeded starting books ride the warm-start path
(``EngineRunSpec.initial_state``, §6.7) — the same path a resumed paper session
uses — so these tests also pin that the carried book participates in funding,
liquidation, and closes exactly like a book built from fills.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytest.importorskip("nautilus_trader")

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
from flint.engine import EngineConfig, PortfolioState
from flint.engine.api import EngineFeed, EngineRunSpec
from flint.engine.fills import TradePrint
from flint.engine.funding.settlement import FundingCoverageError
from flint.engine.loop import NoopStrategy
from flint.engine.portfolio import FILL, FUNDING, LIQUIDATION, EventLog
from flint.engine.select import engine_for
from flint.ports import TenantContext
from flint.venues import HYPERLIQUID

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
HOUR_S = 3600
HOUR_MS = 3600 * 1000
T0 = 1_700_000_000_000  # a bar start (unix ms)
SETTLE_TS = T0 + 12 * 60 * 1000  # a funding settlement at minute 12 of the bar


def _run(
    candles,
    *,
    state: PortfolioState | None = None,
    marks=None,
    funding=None,
    books=None,
    trades=None,
    strategy=None,
    run_id: str = "run-3.1",
):
    """Run the seam's engine over hand-authored inputs; return (state, log)."""
    log = EventLog(InMemoryUserData(), TenantContext.local(), run_id=run_id)
    spec = EngineRunSpec(
        config=EngineConfig(),
        venue_spec=HYPERLIQUID,
        fund_venue=VENUE,
        mark_policy="close_derived",
        initial_state=state,
    )
    feed = EngineFeed(
        candles=list(candles),
        marks=marks or {},
        funding=funding or {},
        books=books or {},
        trades=trades or {},
    )
    final = engine_for("nautilus")().run(
        feed, strategy or NoopStrategy(), event_log=log, spec=spec
    )
    return final, log


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

    # Counterfactual the lock protects: pre-funding equity is BELOW maintenance.
    pre_funding_equity = 50.0 + (97.0 - 100.0) * 10.0  # = 20.0
    maintenance = 0.025 * 97.0 * 10.0  # = 24.25
    assert pre_funding_equity < maintenance  # would liquidate if checked first

    state, log = _run(
        [_bar()],
        state=state,
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
    state, log = _run(
        [_bar()],
        state=_long_10_at_100("50"),
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
    state, log = _run(
        [_bar()],
        state=_long_10_at_100("50"),
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
    state, log = _run(
        [_quiet_bar()],
        state=_long_10_at_100("1000"),
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
    probe = _FundingProbe()
    state, log = _run(
        [_quiet_bar()],
        state=_long_10_at_100("1000"),
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
    state, log = _run(
        [_quiet_bar()],
        state=_long_10_at_100("1000"),
        marks={MARKET: [_mark(mark_price=100.0, index_price=100.0)]},
        funding={MARKET: [_funding(rate_hourly=0.02, rate_type="predicted")]},
    )
    assert FUNDING not in _kinds(log)
    assert state.account(VENUE).cash == Decimal("1000")


def test_funding_rate_is_clamped_to_the_venue_cap():
    # A fixture final rate of +10%/hr exceeds HL's 4%/hr cap; the settlement clamps
    # to 0.04, flags it, and pays 10 × 100 × 0.04 = −$40 (not −$100).
    state, log = _run(
        [_quiet_bar()],
        state=_long_10_at_100("1000"),
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
    state, log = _run(
        [_quiet_bar()],
        state=_long_10_at_100("100000"),
        marks={MARKET: [mark]},
        funding={MARKET: [rate]},
    )
    ev = _first(log, FUNDING)
    assert ev.payload["oracle_price"] == 100.0  # priced on mark 100, not index 90
    assert ev.payload["interval_s"] == 8 * HOUR_S
    assert Decimal(ev.payload["amount"]) == Decimal("-8.00")  # 10×100×0.001×8


def test_missing_oracle_snapshot_hard_gates_instead_of_pricing_on_close():
    # §6.4/§16: an oracle-priced settlement with no mark snapshot to read must fail
    # loud, not silently price on the bar close (which can differ by percent).
    with pytest.raises(FundingCoverageError):
        _run(
            [_quiet_bar()],  # no marks supplied
            state=_long_10_at_100("1000"),
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
    bar0 = Candle(T0, 100.0, 101.0, 99.0, 100.5, 500.0, MARKET, HOUR_S, VENUE)
    bar1 = Candle(T0 + HOUR_MS, 105.0, 106.0, 104.0, 105.5, 500.0, MARKET, HOUR_S, VENUE)
    state, log = _run([bar0, bar1], state=state, strategy=_OpenOnceStrategy())

    pos = state.position(VENUE, MARKET)
    assert pos is not None
    assert pos.size == 1.0
    # T+1: the decision was made on bar0, the fill happens IN bar1 — the fill
    # event is stamped inside bar1 and priced off bar1's open (Tier-C parametric,
    # so slippage around 105.0 — never bar0's close 100.5 or open 100.0, which sit
    # far outside bar1's range).
    fill_ev = _first(log, FILL)
    assert fill_ev.ts >= T0 + HOUR_MS
    assert 104.0 <= pos.entry_price <= 107.0


def test_market_order_is_not_filled_on_its_own_decision_bar():
    state = PortfolioState()
    state.fund(VENUE, "1000")
    bar0 = Candle(T0, 100.0, 101.0, 99.0, 100.5, 500.0, MARKET, HOUR_S, VENUE)
    state, log = _run([bar0], state=state, strategy=_OpenOnceStrategy())
    # Only one bar: the order is queued for a T+1 that never comes → no position.
    assert state.position(VENUE, MARKET) is None
    assert FILL not in _kinds(log)


# --- intrabar adverse-extreme-first policy + flagging (Tier C) -------------


def test_tier_c_liquidation_uses_the_adverse_extreme_and_is_flagged_ambiguous():
    # OHLCV-only bar (no mark snapshots): the liquidation is evaluated on the
    # candle's adverse extreme (low, for a long) and flagged intrabar_ambiguous.
    state, log = _run([_bar(low=96.0)], state=_long_10_at_100("30"))  # no marks → Tier C

    assert state.position(VENUE, MARKET) is None
    liq = _first(log, LIQUIDATION)
    assert liq.payload["mark_price"] == 96.0  # the adverse extreme, not close
    assert liq.payload["intrabar_ambiguous"] is True


def test_liquidation_with_recorded_marks_is_not_flagged_ambiguous():
    # A mark snapshot in the bar → Tier A/B → path known enough → not flagged.
    state, log = _run(
        [_bar(low=96.0)],
        state=_long_10_at_100("30"),
        marks={MARKET: [_mark(mark_price=96.0)]},
    )
    liq = _first(log, LIQUIDATION)
    assert liq.payload["intrabar_ambiguous"] is False


# --- §6.5 liquidation: tiered maintenance, cross-pool, isolated, backstop ---

ETH = "ETH-PERP"


def _candle_for(market: str, ts: int, low: float, close: float) -> Candle:
    return Candle(ts, 100.0, 101.0, low, close, 1000.0, market, HOUR_S, VENUE)


def _mark_for(market: str, ts: int, price: float) -> MarkSnapshot:
    return MarkSnapshot(
        market=market, ts=ts, mark_price=price, index_price=price, venue=VENUE
    )


def test_liquidation_6_5_golden_liq_price_is_about_97_5():
    # §6.5 worked example: long 10 SOL, entry 100, $50 margin (20× → 2.5% maint).
    # The dive to 97 is past the trigger, so it liquidates; the recorded trigger
    # price is the HL cross formula (1000−50)/(10×0.975) = 97.44 ≈ 97.5 (the spec's
    # rounded figure). Not a backstop (equity 20 > 2/3×24.25), so no forfeiture.
    state, log = _run(
        [_bar(low=97.0)],
        state=_long_10_at_100("50"),
        marks={MARKET: [_mark(mark_price=97.0)]},
    )

    assert state.position(VENUE, MARKET) is None
    liq = _first(log, LIQUIDATION)
    assert liq.payload["liq_price"] == pytest.approx(97.4359, abs=1e-3)
    assert abs(liq.payload["liq_price"] - 97.5) < 0.1  # matches spec's ≈97.5
    assert liq.payload["margin_mode"] == "cross"
    assert liq.payload["backstop"] is False
    assert liq.payload["insurance_fund_solvent"] is True
    assert liq.payload["adl_rank"] == "pnl_pct_x_leverage"


def test_backstop_below_two_thirds_maintenance_forfeits_margin():
    # A gap far past maintenance (equity −20 < 2/3 × 23.75) → the HLP vault takes
    # over; under the solvent-fund assumption the loss is capped at the backing
    # (−$30) and the covered shortfall ($20) is recorded on the event (§6.5).
    state, log = _run(
        [_bar(low=95.0)],
        state=_long_10_at_100("30"),
        marks={MARKET: [_mark(mark_price=95.0)]},
    )

    liq = _first(log, LIQUIDATION)
    assert liq.payload["backstop"] is True
    assert Decimal(liq.payload["realized_pnl"]) == Decimal("-30")  # capped at backing
    assert Decimal(liq.payload["insurance_shortfall"]) == Decimal("20")
    assert state.account(VENUE).cash == Decimal("0")  # vault absorbs the rest


def test_cross_pool_closes_largest_loss_first_then_stops_when_pool_clears():
    # Two cross positions share one $30 pool. When SOL craters, the pool breaches;
    # the engine closes the largest-loss leg (SOL, −$20) first, then re-evaluates —
    # the pool now clears ($10 ≥ $2.5 maint), so the ETH leg SURVIVES (§6.5).
    state = PortfolioState()
    state.fund(VENUE, "30")
    state.positions[(VENUE, MARKET)] = Position(
        market=MARKET, venue=VENUE, side=Side.LONG, size=10.0,
        entry_price=100.0, margin_mode="cross",
    )
    state.positions[(VENUE, ETH)] = Position(
        market=ETH, venue=VENUE, side=Side.LONG, size=1.0,
        entry_price=100.0, margin_mode="cross",
    )
    # Bar 1: ETH benign → establishes ETH's carried mark. Bar 2: SOL dives to 98.
    eth_bar = _candle_for(ETH, T0, low=99.0, close=100.0)
    sol_bar = _candle_for(MARKET, T0 + HOUR_MS, low=98.0, close=98.0)
    state, log = _run(
        [eth_bar, sol_bar],
        state=state,
        marks={
            ETH: [_mark_for(ETH, T0 + 12 * 60 * 1000, 100.0)],
            MARKET: [_mark_for(MARKET, T0 + HOUR_MS + 12 * 60 * 1000, 98.0)],
        },
    )

    assert state.position(VENUE, MARKET) is None  # largest loss closed first
    assert state.position(VENUE, ETH) is not None  # pool cleared → survives
    liqs = [e for e in log.read() if e.kind == LIQUIDATION]
    assert len(liqs) == 1
    assert liqs[0].payload["market"] == MARKET
    assert state.account(VENUE).cash == Decimal("10")


def test_isolated_position_closes_whole_against_its_own_margin():
    # A perfectly healthy cross pool (huge cash) must NOT shield an isolated
    # position — it stands alone against its own isolated_margin and closes whole
    # when that margin is breached (§6.5).
    state = PortfolioState()
    state.fund(VENUE, "100000")  # cross pool is fine; irrelevant to isolated
    state.positions[(VENUE, MARKET)] = Position(
        market=MARKET, venue=VENUE, side=Side.LONG, size=10.0,
        entry_price=100.0, margin_mode="isolated", isolated_margin=30.0,
    )
    state, log = _run(
        [_bar(low=96.0)],
        state=state,
        marks={MARKET: [_mark(mark_price=96.0)]},
    )

    assert state.position(VENUE, MARKET) is None
    liq = _first(log, LIQUIDATION)
    assert liq.payload["margin_mode"] == "isolated"


# --- lifecycle + shared fill path ------------------------------------------


def test_market_order_walks_a_provided_book_as_tier_a():
    # With a recorded book + trades, the default CLOB model walks real depth and
    # records the fill as Tier A — end-to-end through the T+1 path.
    state = PortfolioState()
    state.fund(VENUE, "100000")
    bar0 = Candle(T0, 100.0, 101.0, 99.0, 100.5, 500.0, MARKET, HOUR_S, VENUE)
    bar1 = Candle(T0 + HOUR_MS, 100.0, 101.0, 99.0, 100.5, 500.0, MARKET, HOUR_S, VENUE)
    book = OrderbookSnapshot(
        MARKET, T0 + HOUR_MS, ((99.9, 10.0),), ((100.0, 5.0), (100.1, 5.0)), VENUE
    )
    state, log = _run(
        [bar0, bar1],
        state=state,
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


def test_market_order_without_a_book_is_tier_c_and_flags_flow_to_the_event():
    # No book fixture → Tier-C parametric fill; the uncalibrated flag reaches the
    # FILL event payload so the tearsheet can show it.
    state = PortfolioState()
    state.fund(VENUE, "100000")
    bar0 = Candle(T0, 100.0, 101.0, 99.0, 100.5, 500.0, MARKET, HOUR_S, VENUE)
    bar1 = Candle(T0 + HOUR_MS, 100.0, 101.0, 99.0, 100.5, 500.0, MARKET, HOUR_S, VENUE)
    state, log = _run([bar0, bar1], state=state, strategy=_OpenOnceStrategy())
    fill_ev = next(e for e in log.read() if e.kind == FILL)
    assert fill_ev.payload["fidelity_tier"] == "C"
    assert "uncalibrated" in fill_ev.payload["flags"]


def test_run_brackets_events_with_run_started_and_finished():
    state, log = _run([_bar()])
    kinds = _kinds(log)
    assert kinds[0] == "run_started"
    assert kinds[-1] == "run_finished"


def test_close_signal_routes_a_reduce_only_fill_next_bar():
    # A carried position (warm start) plus a close signal reduces it to flat via
    # the T+1 path — the paper-resume exit that N10.1's seed materialization fixed.
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
    state, log = _run(
        [bar0, bar1], state=_long_10_at_100("1000"), strategy=_CloseOnce()
    )

    assert state.position(VENUE, MARKET) is None
    assert FILL in _kinds(log)
