"""Slice 5.4 — built-in template registry + each template validates and runs (§8.4).

All inputs are hand-authored candle paths / snapshots (D26 — no generated market-like
series). Proven here:

1. **Registry** is the single source of truth: all nine templates registered, sorted,
   fetchable, dup-guarded; only the ML template is flagged ``is_ml``.
2. **Each template runs on a fixture** and emits only well-formed Signals — never
   ``ctx.submit_order``. A validity helper mirrors the engine's §8.1 rules (exactly
   one sizing per open, no duplicate action key per bar).
3. **Real-engine validation**: representative templates drive the Nautilus bar lane
   through ``EngineStrategy`` (via the engine seam) and complete without
   ``SignalValidationError``, opening a real position.
4. **ML template** subclasses ``MLStrategy``, its ``features()`` passes the feature-
   causality screen, and (with lightgbm) trains + trades through ``MLEngineStrategy``.
"""

from __future__ import annotations

import inspect

import pytest

from flint.core.models import Candle, FundingRate, Position, Side, Signal
from flint.engine import EngineConfig
from flint.engine.api import EngineFeed, EngineRunSpec
from flint.engine.portfolio import EventLog
from flint.engine.select import engine_for
from flint.ports import TenantContext
from flint.strategy import EngineStrategy, get_template, list_templates, template_names
from flint.strategy.ml import MLEngineStrategy, MLStrategy
from flint.strategy.model_store import ModelStore
from flint.venues import HYPERLIQUID
from flint.strategy.sandbox import screen_source
from flint.strategy.templates import (
    BasisTradeStrategy,
    BollingerStrategy,
    BreakoutStrategy,
    FundingDislocationStrategy,
    FundingHarvestStrategy,
    LightGbmTrendStrategy,
    MaCrossStrategy,
    OiMomentumStrategy,
    RsiReversionStrategy,
    TemplateNotFound,
)
from flint.strategy.templates.registry import TemplateSpec, register

HL = "hyperliquid"
MKT = "SOL-PERP"
HOUR_MS = 3_600_000
T0 = 1_700_000_000_000


def _c(ts: int, close: float, *, high: float | None = None, low: float | None = None) -> Candle:
    return Candle(
        ts=ts, open=close, high=high if high is not None else close,
        low=low if low is not None else close, close=close, volume=1000.0,
        market=MKT, resolution_s=3600, venue=HL,
    )


def _path(closes: list[float]) -> list[Candle]:
    return [_c(T0 + i * HOUR_MS, c) for i, c in enumerate(closes)]


def _pos(side: Side, size: float = 1.0) -> Position:
    return Position(
        market=MKT, venue=HL, side=side, size=size, entry_price=100.0,
        margin_mode="cross", isolated_margin=0.0,
    )


class _FakeCtx:
    """Configurable engine-ctx double for driving one template bar."""

    def __init__(self, *, position=None, funding=None, basis=None, oi=None,
                 candles=None, now=T0):
        self._position = position
        self._funding = funding
        self._basis = basis
        self._oi = oi
        self._candles = candles or []
        self.now = now
        self.rng = object()

    def position(self, market, venue=None):
        return self._position

    def funding_rate(self, market, venue=None):
        if self._funding is None:
            return None
        return FundingRate(
            market=market, ts=self.now - 1, rate_hourly=self._funding, interval_s=3600,
            price_basis="oracle", rate_type="predicted", venue=venue or HL,
        )

    def basis_bps(self, market, venue=None):
        return self._basis

    def open_interest(self, market, venue=None):
        return self._oi

    def candles(self, market, lookback, venue=None):
        return list(self._candles) if lookback > 0 else []

    def submit_order(self, *a, **k):  # a template must NEVER call this
        raise AssertionError("templates must emit Signals, never call ctx.submit_order")


def _assert_valid(signals: list[Signal]) -> None:
    """Mirror the engine's §8.1 per-bar rules: no dup key, exactly one sizing per open."""
    seen = set()
    for sig in signals:
        assert isinstance(sig, Signal)
        key = (sig.venue, sig.market, sig.action)
        assert key not in seen, f"duplicate signal key {key}"
        seen.add(key)
        if sig.action in ("long", "short"):
            assert (sig.size > 0) != (sig.size_usd > 0), "open needs exactly one sizing"


# --- 1. registry --------------------------------------------------------------


def test_registry_has_all_ten_templates_sorted():
    names = template_names()
    assert names == sorted(names)
    assert set(names) == {
        "funding_harvest", "funding_dislocation", "funding_svd", "basis_trade",
        "oi_momentum", "ma_cross", "rsi_reversion", "bollinger", "breakout",
        "lgbm_trend",
    }


def test_registry_get_and_missing():
    spec = get_template("ma_cross")
    assert spec.strategy_cls is MaCrossStrategy
    assert "fast" in spec.param_spec()
    with pytest.raises(TemplateNotFound):
        get_template("does_not_exist")


