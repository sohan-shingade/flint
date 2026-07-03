"""Slice 3.6 — the internal strategy interface, §8.1 conversion, §8.2 visibility.

Proven here, all on hand-built inputs (D26):

1. **Signal → Order conversion (§8.1)** — size_usd sizes at the *execution bar's
   open* (never the decision close), close is reduce-only, and invalid batches
   raise rather than merge.
2. **The ctx visibility contract (§8.2)** — every accessor is strictly-before-bar-
   start, closed-only, None-over-stale; one truncated-frame test each.
3. **Full-cost tearsheet + §19.2 invariants** — the MA-cross acceptance run
   decomposes cost honestly and its order/funding ledger conserves.
"""

from __future__ import annotations


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
from flint.engine import (
    AccountView,
    BacktestEngine,
    EngineConfig,
    OpenInterestSnapshot,
    OrderStatus,
    PortfolioState,
    SignalValidationError,
    build_tearsheet,
    check_invariants,
)
from flint.engine.fills import NaiveFillModel
from flint.engine.funding.settlement import FundingCoverageError
from flint.engine.portfolio import ORDER_PLACED, EventLog
from flint.engine.tearsheet import InvariantError
from flint.ports import TenantContext
from flint.venues import HYPERLIQUID

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
HOUR_S = 3600
HOUR_MS = 3600 * 1000
T0 = 1_700_000_000_000


def _engine(state=None, fill_model=None, config=None):
    store = InMemoryUserData()
    log = EventLog(store, TenantContext.local(), run_id="run-3.6")
    engine = BacktestEngine(
        log,
        config=config or EngineConfig(),
        state=state or PortfolioState(),
        fill_model=fill_model,
    )
    return engine, log


def _bar(ts, open_, *, high=None, low=None, close=None, volume=1000.0) -> Candle:
    close = open_ if close is None else close
    return Candle(
        ts=ts,
        open=open_,
        high=high if high is not None else max(open_, close) + 1.0,
        low=low if low is not None else min(open_, close) - 1.0,
        close=close,
        volume=volume,
        market=MARKET,
        resolution_s=HOUR_S,
        venue=VENUE,
    )


class _Script:
    def __init__(self, by_ts):
        self._by_ts = by_ts

    def on_candle(self, candle, ctx):
        return self._by_ts.get(candle.ts, [])


# --- §8.1 Signal → Order conversion ---------------------------------------


def test_size_usd_sizes_at_the_execution_bars_open_not_the_decision_close():
    # long(size_usd=5000) decided on bar0 (close 100) must size at bar1's OPEN
    # (105) — 5000/105 = 47.61 base after lot floor — NOT at 100 (which would give
    # 50.0). Sizing on the decision close is a look-ahead no linter can see (§8.1).
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, log = _engine(state=state, fill_model=NaiveFillModel())
    script = _Script({T0: [Signal.long(MARKET, VENUE, size_usd=5000.0)]})
    engine.run(
        [_bar(T0, 100.0, close=100.0), _bar(T0 + HOUR_MS, 105.0)], strategy=script
    )

    pos = state.position(VENUE, MARKET)
    assert pos is not None
    assert pos.entry_price == 105.0
    assert pos.size == 47.61  # floor(5000/105, 2 dp) — sized at the OPEN, not 100
    placed = next(e for e in log.read() if e.kind == ORDER_PLACED)
    assert placed.payload["size_usd"] == 5000.0
    assert placed.payload["size_residual"] == pytest.approx(5000 / 105 - 47.61, abs=1e-9)


def test_size_usd_that_rounds_below_one_lot_is_placed_then_rejected():
    # $1 at price 105 → 0.0095 base → floors to 0.00 (< one 2-dp lot) → the order
    # is placed then rejected (no size), never silently grown to a lot (§8.1 rule 2).
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, log = _engine(state=state, fill_model=NaiveFillModel())
    script = _Script({T0: [Signal.long(MARKET, VENUE, size_usd=1.0)]})
    engine.run([_bar(T0, 100.0), _bar(T0 + HOUR_MS, 105.0)], strategy=script)

    (rec,) = list(engine._orders.values())
    assert rec.status is OrderStatus.REJECTED
    assert rec.reason == "size_rounds_to_zero"
    assert state.position(VENUE, MARKET) is None


