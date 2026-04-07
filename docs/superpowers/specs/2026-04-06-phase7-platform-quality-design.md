# Phase 7: Backtest Platform Quality & Quant UX

> Design spec for 18 fixes across 3 sub-projects, discovered via full quant workflow simulation.
> Date: 2026-04-06

## Context

A full quant desk simulation (data download, exploration, 4 strategy iterations, Optuna optimization, margin/fee stress testing across SOL/BTC/ETH-PERP over 90 days) uncovered 10 bugs and 7 feature gaps. These issues affect every user running the backtest-optimize-validate loop.

## Sub-project Decomposition

| Sub-project | Scope | Items | Files touched |
|---|---|---|---|
| **A: Critical Correctness** | Things that produce wrong results or break | 6 | strategies.py, backtest.py, monte_carlo.py, storage.py, store.py, data.py |
| **B: API Consistency** | Things that confuse users | 6 | data.py, backtest.py |
| **C: Richer Analytics** | Things that hide useful information | 5 | engine.py, optimization.py, margin.py, metrics.py, backtest.py |

Each sub-project is independently shippable and testable.

---

## Sub-project A: Critical Correctness Fixes

### A1. Strategy Name Mismatch

**Bug:** `GET /strategies` lists `funding_mean_reversion`, `momentum_breakout`, `funding_arb`, `basis_trade`, `mev_arb_monitor` but the backtest `builders` dict uses different names or doesn't include them at all. Users see a strategy, click run, get "Unknown strategy."

**Files:** `flint/api/routes/strategies.py`, `flint/api/routes/backtest.py`

**Design:**

Eliminate the hardcoded `_STRATEGIES` list in `strategies.py`. Replace it with a dynamic catalog generated from the `builders` dict in `backtest.py`.

1. Add metadata to each strategy class as class-level attributes:
   - `display_name: str` (e.g., "Funding Harvest")
   - `description: str` (one-line description)
   - `strategy_type: str` ("single_venue", "multi_venue", "monitor")
   - `supported_venues: list` (default `[]`)
   - `needs_funding: bool` (default `False`)
   - `default_markets: list` (default `["SOL-PERP", "BTC-PERP", "ETH-PERP"]`)

   Strategies that don't define these fall back to sensible defaults derived from the class name.

2. Export a function from `backtest.py`:
   ```python
   def get_strategy_catalog() -> list[dict]:
   ```
   This iterates `builders`, instantiates each with `_DEFAULTS`, reads class metadata + `parameters()`, and returns the catalog.

3. `GET /strategies` calls `get_strategy_catalog()` instead of returning `_STRATEGIES`.

4. Add all missing strategies to `builders`:
   - `funding_mean_reversion` (maps to `FundingMeanReversionStrategy`)
   - `momentum_breakout` (maps to `MomentumBreakoutStrategy`)
   - `funding_arb` (maps to `FundingArbStrategy`)
   - `basis_trade` (maps to `BasisTradeStrategy`)
   - `mev_arb_monitor` (maps to `MevArbMonitor`)

5. Add all missing strategies to `_STRATEGIES` list that exist in builders but aren't listed:
   - `grid_trader`, `dual_timeframe`, `vwap_reversion`, `macd_divergence`, `atr_breakout`, `multi_venue_funding`, `rsi_macd_combo`

**Backward compatibility:** Strategy names that work today keep working. New names are additions only.

### A2. Monte Carlo Sharpe Annualization

**Bug:** `flint/analytics/monte_carlo.py` line 95 uses `sqrt(8760)` to annualize per-trade returns. Per-trade returns span variable holding periods, not 1 hour. A strategy with 38 trades over 90 days produces Sharpe CIs of [43, 46] for a point estimate of 6.15.

**File:** `flint/analytics/monte_carlo.py`

**Design:**

Change `run_monte_carlo()` signature to accept the backtest duration:

```python
def run_monte_carlo(
    trade_pnls: List[float],
    initial_capital: float = 10_000.0,
    n_simulations: int = 1000,
    ruin_threshold: float = 0.50,
    period_seconds: int = 0,  # NEW: total backtest duration
) -> MonteCarloResult:
```

Compute annualization factor from actual trade frequency:

```python
if period_seconds > 0 and n_trades > 0:
    period_years = period_seconds / (365.25 * 86400)
    trades_per_year = n_trades / period_years
else:
    trades_per_year = n_trades  # fallback: assume 1 year
annualization = np.sqrt(trades_per_year)
```

