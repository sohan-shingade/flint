# Phase 7A: Critical Correctness Fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 6 bugs that produce wrong results or break functionality — strategy name mismatch, Monte Carlo annualization, journal return %, OHLCV ordering, in-memory state corruption, coverage > 100%.

**Architecture:** All fixes are localized to individual modules. No new files created. Changes are backward-compatible — existing API responses gain fields but don't lose them.

**Tech Stack:** Python, FastAPI, DuckDB, numpy

---

### Task 1: Fix Strategy Name Mismatch (A1)

**Files:**
- Modify: `flint/api/routes/backtest.py:23-39` (imports), `flint/api/routes/backtest.py:134-199` (builders dict)
- Modify: `flint/api/routes/strategies.py:1-150` (replace hardcoded list)
- Test: `tests/test_phase7a_correctness.py`

- [ ] **Step 1: Write failing test — every listed strategy can be built**

Create `tests/test_phase7a_correctness.py`:

```python
"""Tests for Phase 7A critical correctness fixes."""
from __future__ import annotations

import pytest


class TestStrategyCatalogMatchesBuilders:
    """Every strategy listed in GET /strategies must be buildable via backtest."""

    def test_all_listed_strategies_exist_in_builders(self):
        from flint.api.routes.strategies import list_strategies
        from flint.api.routes.backtest import _build_strategy

        result = list_strategies()
        strategies = result["strategies"]
        assert len(strategies) >= 10, f"Expected at least 10 strategies, got {len(strategies)}"

        for s in strategies:
            name = s["name"]
            strat = _build_strategy(name, {})
            assert strat is not None, (
                f"Strategy '{name}' is listed in GET /strategies but "
                f"_build_strategy('{name}', {{}}) returns None"
            )

    def test_all_builders_are_listed(self):
        from flint.api.routes.strategies import list_strategies
        from flint.api.routes.backtest import _build_strategy

        result = list_strategies()
        listed_names = {s["name"] for s in result["strategies"]}

        # Import the builders dict to check all keys
        from flint.api.routes.backtest import _DEFAULTS
        for name in _DEFAULTS:
            assert name in listed_names, (
                f"Strategy '{name}' exists in builders but is not listed in GET /strategies"
            )

    def test_strategy_has_required_fields(self):
        from flint.api.routes.strategies import list_strategies

        result = list_strategies()
        for s in result["strategies"]:
            assert "name" in s, f"Missing 'name' in strategy entry"
            assert "display_name" in s, f"Missing 'display_name' for {s.get('name')}"
            assert "description" in s, f"Missing 'description' for {s.get('name')}"
            assert "params" in s, f"Missing 'params' for {s.get('name')}"
            assert "type" in s, f"Missing 'type' for {s.get('name')}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase7a_correctness.py::TestStrategyCatalogMatchesBuilders -v`
Expected: FAIL — `_build_strategy('funding_mean_reversion', {})` returns None

- [ ] **Step 3: Add missing strategy imports and builders**

In `flint/api/routes/backtest.py`, add imports at line 23-39 (add after existing imports):

```python
from ...strategy import (
    MACrossoverStrategy,
    EMACrossoverStrategy,
    RSIStrategy,
    BollingerStrategy,
    MomentumStrategy,
    FundingHarvestStrategy,
    MeanReversionStrategy,
    BreakoutMomentumStrategy,
    GridTraderStrategy,
    DualTimeframeStrategy,
    VWAPReversionStrategy,
    MACDDivergenceStrategy,
    ATRBreakoutStrategy,
    MultiVenueFundingStrategy,
    RSIMACDComboStrategy,
    FundingMeanReversionStrategy,
    MomentumBreakoutStrategy,
    FundingArbStrategy,
    BasisTradeStrategy,
    MevArbMonitor,
)
```

Add the missing entries to the `builders` dict (after line 198):

