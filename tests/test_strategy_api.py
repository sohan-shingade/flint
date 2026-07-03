"""Slice 5.1 — the public Strategy API (§8.1, D21, D28).

Proven here, all on hand-built inputs (D26):

1. **The public base** — ``params`` are per-instance (optimizer-safe), overrides
   are validated, ``param_spec`` exposes the knobs, and ``on_candle`` returns are
   normalized (bare Signal wrapped, None/[] = nothing, junk raised).
2. **The engine bridge** (:class:`EngineStrategy`) — the three-arg user
   ``on_candle`` is adapted to the engine's two-arg seam, ``history`` carries the
   closed bars with the current bar last, and the D28 gate turns a
   ``venue="binance"`` signal into a structured ``venue_not_executable`` rejection
   while the Hyperliquid leg still executes end-to-end.
3. **The surface end-to-end** — sizing helpers size off current venue equity, and
   the ``ctx.submit_order`` escape hatch trades on the same Order path and earns
   the tearsheet's "linter coverage partial" note.
"""

from __future__ import annotations

import pytest

from flint import Signal as TopLevelSignal
from flint import Strategy as TopLevelStrategy
from flint.adapters import InMemoryUserData
from flint.core.models import Candle, Side, Signal
from flint.engine import BacktestEngine, EngineConfig, PortfolioState
from flint.engine.fills import NaiveFillModel
from flint.engine.portfolio import EventLog
from flint.ports import TenantContext
from flint.strategy import (
    EXECUTABLE_VENUES,
    EngineStrategy,
    Strategy,
    StrategyRejection,
    normalize_signals,
)
from flint.strategy.base import IMPERATIVE_ORDERS_NOTE, UnknownParamError

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
HOUR_S = 3600
HOUR_MS = 3600 * 1000
T0 = 1_700_000_000_000


def _bar(ts, open_, *, close=None, venue=VENUE, market=MARKET) -> Candle:
    close = open_ if close is None else close
    return Candle(
        ts=ts,
        open=open_,
        high=max(open_, close) + 1.0,
        low=min(open_, close) - 1.0,
        close=close,
        volume=1000.0,
        market=market,
        resolution_s=HOUR_S,
        venue=venue,
    )


def _engine(state=None):
    store = InMemoryUserData()
    log = EventLog(store, TenantContext.local(), run_id="run-5.1")
    engine = BacktestEngine(
        log, config=EngineConfig(), state=state or PortfolioState(),
        fill_model=NaiveFillModel(),
    )
    return engine, log


class _FakeCtx:
    """A minimal stand-in for the engine ctx — just the accessors the adapter uses."""

    def __init__(self, now, closed_by_market):
        self.now = now
        self._closed = closed_by_market
        self.submitted: list = []

    def candles(self, market, lookback, venue=None):
        bars = self._closed.get(market, [])
        return bars[-lookback:] if lookback > 0 else []

    def submit_order(self, *args, **kwargs):
        self.submitted.append((args, kwargs))
        return "ok"


# --- the public base: params + normalization ------------------------------


def test_top_level_reexports_are_the_real_classes():
    assert TopLevelStrategy is Strategy
    assert TopLevelSignal is Signal


def test_params_are_per_instance_and_overridable():
    class S(Strategy):
        params = dict(entry_bps=5.0, size_usd=1000.0)

    a = S()
    b = S(entry_bps=9.0)
    assert a.params == {"entry_bps": 5.0, "size_usd": 1000.0}
    assert b.params["entry_bps"] == 9.0
    # mutating one instance never touches the class declaration or a sibling
    a.params["entry_bps"] = 1.0
    assert S.params["entry_bps"] == 5.0
    assert b.params["entry_bps"] == 9.0


def test_unknown_param_override_is_rejected():
    class S(Strategy):
        params = dict(entry_bps=5.0)

    with pytest.raises(UnknownParamError):
        S(entyr_bps=5.0)  # typo — caught, not silently absorbed


def test_param_spec_exposes_declared_knobs():
    class S(Strategy):
        params = dict(a=1, b=2.0)

    assert S.param_spec() == {"a": 1, "b": 2.0}
    assert S().name == "S"


def test_on_candle_default_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        Strategy().on_candle(_bar(T0, 100.0), [], _FakeCtx(T0, {}))


def test_normalize_signals_wraps_bare_and_empties_none():
    sig = Signal.long(MARKET, VENUE, size_usd=100.0)
    assert normalize_signals(sig) == [sig]
    assert normalize_signals(None) == []
    assert normalize_signals([]) == []
    assert normalize_signals([sig, sig]) == [sig, sig]


def test_normalize_signals_rejects_non_signal_returns():
    with pytest.raises(TypeError):
        normalize_signals("long")  # a bare string is not a signal batch
    with pytest.raises(TypeError):
        normalize_signals([Signal.long(MARKET, VENUE, size=1.0), object()])


# --- the engine bridge: history + venue gate (D28) ------------------------


def test_adapter_history_is_closed_bars_with_current_last():
    prev = _bar(T0, 100.0, close=101.0)
    curr = _bar(T0 + HOUR_MS, 101.0, close=102.0)
    seen: dict = {}

    class S(Strategy):
        def on_candle(self, candle, history, ctx):
            seen["history"] = history
            return []

    adapter = EngineStrategy(S())
    ctx = _FakeCtx(now=curr.ts, closed_by_market={MARKET: [prev]})
    adapter.on_candle(curr, ctx)
    # prior closed bars, then the just-closed current bar last (the §8.2 convention)
    assert seen["history"] == [prev, curr]