def test_close_is_reduce_only_full_size_and_cannot_flip():
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, log = _engine(state=state, fill_model=NaiveFillModel())
    script = _Script(
        {
            T0: [Signal.long(MARKET, VENUE, size=2.0)],
            T0 + 2 * HOUR_MS: [Signal.close(MARKET, VENUE)],
        }
    )
    engine.run(
        [
            _bar(T0, 100.0),
            _bar(T0 + HOUR_MS, 100.0),
            _bar(T0 + 2 * HOUR_MS, 100.0),
            _bar(T0 + 3 * HOUR_MS, 100.0),
        ],
        strategy=script,
    )
    assert state.position(VENUE, MARKET) is None  # closed flat, not flipped short


def test_open_with_no_sizing_raises():
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, _ = _engine(state=state, fill_model=NaiveFillModel())
    script = _Script({T0: [Signal(market=MARKET, venue=VENUE, action="long")]})
    with pytest.raises(SignalValidationError):
        engine.run([_bar(T0, 100.0), _bar(T0 + HOUR_MS, 100.0)], strategy=script)


def test_open_with_both_sizings_raises():
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, _ = _engine(state=state, fill_model=NaiveFillModel())
    script = _Script(
        {T0: [Signal.long(MARKET, VENUE, size=1.0, size_usd=100.0)]}
    )
    with pytest.raises(SignalValidationError):
        engine.run([_bar(T0, 100.0), _bar(T0 + HOUR_MS, 100.0)], strategy=script)


def test_duplicate_market_venue_action_in_one_bar_raises():
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, _ = _engine(state=state, fill_model=NaiveFillModel())
    script = _Script(
        {
            T0: [
                Signal.long(MARKET, VENUE, size=1.0),
                Signal.long(MARKET, VENUE, size=2.0),  # same (market,venue,action)
            ]
        }
    )
    with pytest.raises(SignalValidationError):
        engine.run([_bar(T0, 100.0), _bar(T0 + HOUR_MS, 100.0)], strategy=script)


def test_submit_order_escape_hatch_routes_through_the_shared_path():
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, log = _engine(state=state, fill_model=NaiveFillModel())

    class _Imperative:
        def on_candle(self, candle, ctx):
            if candle.ts == T0:
                ctx.submit_order(MARKET, VENUE, Side.LONG, 1.5)
            return []

    engine.run([_bar(T0, 100.0), _bar(T0 + HOUR_MS, 105.0)], strategy=_Imperative())
    pos = state.position(VENUE, MARKET)
    assert pos is not None
    assert pos.size == 1.5
    assert pos.entry_price == 105.0  # filled T+1 like any market order


# --- §8.2 visibility contract: one truncated-frame test per accessor -------


class _Probe:
    """Capture one accessor's value on the bar at ``at_ts``."""

    def __init__(self, at_ts, fn):
        self.at_ts = at_ts
        self.fn = fn
        self.value = "unset"

    def on_candle(self, candle, ctx):
        if candle.ts == self.at_ts:
            self.value = self.fn(ctx)
        return []


def _capture(fn, candles, **run_kw):
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, _ = _engine(state=state)
    probe = _Probe(candles[-1].ts, fn)
    engine.run(candles, strategy=probe, **run_kw)
    return probe.value


def _mark(ts, price) -> MarkSnapshot:
    return MarkSnapshot(
        market=MARKET, ts=ts, mark_price=price, index_price=price * 0.99, venue=VENUE
    )


def test_funding_rate_accessor_is_none_when_only_future_rates_exist():
    bars = [_bar(T0, 100.0)]
    predicted = FundingRate(
        market=MARKET, ts=T0 - 60_000, rate_hourly=0.01, interval_s=HOUR_S,
        price_basis="oracle", rate_type="predicted", venue=VENUE,
    )
    future = FundingRate(
        market=MARKET, ts=T0 + 60_000, rate_hourly=0.02, interval_s=HOUR_S,
        price_basis="oracle", rate_type="predicted", venue=VENUE,
    )
    seen = _capture(lambda c: c.funding_rate(MARKET), bars, funding={MARKET: [predicted]})
    assert seen is not None and seen.rate_hourly == 0.01
    truncated = _capture(lambda c: c.funding_rate(MARKET), bars, funding={MARKET: [future]})
    assert truncated is None  # ts >= bar start → invisible