def test_registry_only_ml_template_flagged_is_ml():
    ml = [s.name for s in list_templates() if s.is_ml]
    assert ml == ["lgbm_trend"]
    assert get_template("lgbm_trend").category == "ml"


def test_registry_rejects_duplicate_name():
    with pytest.raises(ValueError):
        register(TemplateSpec("ma_cross", MaCrossStrategy, "dup", "technical"))


# --- 2. each template runs on a fixture and emits valid Signals ---------------


def _drive(strategy, ctx, candle):
    out = strategy.on_candle(candle, ctx._candles + [candle], ctx)
    signals = out if isinstance(out, list) else ([] if out is None else [out])
    _assert_valid(signals)
    return signals


def test_ma_cross_goes_long_on_up_then_short_on_down():
    strat = MaCrossStrategy(fast=2, slow=4, ma_type="sma")
    up = _path([10, 10, 10, 11, 12, 13])
    long_sig = _drive(strat, _FakeCtx(candles=up[:-1]), up[-1])
    assert any(s.action == "long" for s in long_sig)
    # Now hold a long, feed a downtrend -> flip to short (close + short).
    down = _path([13, 13, 13, 12, 11, 10])
    flip = _drive(strat, _FakeCtx(position=_pos(Side.LONG), candles=down[:-1]), down[-1])
    assert [s.action for s in flip] == ["close", "short"]


def test_ma_cross_no_churn_when_already_in_desired_state():
    strat = MaCrossStrategy(fast=2, slow=4, ma_type="sma")
    up = _path([10, 10, 10, 11, 12, 13])
    # Already long, still bullish -> no signals (no churn).
    assert _drive(strat, _FakeCtx(position=_pos(Side.LONG), candles=up[:-1]), up[-1]) == []


def test_rsi_reversion_long_on_oversold():
    strat = RsiReversionStrategy(period=3, oversold=30.0, overbought=70.0)
    falling = _path([100, 98, 96, 94, 92])  # only losses -> RSI 0 -> oversold
    sig = _drive(strat, _FakeCtx(candles=falling[:-1]), falling[-1])
    assert any(s.action == "long" for s in sig)


def test_bollinger_short_above_upper_band():
    strat = BollingerStrategy(period=4, k=1.0)
    spike = _path([100, 100, 100, 100, 130])  # last close well above the band
    sig = _drive(strat, _FakeCtx(candles=spike[:-1]), spike[-1])
    assert any(s.action == "short" for s in sig)


def test_breakout_long_above_prior_channel_high():
    strat = BreakoutStrategy(lookback=3)
    bars = _path([10, 11, 10, 11, 20])  # last close 20 > prior 3-bar high (11)
    sig = _drive(strat, _FakeCtx(candles=bars[:-1]), bars[-1])
    assert any(s.action == "long" for s in sig)


def test_funding_harvest_shorts_positive_rate_longs_negative():
    strat = FundingHarvestStrategy(rate_threshold=1e-5)
    hist = _path([100, 100])
    short = _drive(strat, _FakeCtx(funding=0.001, candles=hist[:-1]), hist[-1])
    assert [s.action for s in short] == ["short"]
    long = _drive(strat, _FakeCtx(funding=-0.001, candles=hist[:-1]), hist[-1])
    assert [s.action for s in long] == ["long"]
    flat = _drive(strat, _FakeCtx(funding=0.0, candles=hist[:-1]), hist[-1])
    assert flat == []


def test_funding_dislocation_trades_hl_leg_vs_benchmark():
    strat = FundingDislocationStrategy(benchmark_rate=0.0005, threshold=1e-4)
    hist = _path([100, 100])
    rich = _drive(strat, _FakeCtx(funding=0.001, candles=hist[:-1]), hist[-1])
    assert [s.action for s in rich] == ["short"]  # HL richer than benchmark
    cheap = _drive(strat, _FakeCtx(funding=0.0, candles=hist[:-1]), hist[-1])
    assert [s.action for s in cheap] == ["long"]


def test_basis_trade_fades_the_premium():
    strat = BasisTradeStrategy(basis_threshold_bps=20.0)
    hist = _path([100, 100])
    assert [s.action for s in _drive(strat, _FakeCtx(basis=50.0, candles=hist[:-1]), hist[-1])] == ["short"]
    assert [s.action for s in _drive(strat, _FakeCtx(basis=-50.0, candles=hist[:-1]), hist[-1])] == ["long"]
    assert _drive(strat, _FakeCtx(basis=0.0, candles=hist[:-1]), hist[-1]) == []