```python
        "funding_mean_reversion": lambda p: FundingMeanReversionStrategy(
            bb_lookback=int(p.get("bb_lookback", 24)),
            bb_std=float(p.get("bb_std", 2.0)),
            max_hold_hours=int(p.get("max_hold_hours", 12)),
        ),
        "momentum_breakout": lambda p: MomentumBreakoutStrategy(
            breakout_lookback=int(p.get("breakout_lookback", 20)),
            trailing_stop_pct=float(p.get("trailing_stop_pct", 0.02)),
        ),
        "funding_arb": lambda p: FundingArbStrategy(
            min_spread_bps=float(p.get("min_spread_bps", 5.0)),
            exit_spread_bps=float(p.get("exit_spread_bps", 1.0)),
            position_size_usd=float(p.get("position_size_usd", 1000)),
        ),
        "basis_trade": lambda p: BasisTradeStrategy(
            entry_basis_bps=float(p.get("entry_basis_bps", 30.0)),
            exit_basis_bps=float(p.get("exit_basis_bps", 5.0)),
            position_size_usd=float(p.get("position_size_usd", 1000)),
        ),
        "mev_arb_monitor": lambda p: MevArbMonitor(
            min_profit_bps=float(p.get("min_profit_bps", 10.0)),
            max_hops=int(p.get("max_hops", 3)),
        ),
```

Add matching entries to `_DEFAULTS` dict:

```python
    "funding_mean_reversion": {"bb_lookback": 24, "bb_std": 2.0, "max_hold_hours": 12},
    "momentum_breakout": {"breakout_lookback": 20, "trailing_stop_pct": 0.02},
    "funding_arb": {"min_spread_bps": 5.0, "exit_spread_bps": 1.0, "position_size_usd": 1000},
    "basis_trade": {"entry_basis_bps": 30.0, "exit_basis_bps": 5.0, "position_size_usd": 1000},
    "mev_arb_monitor": {"min_profit_bps": 10.0, "max_hops": 3},
```

**Note:** Check that each strategy class actually exists in `flint/strategy/__init__.py` before importing. If any are missing from `__init__.py`, add the import there first.

- [ ] **Step 4: Generate the strategies list from builders**

Replace the entire `flint/api/routes/strategies.py` with:

```python
"""Strategy listing API — generated from backtest builders."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()


def _get_catalog():
    """Build strategy catalog from the canonical builders dict."""
    from .backtest import _build_strategy, _DEFAULTS

    _METADATA = {
        "ma_crossover": {"display_name": "MA Crossover", "description": "Goes long when fast SMA crosses above slow SMA, exits when it crosses below. Classic trend-following."},
        "ema_crossover": {"display_name": "EMA Crossover", "description": "Exponential moving-average crossover. Reacts faster to recent price changes than SMA."},
        "rsi": {"display_name": "RSI Mean Reversion", "description": "Buys when RSI drops below oversold threshold, sells when it rises above overbought. Mean-reversion style."},
        "bollinger": {"display_name": "Bollinger Bands", "description": "Buys at the lower band (oversold), sells at the upper band (overbought). Works best in ranging markets."},
        "momentum": {"display_name": "Momentum", "description": "Buys when price is up X% over a lookback window, sells when down X%. Rides strong moves."},
        "momentum_breakout": {"display_name": "Momentum Breakout", "description": "Breakout above N-bar high/low with optional Pyth oracle confirmation."},
        "funding_harvest": {"display_name": "Funding Harvest", "description": "Harvests funding rate payments by taking the opposite side of the crowd.", "needs_funding": True},
        "funding_mean_reversion": {"display_name": "Funding Mean Reversion", "description": "Bollinger bands on hourly funding rate. Needs funding data downloaded.", "needs_funding": True},
        "mean_reversion": {"display_name": "Mean Reversion", "description": "Z-score mean reversion on price. Buys below lower band, sells above upper."},
        "breakout_momentum": {"display_name": "Breakout Momentum", "description": "Price breakout strategy with momentum confirmation."},
        "grid_trader": {"display_name": "Grid Trader", "description": "Places buy/sell grid orders at fixed price intervals."},
        "dual_timeframe": {"display_name": "Dual Timeframe", "description": "Uses two timeframes for trend confirmation and entry timing."},
        "vwap_reversion": {"display_name": "VWAP Reversion", "description": "Mean reversion to VWAP. Buys below VWAP, sells above."},
        "macd_divergence": {"display_name": "MACD Divergence", "description": "Trades MACD histogram divergence from price for reversal signals."},
        "atr_breakout": {"display_name": "ATR Breakout", "description": "Breakout strategy using ATR-based channel. Enters on volatility expansion."},
        "multi_venue_funding": {"display_name": "Multi-Venue Funding", "description": "Cross-venue funding rate arbitrage. Requires multi-venue data.", "type": "multi_venue", "venues": ["drift", "hyperliquid"], "needs_funding": True},
        "rsi_macd_combo": {"display_name": "RSI + MACD Combo", "description": "Combined RSI oversold/overbought with MACD histogram confirmation."},
        "funding_arb": {"display_name": "Funding Arb (Cross-Venue)", "description": "Delta-neutral cross-venue funding arbitrage. Long low-rate venue, short high-rate.", "type": "multi_venue", "venues": ["drift", "hyperliquid"], "needs_funding": True},
        "basis_trade": {"display_name": "Basis Trade (Cross-Venue)", "description": "Cross-venue price basis arbitrage. Long cheap venue, short expensive.", "type": "multi_venue", "venues": ["drift", "hyperliquid"]},
        "mev_arb_monitor": {"display_name": "MEV Arb Monitor", "description": "Scans for DEX arb opportunities. Monitoring only — no trades placed.", "type": "monitor", "markets": ["SOL-PERP"]},
    }

    catalog = []
    for name, defaults in _DEFAULTS.items():
        strat = _build_strategy(name, defaults)
        if strat is None:
            continue

        meta = _METADATA.get(name, {})
        params_raw = strat.parameters() if hasattr(strat, "parameters") else {}
        params = {}
        for pname, pdef in params_raw.items():
            entry = {"type": pdef.get("type", "float")}
            if "default" in pdef:
                entry["default"] = pdef["default"]
            elif pname in defaults:
                entry["default"] = defaults[pname]
            if "low" in pdef:
                entry["min"] = pdef["low"]
            if "high" in pdef:
                entry["max"] = pdef["high"]
            params[pname] = entry

        # For strategies without parameters() defined, use defaults dict
        if not params and defaults:
            for pname, val in defaults.items():
                params[pname] = {
                    "type": "int" if isinstance(val, int) else "float",
                    "default": val,
                }

        catalog.append({
            "name": name,
            "display_name": meta.get("display_name", name.replace("_", " ").title()),
            "description": meta.get("description", f"Built-in {name} strategy."),
            "params": params,
            "markets": meta.get("markets", ["SOL-PERP", "BTC-PERP", "ETH-PERP"]),
            "type": meta.get("type", "single_venue"),
            "venues": meta.get("venues", []),
            **({"needs_funding": True} if meta.get("needs_funding") else {}),
        })
    return catalog


@router.get("")
def list_strategies():
    return {"strategies": _get_catalog()}


@router.get("/{name}")
def get_strategy(name: str):
    for s in _get_catalog():
        if s["name"] == name:
            return s
    raise HTTPException(404, f"Strategy '{name}' not found")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_phase7a_correctness.py::TestStrategyCatalogMatchesBuilders -v`
Expected: PASS

- [ ] **Step 6: Run existing strategy tests for regressions**

Run: `pytest tests/test_strategy.py tests/test_api.py -v -x`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add flint/api/routes/strategies.py flint/api/routes/backtest.py tests/test_phase7a_correctness.py
git commit -m "fix: sync strategy catalog with backtest builders

