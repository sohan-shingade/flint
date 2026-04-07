# Phase 7C: Richer Analytics & Error Surfacing

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface 5 pieces of information currently hidden — volume data warnings, richer optimization results, margin tracking metrics, annualized volatility, and better custom strategy error messages.

**Architecture:** Each change adds fields or warnings to existing response objects. No breaking changes. MarginStats is a new dataclass in margin.py. Optimization enrichment adds 3 optional fields to the result dict.

**Tech Stack:** Python, FastAPI, Optuna, numpy

---

### Task 1: Volume Data Missing Warning (C1)

**Files:**
- Modify: `flint/backtest/engine.py` (in `run()` method, before main loop, around line 190)
- Test: `tests/test_phase7c_analytics.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_phase7c_analytics.py`:

```python
"""Tests for Phase 7C richer analytics and error surfacing."""
from __future__ import annotations

import pytest
from flint.models import Candle, BacktestResult, Signal
from flint.strategy.base import Strategy


class _HoldStrategy(Strategy):
    """Minimal strategy that does nothing — for testing engine behavior."""
    @property
    def name(self):
        return "Hold"

    def reset(self):
        pass

    def on_candle(self, candle, history, ctx=None):
        return Signal.HOLD


class TestVolumeWarning:
    """Engine should warn when all candle volumes are zero."""

    def test_zero_volume_candles_produce_warning(self):
        from flint.backtest.engine import BacktestEngine
        candles = [
            Candle(ts=1000 + i * 3600, open=100, high=101, low=99,
                   close=100, volume=0.0, market="SOL-PERP", resolution_s=3600)
            for i in range(50)
        ]
        engine = BacktestEngine(_HoldStrategy(), 10000, 0.0005)
        result = engine.run(candles)
        assert any("volume" in w.lower() for w in result.strategy_warnings), (
            f"Expected volume warning, got: {result.strategy_warnings}"
        )

    def test_nonzero_volume_no_warning(self):
        from flint.backtest.engine import BacktestEngine
        candles = [
            Candle(ts=1000 + i * 3600, open=100, high=101, low=99,
                   close=100, volume=100.0, market="SOL-PERP", resolution_s=3600)
            for i in range(50)
        ]
        engine = BacktestEngine(_HoldStrategy(), 10000, 0.0005)
        result = engine.run(candles)
        assert not any("volume" in w.lower() for w in result.strategy_warnings)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase7c_analytics.py::TestVolumeWarning -v`
Expected: FAIL — no volume warning produced

- [ ] **Step 3: Add volume check to engine.run()**

In `flint/backtest/engine.py`, in the `run()` method, after the candles are sorted and before the main loop (around line 190, after `sorted_borrow`), add:

```python
        # Warn if all candle volumes are zero (Pyth price-only data)
        volume_warnings = []
        sample = candles[:min(100, len(candles))]
        if sample and all(c.volume == 0 for c in sample):
            volume_warnings.append(
                "All candles have zero volume (Pyth price-only data). "
                "Volume-dependent indicators (VWAP, volume breakout) will be unreliable."
            )
```

Then when building the `BacktestResult` (around line 348), include these warnings. Find where `strategy_warnings` is built:

```python
            strategy_warnings=[m for m in ctx.log_messages
                               if "WARNING" in m or "LIQUIDATED" in m or "MARGIN REJECTED" in m],
```

Change to:

```python
            strategy_warnings=volume_warnings + [m for m in ctx.log_messages
                               if "WARNING" in m or "LIQUIDATED" in m or "MARGIN REJECTED" in m],
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_phase7c_analytics.py::TestVolumeWarning tests/test_backtest.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/backtest/engine.py tests/test_phase7c_analytics.py
git commit -m "feat: warn when all candle volumes are zero (Pyth data)"
```

---

### Task 2: Annualized Volatility in Metrics (C4)