def test_oi_momentum_needs_rising_oi_and_holds_prev_across_bars():
    strat = OiMomentumStrategy(lookback=2)
    bars = _path([10, 11, 12, 13])
    # First bar: no prior OI recorded yet -> no-op, but prev now stored.
    assert _drive(strat, _FakeCtx(oi=100.0, candles=bars[:-1]), bars[-1]) == []
    # Second bar: OI rose (100 -> 110) and price rose -> long.
    sig = _drive(strat, _FakeCtx(oi=110.0, candles=bars[:-1]), bars[-1])
    assert any(s.action == "long" for s in sig)


def test_no_template_calls_submit_order_on_any_fixture():
    # _FakeCtx.submit_order raises; every _drive above already routes through it,
    # but assert the contract once more explicitly for the perp set.
    for strat, ctx in [
        (FundingHarvestStrategy(), _FakeCtx(funding=0.01)),
        (BasisTradeStrategy(), _FakeCtx(basis=99.0)),
    ]:
        hist = _path([100, 100])
        _drive(strat, _FakeCtx(funding=getattr(ctx, "_funding"), basis=getattr(ctx, "_basis"),
                               candles=hist[:-1]), hist[-1])


# --- 3. real-engine validation (representative templates) ---------------------


try:
    from flint.adapters import InMemoryUserData as InMemoryStore
except Exception:  # pragma: no cover
    InMemoryStore = None


def _run(strategy, candles):
    """Drive ``strategy`` through the Nautilus bar lane via the engine seam; return
    the final ``PortfolioState``. The seam funds fresh capital from the spec and
    resolves the venue's fill model (ClobFillModel for Hyperliquid) — templates never
    pick a fill model, so this exercises the exact production path."""
    pytest.importorskip("nautilus_trader")
    log = EventLog(InMemoryStore(), TenantContext.local(), run_id="tpl")
    spec = EngineRunSpec(
        config=EngineConfig(),
        venue_spec=HYPERLIQUID,
        initial_capital="100000",
        fund_venue=HL,
        mark_policy="close_derived",
    )
    return engine_for("nautilus")().run(
        EngineFeed(candles=candles), strategy, event_log=log, spec=spec
    )


def test_ma_cross_runs_through_real_engine_without_validation_error():
    strat = EngineStrategy(MaCrossStrategy(fast=2, slow=3, ma_type="sma"))
    # rising path -> a long is emitted and converted by the engine with no raise
    state = _run(strat, _path([100, 101, 102, 103, 104]))
    assert state.position(HL, MKT) is not None


def test_breakout_runs_through_real_engine():
    strat = EngineStrategy(BreakoutStrategy(lookback=3))
    # close 25 breaks the prior 3-bar high on bar 4; bar 5 lets the T+1 order fill.
    state = _run(strat, _path([10, 11, 10, 11, 25, 26]))
    assert state.position(HL, MKT) is not None


# --- 4. ML template -----------------------------------------------------------


def test_ml_template_is_an_mlstrategy_and_features_are_screen_clean():
    assert issubclass(LightGbmTrendStrategy, MLStrategy)
    codes = [v.code for v in screen_source(inspect.getsource(LightGbmTrendStrategy))]
    assert "feature-lookahead" not in codes


def test_ml_template_features_are_causal_and_keyed():
    strat = LightGbmTrendStrategy()
    row = strat.features(MKT, _path([100 + i for i in range(40)]), None)
    assert set(row) == set(LightGbmTrendStrategy.FEATURE_KEYS)
    assert all(isinstance(v, float) for v in row.values())


def test_ml_template_noop_before_a_model_exists():
    strat = LightGbmTrendStrategy()
    adapter = MLEngineStrategy(strat, ModelStore(TenantContext.local(), "lgbm"), seed=0)
    bars = _path([100 + i for i in range(10)])
    assert adapter.on_candle(bars[-1], _FakeCtx(candles=bars[:-1], now=bars[-1].ts)) == []


def test_ml_template_trains_and_trades_with_lightgbm():
    pytest.importorskip("lightgbm")
    strat = LightGbmTrendStrategy(
        warmup_bars=20, label_horizon=3, feat_window=5, fast=3, slow=10,
        rsi_period=5, vol_window=5, n_estimators=10, num_leaves=7,
        min_child_samples=3, retrain_days=1, threshold=0.5,
    )
    adapter = MLEngineStrategy(strat, ModelStore(TenantContext.local(), "lgbm"), seed=7)
    # A long, mostly-rising hand-authored path (not random) with enough closed bars.
    closes = [100 + i + (2 if i % 3 == 0 else 0) for i in range(40)]
    closed = _path(closes)
    now = closed[-1].ts + HOUR_MS
    current = _c(now, closes[-1] + 1)
    out = adapter.on_candle(current, _FakeCtx(candles=closed, now=now))
    assert adapter.retrain_count == 1
    assert adapter.model_store.exists("lgbm_trend")
    _assert_valid(out)  # whatever it decided is a valid Signal list