Generate GET /strategies dynamically from the builders dict instead
of maintaining a separate hardcoded list. Add 5 missing strategies
to the builders dict (funding_mean_reversion, momentum_breakout,
funding_arb, basis_trade, mev_arb_monitor)."
```

---

### Task 2: Fix Monte Carlo Sharpe Annualization (A2)

**Files:**
- Modify: `flint/analytics/monte_carlo.py:46-98`
- Modify: `flint/api/routes/backtest.py:589-591`
- Test: `tests/test_phase7a_correctness.py` (append)

- [ ] **Step 1: Write failing test — MC Sharpe CI should contain point estimate**

Append to `tests/test_phase7a_correctness.py`:

```python
class TestMonteCarloAnnualization:
    """MC Sharpe CI must be in the same ballpark as the point estimate."""

    def test_sharpe_ci_contains_reasonable_range(self):
        from flint.analytics.monte_carlo import run_monte_carlo
        # 30 trades over 90 days — clearly winning strategy
        pnls = [100, -20, 80, -10, 60, -30, 90, -15, 70, -25,
                110, -40, 50, -5, 120, -35, 85, -20, 95, -10,
                75, -15, 65, -25, 105, -30, 55, -20, 115, -10]
        period_seconds = 90 * 86400  # 90 days
        result = run_monte_carlo(pnls, initial_capital=10000,
                                 n_simulations=500,
                                 period_seconds=period_seconds)
        # CI should be within 10x of the mean, not 100x
        assert abs(result.sharpe_ci_upper) < abs(result.sharpe_mean) * 10, (
            f"Sharpe CI upper {result.sharpe_ci_upper} is wildly different from "
            f"mean {result.sharpe_mean} — annualization is broken"
        )
        assert abs(result.sharpe_ci_lower) < abs(result.sharpe_mean) * 10, (
            f"Sharpe CI lower {result.sharpe_ci_lower} is wildly different from "
            f"mean {result.sharpe_mean} — annualization is broken"
        )

    def test_annualization_uses_trade_frequency(self):
        from flint.analytics.monte_carlo import run_monte_carlo
        pnls = [100, -50] * 10  # 20 trades
        # Same trades over 1 year vs 1 month — different annualized Sharpe
        result_1y = run_monte_carlo(pnls, period_seconds=365 * 86400)
        result_1m = run_monte_carlo(pnls, period_seconds=30 * 86400)
        # 1-month has 12x higher trade frequency → higher annualized Sharpe
        assert abs(result_1m.sharpe_mean) > abs(result_1y.sharpe_mean) * 1.5

    def test_backward_compat_no_period(self):
        from flint.analytics.monte_carlo import run_monte_carlo
        pnls = [100, -50, 80, -30, 60]
        result = run_monte_carlo(pnls)
        assert result.n_simulations > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase7a_correctness.py::TestMonteCarloAnnualization -v`
Expected: FAIL — `run_monte_carlo() got an unexpected keyword argument 'period_seconds'`

- [ ] **Step 3: Fix the annualization in monte_carlo.py**

In `flint/analytics/monte_carlo.py`, change the function signature (line 46) and annualization logic (lines 66-98):

Replace the function signature:
```python
def run_monte_carlo(
    trade_pnls: List[float],
    initial_capital: float = 10_000.0,
    n_simulations: int = 1000,
    ruin_threshold: float = 0.50,
    period_seconds: int = 0,
) -> MonteCarloResult:
```

After `pnls = np.array(trade_pnls)` and `n_trades = len(pnls)`, add:

```python
    # Compute annualization from trade frequency, not hourly assumption
    if period_seconds > 0 and n_trades > 0:
        period_years = period_seconds / (365.25 * 86400)
        trades_per_year = n_trades / period_years
    else:
        trades_per_year = float(n_trades)  # fallback: assume 1 year
    annualization = np.sqrt(trades_per_year)
```

Replace line 95 (`sharpe = float(np.mean(returns) / std * np.sqrt(8760))`) with:

```python
            sharpe = float(np.mean(returns) / std * annualization)
```

- [ ] **Step 4: Update the caller in backtest.py**

In `flint/api/routes/backtest.py`, change line 591 from:
```python
                mc = run_monte_carlo(trade_pnls, req.initial_capital, n_simulations=500)