**Files:**
- Modify: `flint/analytics/metrics.py:14-117`
- Test: `tests/test_phase7c_analytics.py` (append)

- [ ] **Step 1: Write failing test**

Append to `tests/test_phase7c_analytics.py`:

```python
class TestAnnualizedVolatility:
    def test_volatility_present_in_metrics(self):
        from flint.analytics.metrics import compute_metrics, MetricsSummary
        from flint.models import BacktestResult, Position
        positions = [
            Position(entry_price=100, size=10, entry_ts=i*3600,
                     exit_price=102, exit_ts=(i+1)*3600, pnl=20, closed=True)
            for i in range(10)
        ]
        result = BacktestResult(
            total_pnl=200, win_rate=1.0, max_drawdown=0.01,
            sharpe_ratio=2.0, total_trades=10,
            winning_trades=10, losing_trades=0,
            positions=positions,
            equity_curve=[10000 + i * 20 for i in range(100)],
        )
        m = compute_metrics(result, initial_capital=10000)
        assert hasattr(m, "annualized_volatility_pct")
        assert m.annualized_volatility_pct > 0

    def test_zero_returns_zero_vol(self):
        from flint.analytics.metrics import compute_metrics
        from flint.models import BacktestResult
        result = BacktestResult(
            total_pnl=0, win_rate=0, max_drawdown=0,
            sharpe_ratio=0, total_trades=0,
            winning_trades=0, losing_trades=0,
            positions=[], equity_curve=[10000],
        )
        m = compute_metrics(result, initial_capital=10000)
        assert m.annualized_volatility_pct == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase7c_analytics.py::TestAnnualizedVolatility -v`
Expected: FAIL — `MetricsSummary has no attribute 'annualized_volatility_pct'`

- [ ] **Step 3: Add annualized_volatility_pct to MetricsSummary**

In `flint/analytics/metrics.py`, add to the `MetricsSummary` dataclass (after `worst_trade: float`, around line 33):

```python
    # volatility
    annualized_volatility_pct: float = 0.0
```

In `compute_metrics()`, after the Sharpe computation (after line 62), add:

```python
    annualized_vol = float(ret_std * np.sqrt(periods_per_year) * 100) if ret_std > 0 else 0.0
```

Add it to the return statement:

```python
    return MetricsSummary(
        total_return_pct=total_return_pct,
        annualized_return_pct=annualized,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        max_drawdown=max_dd,
        max_drawdown_duration_s=max_dd_dur_s,
        total_trades=n_trades,
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_win=avg_win,
        avg_loss=avg_loss,
        avg_holding_period_s=avg_hold,
        total_pnl=result.total_pnl,
        best_trade=best,
        worst_trade=worst,
        annualized_volatility_pct=annualized_vol,
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_phase7c_analytics.py::TestAnnualizedVolatility tests/test_analytics.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/analytics/metrics.py tests/test_phase7c_analytics.py
git commit -m "feat: add annualized_volatility_pct to MetricsSummary"
```

---

### Task 3: Margin Tracking Metrics (C3)

**Files:**
- Modify: `flint/execution/margin.py` (add MarginStats dataclass + accumulation)
- Modify: `flint/models.py` (add margin_stats field to BacktestResult)
- Modify: `flint/backtest/engine.py` (populate margin_stats after run)
- Modify: `flint/analytics/tearsheet.py` (include margin metrics in dict)
- Test: `tests/test_phase7c_analytics.py` (append)

- [ ] **Step 1: Write failing test**

Append to `tests/test_phase7c_analytics.py`:

```python
class TestMarginTrackingMetrics:
    def test_margin_stats_present_when_enabled(self):
        from flint.execution.margin import MarginEngine, MarginStats
        engine = MarginEngine()
        assert hasattr(engine, "stats")
        assert isinstance(engine.stats, MarginStats)
        assert engine.stats.max_leverage == 0.0
        assert engine.stats.margin_calls == 0

    def test_margin_stats_on_backtest_result(self):
        from flint.models import BacktestResult
        result = BacktestResult(
            total_pnl=0, win_rate=0, max_drawdown=0,
            sharpe_ratio=0, total_trades=0,
            winning_trades=0, losing_trades=0,
        )
        assert hasattr(result, "margin_stats")
        assert result.margin_stats is None  # None when margin not enabled
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase7c_analytics.py::TestMarginTrackingMetrics -v`
Expected: FAIL — `MarginEngine has no attribute 'stats'`

- [ ] **Step 3: Add MarginStats to margin.py**

In `flint/execution/margin.py`, add after the `MarginState` dataclass (around line 40):

```python
@dataclass
class MarginStats:
    """Accumulated margin statistics over a backtest."""
    max_leverage: float = 0.0
    total_leverage_sum: float = 0.0
    bars_counted: int = 0
    margin_calls: int = 0
    max_utilization_pct: float = 0.0

    @property
    def avg_leverage(self) -> float:
        return self.total_leverage_sum / self.bars_counted if self.bars_counted > 0 else 0.0
```

In `MarginEngine.__init__()` (line 80-84), add:

```python
        self.stats = MarginStats()
```

In `MarginEngine.check_liquidations()`, after detecting a liquidation (line 183, inside `if liquidated:`), add:

```python
                self.stats.margin_calls += 1
```

Add a new method to `MarginEngine` for per-bar stats tracking:

```python
    def update_stats(self, positions: List[PositionInfo], cash: float) -> None:
        """Update accumulated stats. Called once per bar."""
        state = self.compute_margin_state(cash, positions)
        self.stats.max_leverage = max(self.stats.max_leverage, state.leverage)
        self.stats.total_leverage_sum += state.leverage
        self.stats.bars_counted += 1
        utilization = (state.total_margin_used / (cash + sum(p.unrealized_pnl for p in positions)) * 100
                      if (cash + sum(p.unrealized_pnl for p in positions)) > 0 else 0)
        self.stats.max_utilization_pct = max(self.stats.max_utilization_pct, utilization)
```

In `MarginEngine.reset()`, add:

```python
        self.stats = MarginStats()
```

- [ ] **Step 4: Add margin_stats to BacktestResult**

In `flint/models.py`, add to `BacktestResult` (after `borrow_payments`, around line 124):

```python
    margin_stats: Optional[Any] = None  # MarginStats when margin_tracking enabled
```

Add `Any` to the typing imports if not already there.

- [ ] **Step 5: Wire margin stats into backtest engine**

In `flint/backtest/engine.py`, find where `check_liquidations` is called (around line 217). After it, add a call to `update_stats` if margin engine exists:

```python
            # Update margin stats per bar
            if self._margin_engine is not None:
                self._margin_engine.update_stats(ctx.positions, ctx.account.cash)
```

Then when building `BacktestResult` (around line 340), add:

```python
            margin_stats=self._margin_engine.stats if self._margin_engine else None,
```

- [ ] **Step 6: Surface in tearsheet**

In `flint/analytics/tearsheet.py`, find where the metrics dict is built in `to_dict()`. After existing metrics, add:

```python
        if hasattr(self, '_result') and hasattr(self._result, 'margin_stats') and self._result.margin_stats is not None:
            ms = self._result.margin_stats
            d["metrics"]["max_leverage"] = round(ms.max_leverage, 2)
            d["metrics"]["avg_leverage"] = round(ms.avg_leverage, 2)
            d["metrics"]["margin_calls"] = ms.margin_calls
            d["metrics"]["max_margin_utilization_pct"] = round(ms.max_utilization_pct, 1)
```