def test_basis_bps_accessor_reads_last_mark_before_bar_start_else_none():
    bars = [_bar(T0, 100.0)]
    got = _capture(lambda c: c.basis_bps(MARKET), bars, marks={MARKET: [_mark(T0 - 1, 100.0)]})
    assert got == pytest.approx((100.0 - 99.0) / 99.0 * 10_000)
    truncated = _capture(lambda c: c.basis_bps(MARKET), bars, marks={MARKET: [_mark(T0, 100.0)]})
    assert truncated is None  # the only mark is at (not before) bar start


def test_open_interest_accessor_reads_last_value_before_bar_start_else_none():
    bars = [_bar(T0, 100.0)]
    oi = [OpenInterestSnapshot(MARKET, T0 - 1, 12345.0, VENUE)]
    assert _capture(lambda c: c.open_interest(MARKET), bars, oi={MARKET: oi}) == 12345.0
    future = [OpenInterestSnapshot(MARKET, T0 + 1, 999.0, VENUE)]
    assert _capture(lambda c: c.open_interest(MARKET), bars, oi={MARKET: future}) is None


def test_orderbook_accessor_returns_fresh_but_none_when_stale():
    bars = [_bar(T0, 100.0)]
    threshold_ms = int(HYPERLIQUID.book_staleness_s * 1000)
    fresh = OrderbookSnapshot(MARKET, T0 - 1000, ((99.0, 1.0),), ((101.0, 1.0),), VENUE)
    got = _capture(lambda c: c.orderbook(MARKET), bars, books={MARKET: [fresh]})
    assert got is fresh
    stale = OrderbookSnapshot(
        MARKET, T0 - threshold_ms - 1000, ((99.0, 1.0),), ((101.0, 1.0),), VENUE
    )
    assert _capture(lambda c: c.orderbook(MARKET), bars, books={MARKET: [stale]}) is None


def test_candles_accessor_returns_closed_bars_only_excluding_the_current():
    bars = [_bar(T0, 100.0), _bar(T0 + HOUR_MS, 101.0), _bar(T0 + 2 * HOUR_MS, 102.0)]
    got = _capture(lambda c: c.candles(MARKET, 5), bars)
    assert [c.ts for c in got] == [T0, T0 + HOUR_MS]  # the current (3rd) bar excluded


def test_account_view_reflects_equity_and_is_a_plain_value_object():
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, _ = _engine(state=state)
    captured = {}

    class _P:
        def on_candle(self, candle, ctx):
            captured["acct"] = ctx.account(VENUE)
            return []

    engine.run([_bar(T0, 100.0)], strategy=_P())
    acct = captured["acct"]
    assert isinstance(acct, AccountView)
    assert acct.equity == 100000.0
    assert acct.cross_margin_available == 100000.0
    assert not hasattr(acct, "_log") and not hasattr(acct, "state")  # §8.3: inert


# --- §8.1 sizing helpers (unit) -------------------------------------------


def test_sizing_helpers_compute_size_usd_off_equity():
    acct = AccountView(venue=VENUE, equity=10_000.0, cross_margin_available=10_000.0)
    assert acct.size_for_target_leverage(2.0) == 20_000.0
    # risk 1% of 10k with a 50bps stop → 100 / 0.005 = 20_000 notional
    assert acct.size_for_risk_pct(0.01, 50.0) == pytest.approx(20_000.0)
    # half-Kelly: f* = 0.6 − 0.4/2 = 0.4; stake 0.5×0.4×10k = 2_000
    assert acct.size_for_kelly(edge=2.0, win_rate=0.6, fraction=0.5) == pytest.approx(2_000.0)
    assert acct.size_for_kelly(edge=-1.0, win_rate=0.6) == 0.0  # no edge → nothing


# --- full-cost tearsheet + §19.2 acceptance -------------------------------