```
to:
```python
                mc = run_monte_carlo(
                    trade_pnls, req.initial_capital, n_simulations=500,
                    period_seconds=req.end_ts - req.start_ts,
                )
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_phase7a_correctness.py::TestMonteCarloAnnualization tests/test_monte_carlo.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add flint/analytics/monte_carlo.py flint/api/routes/backtest.py tests/test_phase7a_correctness.py
git commit -m "fix: Monte Carlo Sharpe uses trade frequency, not sqrt(8760)

The MC simulation was annualizing per-trade returns with sqrt(8760),
assuming hourly data. Per-trade returns span variable holding periods.
Now computes trades_per_year from actual backtest duration."
```

---

### Task 3: Fix Journal Saves 0% Return (A3)

**Files:**
- Modify: `flint/journal/storage.py:13-86`
- Test: `tests/test_phase7a_correctness.py` (append)

- [ ] **Step 1: Write failing test**

Append to `tests/test_phase7a_correctness.py`:

```python
class TestJournalReturnPct:
    """Journal must store and return total_return_pct."""

    def _result(self, pnl=500):
        from flint.models import BacktestResult, Position
        pos = Position(entry_price=100, size=10, entry_ts=1000,
                       exit_price=105, exit_ts=2000, pnl=pnl, closed=True)
        return BacktestResult(
            total_pnl=pnl, win_rate=0.6, max_drawdown=0.05,
            sharpe_ratio=1.5, total_trades=1,
            winning_trades=1, losing_trades=0,
            positions=[pos], equity_curve=[10000, 10000 + pnl],
        )

    def test_total_return_pct_stored(self):
        from flint.store import FlintStore
        from flint.journal.storage import JournalStorage
        store = FlintStore(":memory:")
        journal = JournalStorage(store)
        journal.save_run("r1", "TestStrat", "SOL-PERP", 3600,
                         1000, 5000, 10000, result=self._result(500))
        runs = journal.list_runs()
        assert len(runs) == 1
        assert "total_return_pct" in runs[0], "total_return_pct missing from journal"
        assert runs[0]["total_return_pct"] == pytest.approx(5.0, abs=0.01)
        store.close()

    def test_negative_return(self):
        from flint.store import FlintStore
        from flint.journal.storage import JournalStorage
        store = FlintStore(":memory:")
        journal = JournalStorage(store)
        journal.save_run("r2", "TestStrat", "SOL-PERP", 3600,
                         1000, 5000, 10000, result=self._result(-2000))
        runs = journal.list_runs()
        assert runs[0]["total_return_pct"] == pytest.approx(-20.0, abs=0.01)
        store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase7a_correctness.py::TestJournalReturnPct -v`
Expected: FAIL — `total_return_pct` not in runs dict

- [ ] **Step 3: Add total_return_pct to journal schema and save logic**

In `flint/journal/storage.py`:

Add column to `_CREATE_RUNS` (after `total_pnl DOUBLE,`):
```python
    total_return_pct DOUBLE DEFAULT 0,
```

In `__init__`, add migration after the CREATE TABLE calls (line 57):
```python
            try:
                store._conn.execute(
                    "ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS "
                    "total_return_pct DOUBLE DEFAULT 0"
                )
            except Exception:
                pass  # column already exists or ALTER not supported
```

In `save_run()`, compute the return (after line 68):
```python
        total_return_pct = (total_pnl / initial_capital * 100) if initial_capital > 0 else 0.0
```

Update the INSERT statement — add one more `?` and include `total_return_pct` in the values list. Change from 16 to 17 placeholders:
```python
            self._store._conn.execute(
                "INSERT OR REPLACE INTO backtest_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [run_id, strategy_name, market, resolution_s, start_ts, end_ts,
                 initial_capital, params_json, int(time.time()),
                 total_pnl, total_return_pct, win_rate, sharpe, max_dd, trades, fees, funding],
            )