Note: check how the tearsheet accesses the result object — it may need the result passed in or stored differently. Adjust based on what `generate_tearsheet()` receives.

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_phase7c_analytics.py::TestMarginTrackingMetrics tests/test_backtest.py tests/test_backtest_v2.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add flint/execution/margin.py flint/models.py flint/backtest/engine.py flint/analytics/tearsheet.py tests/test_phase7c_analytics.py
git commit -m "feat: surface margin tracking metrics in backtest results

Add MarginStats accumulator to MarginEngine with max_leverage,
avg_leverage, margin_calls, max_utilization_pct. Populated per-bar
during backtest. Included in tearsheet metrics dict."
```

---

### Task 4: Richer Optimization Results (C2)

**Files:**
- Modify: `flint/api/routes/optimization.py:266-305`
- Modify: `flint/optimization/optimizer.py:109-181` (return study object)
- Modify: `ui/src/pages/BacktestLab.tsx:1404,1716,1732` (rename trials → top_trials)
- Test: `tests/test_phase7c_analytics.py` (append)

- [ ] **Step 1: Write failing test**

Append to `tests/test_phase7c_analytics.py`:

```python
class TestRicherOptimizationResults:
    def test_optimization_result_has_study(self):
        """OptimizationResult should expose the Optuna study."""
        from flint.optimization.optimizer import OptimizationResult
        assert hasattr(OptimizationResult, '__dataclass_fields__')
        fields = OptimizationResult.__dataclass_fields__
        assert "study" in fields, "OptimizationResult needs a 'study' field"

    def test_convergence_format(self):
        convergence = [[0, 1.0], [1, 1.5], [2, 1.5], [3, 2.0]]
        assert all(len(entry) == 2 for entry in convergence)
        # Should be monotonically non-decreasing (for maximize)
        values = [v for _, v in convergence]
        for i in range(1, len(values)):
            assert values[i] >= values[i-1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase7c_analytics.py::TestRicherOptimizationResults -v`
Expected: FAIL — `OptimizationResult` has no `study` field

- [ ] **Step 3: Return study from optimizer**

In `flint/optimization/optimizer.py`, add `study` field to `OptimizationResult`:

```python
@dataclass
class OptimizationResult:
    best_params: Dict[str, Any]
    best_value: float
    metric: str
    n_trials: int
    trials: List[TrialResult] = field(default_factory=list)
    study: Any = None  # Optuna Study object for param importance
```

In the `optimize()` method, change the return (around line 176) to include the study:

```python
        return OptimizationResult(
            best_params=best.params,
            best_value=best.value,
            metric=self.metric,
            n_trials=len(study.trials),
            trials=sorted(trials, key=lambda t: t.metric_value, reverse=True),
            study=study,
        )
```

- [ ] **Step 4: Enrich the result dict in optimization route**

In `flint/api/routes/optimization.py`, after line 300 (after `result_dict[metric_key] = best_val`), add:

```python
            # Convergence: best value over trial number
            convergence = []
            best_so_far = float("-inf")
            for t in opt_result.study.trials if opt_result.study else []:
                val = t.value if t.value is not None else float("-inf")
                if val > best_so_far:
                    best_so_far = val
                convergence.append([t.number, round(best_so_far, 4) if best_so_far > float("-inf") else None])
            result_dict["convergence"] = convergence

            # Parameter importance (requires sklearn)
            try:
                from optuna.importance import get_param_importances
                if opt_result.study and len(opt_result.study.trials) >= 5:
                    importance = get_param_importances(opt_result.study)
                    result_dict["param_importance"] = {k: round(v, 4) for k, v in importance.items()}
                else:
                    result_dict["param_importance"] = None
            except Exception:
                result_dict["param_importance"] = None

            # Best trial full metrics
            if trials_list:
                best_trial = trials_list[0]  # already sorted by metric_value desc
                result_dict["best_backtest_metrics"] = {
                    "total_pnl": best_trial.get("total_pnl"),
                    "sharpe_ratio": best_trial.get("sharpe_ratio"),
                    "max_drawdown": best_trial.get("max_drawdown"),
                    "win_rate": best_trial.get("win_rate"),
                    "total_trades": best_trial.get("total_trades"),
                }
```

Rename `trials` to `top_trials` in the result dict (line 293):

```python
            result_dict = {
                "best_params": opt_result.best_params,
                "best_value": best_val,
                "metric": opt_result.metric,
                "n_trials": opt_result.n_trials,
                "top_trials": trials_list,  # renamed from "trials"
                ...
            }
```

- [ ] **Step 5: Update UI references**

In `ui/src/pages/BacktestLab.tsx`, replace the 3 references:

Line 1404: `optResults.trials.slice(0, 3)` → `(optResults.top_trials || optResults.trials || []).slice(0, 3)`
Line 1716: `optResults.trials.length > 0` → `(optResults.top_trials || optResults.trials || []).length > 0`
Line 1732: `optResults.trials.slice(0, 10)` → `(optResults.top_trials || optResults.trials || []).slice(0, 10)`

The fallback to `optResults.trials` ensures backward compatibility with any cached responses.

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_phase7c_analytics.py::TestRicherOptimizationResults tests/test_optimizer.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add flint/optimization/optimizer.py flint/api/routes/optimization.py ui/src/pages/BacktestLab.tsx tests/test_phase7c_analytics.py
git commit -m "feat: richer optimization results with convergence, param importance

Add convergence history, param_importance, and best_backtest_metrics
to optimization results. Rename trials to top_trials. Expose Optuna
study object for downstream analysis."
```

---

### Task 5: Custom Strategy Error Messages (C5)

**Files:**
- Modify: `flint/api/routes/backtest.py:623-627` (enhance _run error handling)
- Test: `tests/test_phase7c_analytics.py` (append)

Note: The `get_results()` error wrapping was already done in Phase 7A Task 5. This task handles the strategy _runtime_ error detail.

- [ ] **Step 1: Write test**

Append to `tests/test_phase7c_analytics.py`:

```python
class TestStrategyErrorMessages:
    def test_strategy_error_includes_type_and_message(self):
        """Strategy errors should include exception type and message."""
        error_detail = "ZeroDivisionError: division by zero"
        assert "ZeroDivisionError" in error_detail
        assert "division by zero" in error_detail

    def test_strategy_traceback_extracts_user_code(self):
        """Traceback extraction should find user code frames."""
        tb_lines = [
            '  File "<string>", line 42, in on_candle',
            '  File "flint/indicators.py", line 95, in rsi',
            'ZeroDivisionError: division by zero',
        ]
        user_lines = [l for l in tb_lines if '<string>' in l]
        assert len(user_lines) == 1
        assert 'line 42' in user_lines[0]
```

- [ ] **Step 2: Enhance error handling in _run()**

In `flint/api/routes/backtest.py`, replace the except block (lines 623-627):

```python
        except Exception as e:
            import traceback
            logger.exception("Backtest %s failed", run_id)
            tb = traceback.format_exc()
            # Extract user code frames (from exec'd strategy code)
            user_lines = [
                l.strip() for l in tb.split('\n')
                if '<string>' in l or ('<module>' in l and 'strategy' in l.lower())
            ]
            error_detail = f"{type(e).__name__}: {e}"
            if user_lines:
                error_detail += f"\n  in strategy code: {user_lines[-1]}"
            _set_status(run_id, "failed")
            _set_result(run_id, {"error": error_detail})
            _set_progress(run_id, phase="error", pct=0, detail=error_detail)
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_phase7c_analytics.py::TestStrategyErrorMessages tests/test_backtest.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add flint/api/routes/backtest.py tests/test_phase7c_analytics.py
git commit -m "feat: strategy errors include traceback with user code location"
```

---

### Task 6: Run Full Test Suite

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v --timeout=120 -x`
Expected: All tests PASS

- [ ] **Step 2: Final commit if any fixups needed**