# A hand-built rise-then-fall path; fast(2)/slow(3) MA crosses up at bar2 (buy
# fills bar3 @105) and down at bar5 (close fills bar6 @98).
_CLOSES = [100.0, 101.0, 103.0, 105.0, 104.0, 100.0, 98.0, 97.0]


class _MaCross:
    def __init__(self, fast, slow, size_usd):
        self.fast, self.slow, self.size_usd = fast, slow, size_usd

    def on_candle(self, candle, ctx):
        closes = [c.close for c in ctx.candles(MARKET, self.slow - 1, VENUE)]
        closes.append(candle.close)
        if len(closes) < self.slow:
            return []
        fast = sum(closes[-self.fast:]) / self.fast
        slow = sum(closes[-self.slow:]) / self.slow
        pos = ctx.position(MARKET, VENUE)
        if fast > slow and pos is None:
            return [Signal.long(MARKET, VENUE, size_usd=self.size_usd)]
        if fast < slow and pos is not None:
            return [Signal.close(MARKET, VENUE)]
        return []


def _ma_cross_run():
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, log = _engine(state=state, fill_model=NaiveFillModel())
    candles = [_bar(T0 + i * HOUR_MS, c, close=c) for i, c in enumerate(_CLOSES)]
    # Funding settles on bar4 and bar5 — the position is open across both.
    settle4 = T0 + 4 * HOUR_MS + 12 * 60 * 1000
    settle5 = T0 + 5 * HOUR_MS + 12 * 60 * 1000
    marks = {MARKET: [_mark(settle4, 104.0), _mark(settle5, 100.0)]}
    funding = {
        MARKET: [
            FundingRate(MARKET, settle4, 0.0001, HOUR_S, "oracle", "final", VENUE),
            FundingRate(MARKET, settle5, 0.0001, HOUR_S, "oracle", "final", VENUE),
        ]
    }
    engine.run(candles, marks=marks, funding=funding, strategy=_MaCross(2, 3, 5000.0))
    return engine, log


def test_ma_cross_acceptance_trades_records_fidelity_and_conserves():
    engine, log = _ma_cross_run()
    ts = build_tearsheet(log.read())

    # It actually traded a full round trip (a buy + a close both filled).
    assert ts.fills >= 2
    assert engine.state.position(VENUE, MARKET) is None  # flat after the close
    # Per-fill fidelity is recorded — Tier C here (no book supplied).
    assert ts.fills_by_tier.get("C", 0) == ts.fills
    # Funding settled exactly twice (bar4 + bar5, position open across both).
    assert ts.funding_settlements == 2
    # §19.2 invariants hold: orders conserve and funding matches the expected count.
    check_invariants(ts, engine._orders, expected_funding_settlements=2)
    assert ts.orders_placed == ts.orders_filled + ts.orders_rejected + ts.orders_cancelled
    # The cost block decomposes net PnL into its honest lines.
    assert ts.net_pnl == ts.trading_pnl + ts.funding + ts.fees
    assert ts.fees < 0 and ts.funding < 0  # paid both
    assert ts.evaluated_start_ts is not None and ts.evaluated_end_ts is not None


def test_invariant_check_raises_when_a_settlement_count_is_wrong():
    engine, log = _ma_cross_run()
    ts = build_tearsheet(log.read())
    with pytest.raises(InvariantError):
        check_invariants(ts, engine._orders, expected_funding_settlements=99)


def test_missing_funding_coverage_hard_gates_the_run():
    # §16 hard gate: an oracle-priced final settlement with no mark to price it
    # rejects the run loudly rather than pricing on the close.
    state = PortfolioState()
    state.fund(VENUE, "100000")
    state.positions[(VENUE, MARKET)] = Position(
        market=MARKET, venue=VENUE, side=Side.LONG, size=1.0,
        entry_price=100.0, margin_mode="cross",
    )
    engine, _ = _engine(state=state)
    funding = {
        MARKET: [FundingRate(MARKET, T0 + 12 * 60 * 1000, 0.0001, HOUR_S, "oracle", "final", VENUE)]
    }
    with pytest.raises(FundingCoverageError):
        engine.run([_bar(T0, 100.0, high=101.0, low=99.0)], funding=funding)
