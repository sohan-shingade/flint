"""Slice 6.1 — walk-forward (+ purge/embargo) and the Optuna TPE optimizer (§3.3, §11).

Proven here:

1. **Geometry** — ``plan_windows`` carves an expanding-train / forward-OOS walk with
   pinned indices; purge trims the train tail, embargo trims the OOS head.
2. **Leak-guard warning** — a purge below the label horizon warns visibly and on the
   result (§11).
3. **Optimizer correctness** — Optuna TPE finds an interior optimum and the run is
   deterministic under a fixed seed; trial counts are recorded.
4. **Fixture engine run** — a real ``BacktestEngine`` over hand-authored candles (D26,
   no synthetic data) shows the point of walk-forward: a fit that looks good in-sample
   loses out-of-sample when the regime flips.
"""

from __future__ import annotations

import pytest

from flint.adapters import InMemoryUserData
from flint.core.models import Candle, Signal
from flint.engine import BacktestEngine, PortfolioState, build_tearsheet
from flint.engine.fills import NaiveFillModel
from flint.engine.portfolio import EventLog
from flint.ports import TenantContext
from flint.research import (
    ParamSpace,
    WalkForwardConfig,
    WalkForwardWarning,
    plan_windows,
    run_walk_forward,
)

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
MIN_S = 60
MIN_MS = 60 * 1000
T0 = 1_700_000_000_000


# --- hand-authored price path: rise to a peak (idx 11), then fall (D26) --------
# 24 one-minute bars. Long-at-open/close-near-end profits on a rising slice and
# loses on a falling one — the substance behind "OOS < in-sample means overfit".
_PRICES = [100.0 + i for i in range(12)] + [111.0 - (i - 11) for i in range(12, 24)]


def _bar(i: int) -> Candle:
    px = _PRICES[i]
    return Candle(
        ts=T0 + i * MIN_MS,
        open=px,
        high=px + 0.5,
        low=px - 0.5,
        close=px,
        volume=1000.0,
        market=MARKET,
        resolution_s=MIN_S,
        venue=VENUE,
    )


CANDLES = [_bar(i) for i in range(24)]


class _LongThenClose:
    """Long at the slice's first bar; close one bar before its last so the T+1 exit
    market order fills on the final bar (a close on the last bar has no next bar)."""

    def __init__(self, size_usd: float, first_ts: int, close_ts: int) -> None:
        self._size = size_usd
        self._first = first_ts
        self._close = close_ts

    def on_candle(self, candle, ctx):
        if candle.ts == self._first:
            return [Signal.long(MARKET, VENUE, size_usd=self._size)]
        if candle.ts == self._close:
            return [Signal.close(MARKET, VENUE)]
        return []


def _engine_runner(params, start, end):
    """Fixture ``BacktestRunner``: run the honest per-bar engine over ``CANDLES[start:end]``
    with a ``size_usd``-parametrized long, and score on realized net PnL."""
    window = CANDLES[start:end]
    first_ts = window[0].ts
    close_ts = window[-2].ts  # exit decided here fills on window[-1] (T+1)
    state = PortfolioState()
    state.fund(VENUE, "100000")
    log = EventLog(InMemoryUserData(), TenantContext.local(), run_id="wf-fixture")
    engine = BacktestEngine(log, state=state, fill_model=NaiveFillModel())
    engine.run(window, strategy=_LongThenClose(params["size_usd"], first_ts, close_ts))
    return float(build_tearsheet(log.read()).net_pnl)


def _quadratic_runner(peak: float):
    """Pure runner with a known interior optimum at ``x == peak`` — isolates optimizer
    correctness from engine behaviour."""

    def run(params, start, end):
        return -((params["x"] - peak) ** 2)

    return run


# --- 1. ParamSpace parsing ----------------------------------------------------


def test_paramspace_parses_the_cli_range_forms():
    stepped = ParamSpace.parse("entry_spread_bps=2:10:0.5")
    assert (stepped.name, stepped.low, stepped.high, stepped.step) == (
        "entry_spread_bps",
        2.0,
        10.0,
        0.5,
    )
    cont = ParamSpace.parse("x=0:1")
    assert cont.step is None and cont.low == 0.0 and cont.high == 1.0


def test_paramspace_rejects_malformed_or_inverted_ranges():
    with pytest.raises(ValueError):
        ParamSpace.parse("no_equals")
    with pytest.raises(ValueError):
        ParamSpace.parse("x=1:2:3:4")
    with pytest.raises(ValueError):
        ParamSpace(name="x", low=5.0, high=1.0)
    with pytest.raises(ValueError):
        ParamSpace(name="x", low=0.0, high=1.0, step=0.0)


# --- 2. Window geometry (pinned indices) --------------------------------------