Replace line 95:
```python
sharpe = float(np.mean(returns) / std * annualization)
```

Update the caller in `backtest.py` (around line 591) to pass `period_seconds=req.end_ts - req.start_ts`.

### A3. Journal Saves 0% Return

**Bug:** `backtest_runs` table has no `total_return_pct` column. Every run shows 0.00% in the journal.

**File:** `flint/journal/storage.py`

**Design:**

1. Add column to schema (line ~22):
   ```sql
   total_return_pct DOUBLE DEFAULT 0,
   ```
   Insert it after `total_pnl`.

2. In `save_run()`, compute and store:
   ```python
   total_return_pct = (result.total_pnl / initial_capital * 100) if initial_capital > 0 else 0.0
   ```

3. Schema migration: `JournalStorage.__init__()` already creates the table with `IF NOT EXISTS`. Add an `ALTER TABLE` to add the column if it doesn't exist (DuckDB supports `ALTER TABLE ADD COLUMN IF NOT EXISTS` since 0.9):
   ```python
   ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS total_return_pct DOUBLE DEFAULT 0
   ```

4. Update the INSERT statement to include the new column (change from 16 to 17 placeholders).

5. Update `list_runs()` to include `total_return_pct` in the response.

### A4. OHLCV Returns Oldest Candles With `limit`

**Bug:** `query_candles()` uses `ORDER BY ts ASC` always. `limit=5` returns the 5 oldest candles.

**File:** `flint/store.py`, `query_candles()` method

**Design:**

When `limit` is provided and `start_ts` is `None` (user wants "the latest N candles"), use descending order then reverse:

```python
if limit and start_ts is None:
    sql += " ORDER BY ts DESC"
    # ... add LIMIT
    rows = self._conn.execute(sql, params).fetchall()
    rows = rows[::-1]  # reverse to chronological
else:
    sql += " ORDER BY ts ASC"
    # ... add LIMIT if set
```

This preserves the existing behavior when `start_ts` is provided (range queries stay ASC).

### A5. In-Memory State Corruption

**Bug:** Three separate dicts (`_status`, `_results`, `_progress`) with manual eviction. After ~15 backtests, the results endpoint returns 500.

**File:** `flint/api/routes/backtest.py`

**Design:**

1. Replace three dicts with a single store:

   ```python
   @dataclass
   class _BacktestEntry:
       status: str
       result: Optional[dict] = None
       progress: dict = field(default_factory=dict)
       created_at: float = field(default_factory=time.time)
   
   _entries: Dict[str, _BacktestEntry] = {}
   ```

2. Eviction: on every write, drop entries older than 1 hour OR over `_MAX_ENTRIES`, whichever is more aggressive. Evict by `created_at` (oldest first). Single dict means no orphans.

3. Wrap `get_results()` in a top-level try/except:
   ```python
   @router.get("/{run_id}/results")
   def get_results(run_id: str):
       try:
           # ... existing logic using _entries[run_id]
       except KeyError:
           raise HTTPException(404, "Backtest not found")
       except Exception as e:
           return JSONResponse(status_code=500, content={
               "id": run_id, "status": "error",
               "results": {"error": f"Results retrieval failed: {type(e).__name__}: {e}"},
               "progress": {},
           })
   ```

4. Update all `_set_status()`, `_set_result()`, `_set_progress()` helpers to operate on `_entries`.

### A6. Coverage Percentage > 100%

**Bug:** `coverage_pct` can exceed 100% due to integer division rounding.

**File:** `flint/api/routes/data.py`, check endpoint

**Design:**

One-line fix: `coverage_pct = min(coverage_pct, 100.0)` after computation.

---

## Sub-project B: API Consistency & Convenience

### B1. Funding Endpoint Empty Without Timestamps

**Bug:** `GET /data/funding?market=SOL-PERP` (no timestamps) returns empty results. Response key is inconsistent (`by_venue` vs `venues`).

**File:** `flint/api/routes/data.py`, funding endpoint (around line 84)

**Design:**

1. Default timestamps when not provided:
   ```python
   import time as _time
   end_ts = end_ts or int(_time.time())
   start_ts = start_ts or (end_ts - 30 * 86400)  # last 30 days
   ```

2. Normalize response key to `venues` in all code paths.

### B2. Download Endpoint Missing `days` Param

**File:** `flint/api/routes/data.py`, POST /download (line 533)

**Design:**

Accept `days` in the request body:

```python
days = body.get("days")
if days and (not start_ts or not end_ts):
    import time as _time
    end_ts = int(_time.time())
    start_ts = end_ts - int(days) * 86400
```

Place this before the existing validation. When both `days` and explicit timestamps are provided, timestamps win.

Update error message: `"Invalid date range — provide days (e.g. 90) or start_ts + end_ts"`.

### B3. Backtest Status Field Inconsistency

**File:** `flint/api/routes/backtest.py`

**Design:**

Change `_set_progress(run_id, phase="done", ...)` to `phase="complete"` to match the top-level status. Grep for all `phase=` assignments and normalize:
- `"init"`, `"strategy"`, `"data"`, `"backtest"`, `"tearsheet"`, `"complete"` (was `"done"`)
- `"error"`, `"cancelled"` stay as-is

### B4. Data Check Endpoint Single-Market Only

**File:** `flint/api/routes/data.py`, GET /check

**Design:**

Add `markets` as an optional query parameter (comma-separated string). Logic:

```python
market: Optional[str] = Query(None)
markets: Optional[str] = Query(None)

market_list = []
if markets:
    market_list = [m.strip() for m in markets.split(",")]
elif market:
    market_list = [market]
else:
    raise HTTPException(400, "Provide market or markets parameter")
```

When `market_list` has one entry, return the existing single-object response. When multiple, return `{"results": [<per-market objects>]}`.

### B5. Download Response Confusing When Cached

**File:** `flint/api/routes/data.py`, POST /download (around line 581)

**Design:**

In the `if not gaps:` branch, change the response:
- `"cached": existing_count` (was `0`)
- Add `"message": "All candles already cached for this range"`

### B6. CCXT Warning Dedup

**File:** `flint/api/routes/data.py`, in `_download_funding_all_venues()`

**Design:**

Add a module-level set:
```python
_ccxt_warned: set = set()
```

In the funding download loop, only append the CCXT warning if the venue key hasn't been warned yet:
```python
if venue_key not in _ccxt_warned:
    _ccxt_warned.add(venue_key)
    download_warnings.append(f"ccxt/{venue} funding unavailable: ...")
```

---

## Sub-project C: Richer Analytics & Error Surfacing

### C1. Volume Data Missing Warning

**File:** `flint/backtest/engine.py`

**Design:**

After candles are loaded and before the main loop, check:
```python
if all(c.volume == 0 for c in candles[:100]):
    self._strategy_warnings.append(
        "All candles have zero volume (Pyth price-only data). "
        "Volume-dependent indicators (VWAP, volume breakout) will be unreliable."
    )
```

`strategy_warnings` already exists on `BacktestResult` and is surfaced in the tearsheet's `data_quality.warnings`.

### C2. Richer Optimization Results

**File:** `flint/api/routes/optimization.py`

**Design:**

After the Optuna study completes (around line 270), add three fields:

1. **`param_importance`**: 
   ```python
   try:
       from optuna.importance import get_param_importances
       importance = get_param_importances(study)
       result_dict["param_importance"] = {k: round(v, 4) for k, v in importance.items()}
   except Exception:
       result_dict["param_importance"] = None
   ```

2. **`convergence`**:
   Track during the trial callback (already iterating trials):
   ```python
   convergence = []
   best_so_far = float('-inf')
   for t in study.trials:
       if t.value is not None and t.value > best_so_far:
           best_so_far = t.value
       convergence.append([t.number, round(best_so_far, 4)])
   result_dict["convergence"] = convergence
   ```

3. **`best_backtest_metrics`**:
   After finding best params, run one final backtest with those params and capture the full metrics dict. The optimization loop already runs backtests — save the `MetricsSummary` from the best trial instead of discarding it:
   ```python
   result_dict["best_backtest_metrics"] = {
       "total_return_pct": ..., "sharpe_ratio": ..., "sortino_ratio": ...,
       "max_drawdown": ..., "total_trades": ..., "win_rate": ..., "profit_factor": ...,
   }
   ```

4. **Rename** `trials` to `top_trials` in the response dict. Update UI references (grep `ui/src/` for `\.trials` or `["trials"]`).

### C3. Margin Tracking Metrics

**Files:** `flint/execution/margin.py`, `flint/backtest/engine.py`, `flint/analytics/metrics.py`

**Design:**

1. Add a stats accumulator to `MarginEngine`:
   ```python
   @dataclass
   class MarginStats:
       max_leverage: float = 0.0
       total_leverage_bars: float = 0.0  # sum for averaging
       bars_counted: int = 0
       margin_calls: int = 0
       max_utilization_pct: float = 0.0
   ```