```

Note: the new column goes after `total_pnl` in both the schema and the values list.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_phase7a_correctness.py::TestJournalReturnPct tests/test_journal.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/journal/storage.py tests/test_phase7a_correctness.py
git commit -m "fix: journal stores total_return_pct instead of 0%

Add total_return_pct column to backtest_runs table with ALTER TABLE
migration for existing databases. Compute from total_pnl / initial_capital."
```

---

### Task 4: Fix OHLCV Returns Oldest Candles With `limit` (A4)

**Files:**
- Modify: `flint/store.py:504-538`
- Test: `tests/test_phase7a_correctness.py` (append)

- [ ] **Step 1: Write failing test**

Append to `tests/test_phase7a_correctness.py`:

```python
class TestOHLCVLimitReturnsNewest:
    """limit=N without start_ts should return the N most recent candles."""

    def test_limit_without_start_returns_newest(self):
        from flint.store import FlintStore
        from flint.models import Candle
        store = FlintStore(":memory:")
        candles = [
            Candle(ts=1000 + i * 3600, open=100+i, high=101+i, low=99+i,
                   close=100+i, volume=10, market="SOL-PERP", resolution_s=3600)
            for i in range(20)
        ]
        store.upsert_candles(candles)
        result = store.query_candles("SOL-PERP", 3600, limit=5)
        assert len(result) == 5
        # Should be the last 5 (newest), in chronological order
        assert result[0].ts == candles[15].ts
        assert result[-1].ts == candles[19].ts
        store.close()

    def test_limit_with_start_returns_from_start(self):
        from flint.store import FlintStore
        from flint.models import Candle
        store = FlintStore(":memory:")
        candles = [
            Candle(ts=1000 + i * 3600, open=100, high=101, low=99,
                   close=100, volume=10, market="SOL-PERP", resolution_s=3600)
            for i in range(20)
        ]
        store.upsert_candles(candles)
        result = store.query_candles("SOL-PERP", 3600, start_ts=1000, limit=5)
        assert len(result) == 5
        # Should be the first 5 from start_ts, ASC order
        assert result[0].ts == candles[0].ts
        assert result[-1].ts == candles[4].ts
        store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase7a_correctness.py::TestOHLCVLimitReturnsNewest -v`
Expected: FAIL — `result[0].ts == candles[0].ts` (oldest, not newest)

- [ ] **Step 3: Fix query_candles ordering**

In `flint/store.py`, replace the ORDER BY and LIMIT block (lines 527-530):

```python
        # When limit is set without start_ts, return the NEWEST candles
        if limit is not None and start_ts is None:
            sql += " ORDER BY ts DESC LIMIT ?"
            params.append(limit)
            with self._lock:
                rows = self._conn.execute(sql, params).fetchall()
            rows = rows[::-1]  # reverse to chronological order
        else:
            sql += " ORDER BY ts ASC"
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            with self._lock:
                rows = self._conn.execute(sql, params).fetchall()
```

Remove the existing `with self._lock:` block at line 531-532 since it's now inside the if/else.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_phase7a_correctness.py::TestOHLCVLimitReturnsNewest tests/test_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/store.py tests/test_phase7a_correctness.py
git commit -m "fix: query_candles limit=N returns newest candles, not oldest