def test_plan_windows_expanding_with_purge_and_embargo():
    cfg = WalkForwardConfig(
        n_windows=3, n_trials=1, purge_bars=1, embargo_bars=1, label_horizon_bars=1
    )
    windows = plan_windows(24, cfg)  # step = 24 // 4 = 6
    assert len(windows) == 3
    # train expands from 0; train_end is the block edge minus the purge; oos_start is
    # the block edge plus the embargo; the last window's OOS absorbs the remainder.
    assert (windows[0].train_start, windows[0].train_end) == (0, 5)
    assert (windows[0].test_block_start, windows[0].oos_start, windows[0].oos_end) == (
        6,
        7,
        12,
    )
    assert (windows[1].train_start, windows[1].train_end) == (0, 11)
    assert (windows[1].oos_start, windows[1].oos_end) == (13, 18)
    assert (windows[2].train_start, windows[2].train_end) == (0, 17)
    assert (windows[2].oos_start, windows[2].oos_end) == (19, 24)


def test_plan_windows_rejects_too_few_bars_and_oversized_guards():
    with pytest.raises(ValueError):
        plan_windows(3, WalkForwardConfig(n_windows=3, n_trials=1))  # step == 0
    with pytest.raises(ValueError):  # purge eats the whole first train block
        plan_windows(24, WalkForwardConfig(n_windows=3, n_trials=1, purge_bars=6))
    with pytest.raises(ValueError):  # embargo eats the whole OOS block
        plan_windows(24, WalkForwardConfig(n_windows=3, n_trials=1, embargo_bars=6))


# --- 3. Leak-guard warning (§11) ----------------------------------------------


def test_purge_below_label_horizon_warns_visibly_and_on_the_result():
    cfg = WalkForwardConfig(
        n_windows=2,
        n_trials=3,
        purge_bars=0,
        embargo_bars=0,
        label_horizon_bars=5,  # holding period 5 bars, purge 0 -> leak risk
    )
    space = [ParamSpace("x", 0.0, 10.0)]
    with pytest.warns(WalkForwardWarning, match="below the label horizon"):
        result = run_walk_forward(30, space, _quadratic_runner(3.0), cfg)
    assert any("label horizon" in w for w in result.warnings)


# --- 4. Optimizer correctness + trial counting + determinism ------------------


def test_tpe_finds_the_interior_optimum_and_counts_trials():
    cfg = WalkForwardConfig(
        n_windows=2, n_trials=40, purge_bars=1, embargo_bars=0, label_horizon_bars=1
    )
    space = [ParamSpace("x", 0.0, 10.0)]
    result = run_walk_forward(30, space, _quadratic_runner(3.0), cfg)

    assert result.total_trials == 2 * 40
    assert all(r.n_trials == 40 for r in result.windows)
    for r in result.windows:
        assert r.best_params["x"] == pytest.approx(3.0, abs=0.5)
        assert r.train_score == pytest.approx(0.0, abs=0.5)
        assert len(r.trial_scores) == 40  # every trial's score kept (DSR input, 6.2)


def test_walk_forward_is_deterministic_under_a_fixed_seed():
    cfg = WalkForwardConfig(n_windows=2, n_trials=15, purge_bars=1, label_horizon_bars=1)
    space = [ParamSpace("x", 0.0, 10.0)]
    a = run_walk_forward(30, space, _quadratic_runner(4.0), cfg)
    b = run_walk_forward(30, space, _quadratic_runner(4.0), cfg)
    assert a.oos_scores == b.oos_scores
    assert [r.best_params for r in a.windows] == [r.best_params for r in b.windows]


# --- 5. Fixture engine run — the overfit lesson (D26) -------------------------


def test_fixture_engine_run_surfaces_overfit_when_the_regime_flips():
    # 3 windows over the rise-then-fall path. Every window's in-sample fit looks
    # profitable (it trains through the rising leg), but the last OOS block sits on
    # the falling leg — so the tuned long loses there. That gap is the whole point.
    cfg = WalkForwardConfig(
        n_windows=3, n_trials=12, purge_bars=1, embargo_bars=1, label_horizon_bars=1
    )
    space = [ParamSpace("size_usd", 100.0, 4000.0)]
    result = run_walk_forward(24, space, _engine_runner, cfg)

    assert result.total_trials == 3 * 12
    assert len(result.oos_scores) == 3
    # rising OOS block profits; falling OOS block loses.
    assert result.windows[0].oos_score > 0.0
    assert result.windows[2].oos_score < 0.0
    # the fit that produced the losing OOS looked good in-sample — OOS < in-sample.
    assert result.windows[2].train_score > 0.0
    assert result.windows[2].oos_score < result.windows[2].train_score
    # TPE pushes size toward the top of the range on a profitable train window.
    assert result.windows[0].best_params["size_usd"] > 2000.0
    # the trial count is displayed, not just recorded.
    assert "36 trials total" in result.describe()