def test_non_executable_venue_becomes_structured_rejection_not_an_order():
    # A two-leg bar: long HL + long binance. Only the HL leg is executable in v1;
    # the binance leg is refused as a structured venue_not_executable record (D28).
    hl = Signal.long(MARKET, venue="hyperliquid", size_usd=500.0)
    bn = Signal.long(MARKET, venue="binance", size_usd=500.0)

    class TwoLeg(Strategy):
        def on_candle(self, candle, history, ctx):
            return [hl, bn]

    adapter = EngineStrategy(TwoLeg())
    ctx = _FakeCtx(now=T0, closed_by_market={})
    routed = adapter.on_candle(_bar(T0, 100.0), ctx)

    assert routed == [hl]  # only the executable leg reaches the engine
    assert len(adapter.rejections) == 1
    rej = adapter.rejections[0]
    assert rej.reason == "venue_not_executable"
    assert rej.signal is bn
    assert "binance" in rej.detail
    assert rej.ts == T0
    # drain empties the buffer (the runner takes them once)
    assert adapter.drain_rejections() == [rej]
    assert adapter.rejections == []


def test_executable_venue_set_is_hyperliquid_only():
    assert EXECUTABLE_VENUES == frozenset({"hyperliquid"})
    assert StrategyRejection.venue_not_executable(
        Signal.long(MARKET, "binance", size=1.0), T0
    ).reason == "venue_not_executable"


def test_empty_venue_defaults_to_the_bars_venue_and_routes():
    # An empty venue mirrors the engine's ``sig.venue or default_venue`` — it is the
    # bar's (executable) venue, so it routes rather than being rejected.
    sig = Signal(market=MARKET, venue="", action="long", size=1.0)

    class S(Strategy):
        def on_candle(self, candle, history, ctx):
            return [sig]

    adapter = EngineStrategy(S())
    routed = adapter.on_candle(_bar(T0, 100.0, venue=VENUE), _FakeCtx(T0, {}))
    assert routed == [sig]
    assert adapter.rejections == []


# --- the surface end-to-end through the real engine -----------------------


def test_public_strategy_trades_end_to_end_on_hyperliquid():
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, _ = _engine(state=state)

    class Buy(Strategy):
        params = dict(size_usd=5000.0)

        def on_candle(self, candle, history, ctx):
            if candle.ts == T0 and ctx.position(MARKET) is None:
                return Signal.long(MARKET, VENUE, size_usd=self.params["size_usd"])
            return []

    engine.run(
        [_bar(T0, 100.0, close=100.0), _bar(T0 + HOUR_MS, 105.0)],
        strategy=EngineStrategy(Buy()),
    )
    pos = state.position(VENUE, MARKET)
    assert pos is not None
    assert pos.entry_price == 105.0  # sized + filled at the execution bar's open
    assert pos.size == 47.61  # floor(5000/105, 2dp)


def test_sizing_helper_sizes_off_current_venue_equity():
    # size_for_target_leverage(2.0) off 10_000 equity → 20_000 notional; at the next
    # bar's open (100) that is 200 base — 2× equity, compounding with the account.
    state = PortfolioState()
    state.fund(VENUE, "10000")
    engine, _ = _engine(state=state)

    class Lever(Strategy):
        def on_candle(self, candle, history, ctx):
            if candle.ts == T0 and ctx.position(MARKET) is None:
                notional = ctx.account(VENUE).size_for_target_leverage(2.0)
                return Signal.long(MARKET, VENUE, size_usd=notional)
            return []

    engine.run(
        [_bar(T0, 100.0, close=100.0), _bar(T0 + HOUR_MS, 100.0)],
        strategy=EngineStrategy(Lever()),
    )
    pos = state.position(VENUE, MARKET)
    assert pos is not None
    assert pos.size == 200.0  # 20_000 / 100 = 2× the 10_000 equity


def test_submit_order_escape_hatch_trades_and_earns_the_tearsheet_note():
    state = PortfolioState()
    state.fund(VENUE, "100000")
    engine, _ = _engine(state=state)

    class Imperative(Strategy):
        def on_candle(self, candle, history, ctx):
            if candle.ts == T0:
                ctx.submit_order(MARKET, VENUE, Side.LONG, 1.0)
            return []

    adapter = EngineStrategy(Imperative())
    engine.run(
        [_bar(T0, 100.0, close=100.0), _bar(T0 + HOUR_MS, 105.0)], strategy=adapter
    )
    pos = state.position(VENUE, MARKET)
    assert pos is not None and pos.size == 1.0  # same Order path as a Signal
    assert adapter.used_imperative_orders is True
    assert adapter.tearsheet_notes() == [IMPERATIVE_ORDERS_NOTE]


def test_signal_only_strategy_earns_no_imperative_note():
    class Buy(Strategy):
        def on_candle(self, candle, history, ctx):
            return []

    adapter = EngineStrategy(Buy())
    _engine()  # unused engine; assert the note is absent without any run
    assert adapter.tearsheet_notes() == []