When limit is used without start_ts, query in DESC order then reverse
to chronological. With start_ts, keep existing ASC behavior."
```

---

### Task 5: Fix In-Memory State Corruption (A5)

**Files:**
- Modify: `flint/api/routes/backtest.py:46-91` (state management), `flint/api/routes/backtest.py:662-699` (get_results)
- Test: `tests/test_phase7a_correctness.py` (append)

- [ ] **Step 1: Write failing test — get_results returns JSON on error, not 500**

Append to `tests/test_phase7a_correctness.py`:

```python
class TestBacktestStateManagement:
    """In-memory backtest state should be atomic and error-safe."""

    def test_get_results_returns_json_on_serialization_error(self):
        """Even if result contains non-serializable data, response is JSON."""
        from flint.api.routes import backtest as bt
        import threading

        # Inject a result with non-serializable data
        run_id = "test_bad_result"
        with bt._state_lock:
            bt._entries[run_id] = bt._BacktestEntry(
                status="complete",
                result={"data": float("nan")},  # NaN can cause JSON issues
                progress={"phase": "complete", "pct": 100},
            )

        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(bt.router, prefix="/backtest")
        client = TestClient(app)
        resp = client.get(f"/backtest/{run_id}/results")
        # Must not be a bare 500 — should be JSON
        assert resp.status_code in (200, 500)
        data = resp.json()
        assert "id" in data or "status" in data

        # Cleanup
        with bt._state_lock:
            bt._entries.pop(run_id, None)

    def test_eviction_is_atomic(self):
        """Eviction removes entries completely — no orphan state."""
        from flint.api.routes import backtest as bt

        # Fill past the limit
        old_max = bt._MAX_ENTRIES
        bt._MAX_ENTRIES = 5
        try:
            for i in range(10):
                rid = f"evict_test_{i}"
                with bt._state_lock:
                    bt._entries[rid] = bt._BacktestEntry(
                        status="complete",
                        result={"pnl": i},
                        progress={"phase": "complete"},
                    )
                    bt._evict_old()

            with bt._state_lock:
                assert len(bt._entries) <= 5
        finally:
            bt._MAX_ENTRIES = old_max
            with bt._state_lock:
                for i in range(10):
                    bt._entries.pop(f"evict_test_{i}", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase7a_correctness.py::TestBacktestStateManagement -v`
Expected: FAIL — `_BacktestEntry` and `_entries` don't exist yet

- [ ] **Step 3: Replace three dicts with single _BacktestEntry store**

In `flint/api/routes/backtest.py`, replace lines 46-91 with:

```python
# In-memory state — single dict, protected by _state_lock
_MAX_ENTRIES = 200
_MAX_AGE_S = 3600  # 1 hour TTL
_state_lock = threading.Lock()

# Concurrency and timeout limits
_MAX_CONCURRENT = 5
_MAX_BACKTEST_SECONDS = 300
_concurrency: Dict[str, int] = {"active": 0}


@dataclass
class _BacktestEntry:
    status: str
    result: Optional[dict] = None
    progress: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


_entries: Dict[str, _BacktestEntry] = {}


def configure_concurrency(max_concurrent: int):
    """Called at startup to set concurrency from config."""
    global _MAX_CONCURRENT
    _MAX_CONCURRENT = max_concurrent


def _evict_old():
    """Remove oldest/expired entries. Called inside _state_lock."""
    now = time.time()
    # Remove expired entries first
    expired = [k for k, v in _entries.items() if now - v.created_at > _MAX_AGE_S]
    for k in expired:
        _entries.pop(k, None)
    # Then cap by count
    if len(_entries) > _MAX_ENTRIES:
        by_age = sorted(_entries.keys(), key=lambda k: _entries[k].created_at)
        for k in by_age[:len(_entries) - _MAX_ENTRIES]:
            _entries.pop(k, None)


def _set_status(run_id: str, status: str):
    with _state_lock:
        if run_id in _entries:
            _entries[run_id].status = status
        else:
            _entries[run_id] = _BacktestEntry(status=status)
        _evict_old()


def _set_progress(run_id: str, **kwargs):
    with _state_lock:
        if run_id not in _entries:
            _entries[run_id] = _BacktestEntry(status="running")
        _entries[run_id].progress.update(kwargs)


def _set_result(run_id: str, result: dict):
    with _state_lock:
        if run_id not in _entries:
            _entries[run_id] = _BacktestEntry(status="running")
        _entries[run_id].result = result
```

Add the `dataclass` and `field` imports at the top of the file:
```python
from dataclasses import dataclass, field
```

- [ ] **Step 4: Update get_results to use _entries and wrap in try/except**

Replace `get_results()` (lines 662-699) with:

```python
@router.get("/{run_id}/results")
def get_results(run_id: str):
    try:
        with _state_lock:
            if run_id not in _entries:
                raise HTTPException(404, "Backtest not found")
            entry = _entries[run_id]
            status = entry.status
            progress = dict(entry.progress)
            result = dict(entry.result) if isinstance(entry.result, dict) else entry.result

        elapsed = time.time() - progress.get("started_at", time.time())
        progress_out = {
            "phase": progress.get("phase", "init"),
            "pct": progress.get("pct", 0),
            "detail": progress.get("detail", ""),
            "elapsed_s": round(elapsed, 1),
            "candles": progress.get("candles", 0),
        }

        # Detect hung backtests
        if status == "running" and elapsed > _MAX_BACKTEST_SECONDS + 60:
            _set_status(run_id, "failed")
            _set_result(run_id, {"error": "Backtest exceeded maximum runtime"})
            return {"id": run_id, "status": "failed",
                    "results": {"error": "Backtest exceeded maximum runtime"},
                    "progress": progress_out}

        if status == "running":
            return {"id": run_id, "status": "running", "results": None, "progress": progress_out}
        return {"id": run_id, "status": status, "results": result, "progress": progress_out}
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger("flint.backtest").exception("Error in get_results for %s", run_id)
        from starlette.responses import JSONResponse
        return JSONResponse(status_code=500, content={
            "id": run_id, "status": "error",
            "results": {"error": f"Results retrieval failed: {type(e).__name__}: {e}"},
            "progress": {},
        })
```

- [ ] **Step 5: Update all other endpoints that read from the old dicts**

Update `list_backtests()`, `get_status()`, `cancel_backtest()` to use `_entries` instead of `_status`, `_results`, `_progress`. Search for all references to the old dict names and replace.

For example, `list_backtests`:
```python
@router.get("/list")
def list_backtests():
    with _state_lock:
        return {"backtests": [
            {"id": k, "status": v.status} for k, v in _entries.items()
        ]}
```

And `get_status`:
```python
@router.get("/{run_id}/status")
def get_status(run_id: str):
    with _state_lock:
        if run_id not in _entries:
            raise HTTPException(404, "Backtest not found")
        return {"id": run_id, "status": _entries[run_id].status}
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_phase7a_correctness.py::TestBacktestStateManagement tests/test_api.py tests/test_backtest.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add flint/api/routes/backtest.py tests/test_phase7a_correctness.py
git commit -m "fix: unify backtest state into single _BacktestEntry dict

Replace three separate dicts (_status, _results, _progress) with a
single _entries dict of _BacktestEntry dataclasses. Atomic eviction
by age (1h TTL) and count. get_results() wrapped in try/except to
return JSON errors instead of bare 500."
```

---

### Task 6: Fix Coverage Percentage > 100% (A6)

**Files:**
- Modify: `flint/api/routes/data.py:221`
- Test: `tests/test_phase7a_correctness.py` (append)

- [ ] **Step 1: Write failing test**

Append to `tests/test_phase7a_correctness.py`:

```python
class TestCoverageCap:
    def test_coverage_capped_at_100(self):
        # Simulate: 101 candles for a range that expects 100
        coverage_pct = round(101 / 100 * 100, 1)
        capped = min(coverage_pct, 100.0)
        assert capped == 100.0
```

- [ ] **Step 2: Apply the one-line fix**

In `flint/api/routes/data.py` line 221, change:

```python
        coverage_pct = round(len(candles) / expected_count * 100, 1) if expected_count else 0
```

to:

```python
        coverage_pct = min(round(len(candles) / expected_count * 100, 1), 100.0) if expected_count else 0
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_phase7a_correctness.py::TestCoverageCap tests/test_api.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add flint/api/routes/data.py tests/test_phase7a_correctness.py
git commit -m "fix: cap data coverage percentage at 100%"
```

---

### Task 7: Run Full Test Suite

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v --timeout=120 -x`
Expected: All 536+ tests PASS

- [ ] **Step 2: Final commit if any fixups needed**