2. In `MarginEngine.check_margin()` (called each bar), update stats:
   ```python
   leverage = total_notional / equity if equity > 0 else 0
   self.stats.max_leverage = max(self.stats.max_leverage, leverage)
   self.stats.total_leverage_bars += leverage
   self.stats.bars_counted += 1
   utilization = margin_used / equity * 100 if equity > 0 else 0
   self.stats.max_utilization_pct = max(self.stats.max_utilization_pct, utilization)
   ```

3. After backtest completes, attach margin stats to `BacktestResult`:
   ```python
   result.margin_stats = margin_engine.stats if margin_engine else None
   ```

4. Surface in tearsheet metrics dict:
   ```python
   if result.margin_stats:
       metrics["max_leverage"] = result.margin_stats.max_leverage
       metrics["avg_leverage"] = stats.total_leverage_bars / stats.bars_counted
       metrics["margin_calls"] = stats.margin_calls
       metrics["max_margin_utilization_pct"] = stats.max_utilization_pct
   ```

### C4. Annualized Volatility in Metrics

**File:** `flint/analytics/metrics.py`

**Design:**

Add field to `MetricsSummary`:
```python
annualized_volatility_pct: float = 0.0
```

Compute alongside Sharpe (the std is already calculated):
```python
annualized_vol = float(ret_std * np.sqrt(periods_per_year) * 100)
```

One field, one line of computation.

### C5. Custom Strategy Error Messages

**File:** `flint/api/routes/backtest.py`, `get_results()` function

**Design:**

The existing try/except at line 694 should catch serialization errors but doesn't catch errors that occur before it (e.g., in the `dict()` copy at line 671). Fix:

1. Move the try/except to wrap the entire function body:
   ```python
   @router.get("/{run_id}/results")
   def get_results(run_id: str):
       try:
           with _state_lock:
               ...  # existing lock block
           ...  # existing serialization
       except HTTPException:
           raise  # let 404s pass through
       except Exception as e:
           import logging
           logging.getLogger("flint.backtest").exception("Error in get_results for %s", run_id)
           return JSONResponse(status_code=500, content={
               "id": run_id, "status": "error",
               "results": {"error": f"Results retrieval failed: {type(e).__name__}: {e}"},
               "progress": {},
           })
   ```

2. For strategy runtime errors, enhance the existing except block in `_run()` (line 623):
   ```python
   except Exception as e:
       tb = traceback.format_exc()
       # Extract user code frames (from exec'd strategy)
       user_lines = [l for l in tb.split('\n') if '<string>' in l or 'strategy' in l.lower()]
       error_detail = f"{type(e).__name__}: {e}"
       if user_lines:
           error_detail += f"\n  Strategy traceback: {user_lines[-1].strip()}"
       _set_result(run_id, {"error": error_detail})
   ```

---

## Testing Strategy

Each sub-project gets its own test file:

- `tests/test_phase7a_correctness.py` — Test strategy catalog generation matches builders, MC Sharpe CI contains point estimate, journal stores return_pct, OHLCV limit returns newest, backtest entry eviction is atomic
- `tests/test_phase7b_api_consistency.py` — Test funding defaults, days param, status naming, batch check, cached response, CCXT warning dedup
- `tests/test_phase7c_analytics.py` — Test volume warning, optimization enrichment, margin stats, annualized vol, error JSON format

All tests use mocks (no network calls), consistent with existing test patterns.

## Files Modified (Summary)

| File | Sub-project | Changes |
|---|---|---|
| `flint/api/routes/strategies.py` | A | Replace hardcoded list with dynamic catalog |
| `flint/api/routes/backtest.py` | A, B, C | Add missing builders, unify state dicts, fix status naming, improve error handling |
| `flint/analytics/monte_carlo.py` | A | Fix annualization, add period_seconds param |
| `flint/journal/storage.py` | A | Add total_return_pct column + migration |
| `flint/store.py` | A | Fix query_candles ordering with limit |
| `flint/api/routes/data.py` | A, B | Fix coverage cap, funding defaults, days param, batch check, cached response, CCXT dedup |
| `flint/backtest/engine.py` | C | Volume warning |
| `flint/api/routes/optimization.py` | C | Richer results (importance, convergence, metrics) |
| `flint/execution/margin.py` | C | MarginStats accumulator |
| `flint/analytics/metrics.py` | C | Add annualized_volatility_pct |
