# Flint Product Feedback Report

**Tester**: AI-driven research workflow
**Date**: March 27, 2026
**Session**: Full research cycle — DB nuke → data download → market research → strategy development → optimization → walk-forward validation → reporting
**Duration**: ~3 hours of active work
**Verdict**: Flint is a powerful platform with strong fundamentals. The data pipeline, backtest engine, and optimization are excellent. Several reliability and UX issues need attention.

---

## What Worked Well

### 1. Data Pipeline (A)
- **Drift API download** was fast and reliable — 2017 hourly candles per market downloaded in seconds
- **Multi-venue funding data** automatically fetched alongside candles (8063 records per market from 4 venues)
- **Funding rate normalization** to hourly intervals across venues (Bitget, dYdX, Gate.io, Hyperliquid) worked seamlessly
- The `POST /api/v1/data/download` endpoint is well-designed — auto-detects gaps, handles incremental downloads

### 2. Backtest Engine (A-)
- **v2 strategy API** is excellent — `ctx.market_order()`, `ctx.position()`, `ctx.get_funding_rates()` are intuitive
- **Strategy loader** with AST validation is a smart security model — blocks dangerous imports but allows all needed math/stats packages
- **Metrics computation** is comprehensive — Sharpe, Sortino, profit factor, drawdown duration, holding period
- **Indicators module** saved significant time — `rsi()`, `ema()`, `bollinger()`, `atr()` just work

### 3. Optuna Optimizer (A-)
- Integration with the strategy's `parameters()` classmethod is elegant
- Found Sharpe 3.4-5.4 params across markets in ~50 trials
- Results returned with best params and metric values

### 4. CLI Experience (B+)
- `flint serve --dev` startup is clean
- `flint data download` works well for single-market downloads
- Rich console output is pleasant

---

## Bugs Found

### Bug 1: Default params applied to custom code strategies (MEDIUM)
**Location**: `flint/api/routes/backtest.py:262`
**Issue**: When submitting a backtest with `code` (inline strategy) but no explicit `params`, the `strategy` field defaults to `"ma_crossover"`, and `_DEFAULTS["ma_crossover"]` params get applied to the custom strategy constructor.

```python
# Line 262: params = req.params or _DEFAULTS.get(req.strategy, {})
# When code is provided, req.strategy defaults to "ma_crossover"
# This causes: AdaptiveAlphaStrategy(**{"fast_period": 10, "slow_period": 30}) → TypeError
```

**Fix**: When `code` is provided, don't apply strategy-name defaults:
```python
params = req.params or ({} if req.code else _DEFAULTS.get(req.strategy, {}))
```

**Workaround**: Always pass `"strategy": "custom"` when using inline code.

### Bug 2: Results endpoint returns 500 for some backtest IDs (HIGH)
**Issue**: `GET /api/v1/backtest/{id}/results` returns HTTP 500 (empty body "Internal Server Error") instead of valid JSON for certain backtest runs. This happened consistently for the same market/window combination (SOL-PERP Jan 28-Feb 25) even after server restart.

**Impact**: Walk-forward analysis lost data points. Scripted workflows crash on JSON parse errors.

**Expected behavior**: Should return `{"status": "running"}`, `{"status": "failed", "error": "..."}`, or `{"status": "complete", "results": {...}}` — never a raw 500.

**Likely cause**: The in-memory result store (`_results` dict) may lose entries after the server restarts (dev mode with file watcher), or the backtest thread crashes without updating the status dict.

### Bug 3: Results endpoint returns empty/non-JSON while backtest is still running (MEDIUM)
**Issue**: Polling results immediately after submission sometimes returns empty HTTP body (no JSON) instead of `{"status": "running", ...}`.

**Observed pattern**: Happens when the server is under load (multiple concurrent backtests). The endpoint seems to fail silently rather than returning the "running" status.

---

## Feature Requests

### 1. Multi-Market Parameter Sweep (HIGH PRIORITY)
**Need**: Test one parameter set across multiple markets simultaneously and get aggregated results (min Sharpe, avg Sharpe, market-by-market breakdown).

**Current workflow**: Submit 3 separate backtests, poll each, manually aggregate. This required writing a 60-line Python script. For 108 param combinations × 3 markets = 324 backtests, this took ~30 minutes.

**Proposed API**:
```json
POST /api/v1/backtest/run
{
    "code": "...",
    "markets": ["SOL-PERP", "BTC-PERP", "ETH-PERP"],
    "params": {...},
    "aggregate": true  // returns min/avg/max Sharpe across markets
}
```

### 2. Walk-Forward Validation Endpoint (HIGH PRIORITY)
**Need**: Split data into N windows and run strategy on each, returning per-window metrics.

**Proposed API**:
```json
POST /api/v1/backtest/walk-forward
{
    "code": "...",
    "market": "BTC-PERP",
    "params": {...},
    "n_windows": 3,       // or "window_days": 28
    "start_ts": ...,
    "end_ts": ...
}
```

**Returns**: Array of per-window results + aggregate statistics. This is the #1 tool for detecting overfitting.

### 3. Batch Optimizer Across Markets (MEDIUM)
**Need**: Optimize strategy params to maximize minimum Sharpe across multiple markets simultaneously.

**Current workflow**: Optimize per-market, then manually find the intersection. This produces per-market optimal params that may not generalize.

**Proposed**: `optimize/run` with `markets: ["SOL-PERP", "BTC-PERP"]` and `objective: "min_sharpe_across_markets"`.

### 4. CLI `data download` Should Support Multiple Markets (LOW)
**Current**: `flint data download -m SOL-PERP` downloads one market at a time.
**Wanted**: `flint data download -m SOL-PERP -m BTC-PERP -m ETH-PERP` or `--all-perps`.

### 5. Data Freshness Endpoint Returns Empty (LOW)
**Issue**: `GET /api/v1/data/freshness` returned `{"freshness": []}` even after downloading data.
**Expected**: Should list each market's data range and last update time.

### 6. Optimization `best_value` Field Missing from Results (LOW)
**Issue**: `GET /api/v1/optimize/{id}/results` returns `best_value: 3.4221` correctly but `best_sharpe` is empty/missing. The field naming is inconsistent — sometimes `best_value`, sometimes `best_sharpe`.

### 7. Concurrent Backtest Limit Could Be Configurable (LOW)
**Current**: Hard limit of 5 concurrent backtests. During grid search, this requires careful batching.
**Wanted**: `flint.yaml` config: `api.max_concurrent_backtests: 10`.

---

## UX Observations

### API Design
- The separation of `POST /backtest/run` (submit) and `GET /backtest/{id}/results` (poll) is good for async operation
- **Missing**: No way to list all running/completed backtests (`GET /backtest/list`)
- **Missing**: No way to cancel a running backtest
- The `params` handling for inline code is confusing — needs documentation that `"strategy": "custom"` is needed to avoid default params

### Strategy Development Workflow
- The indicators module (`flint.indicators`) is excellent — covers all common indicators with clean API
- **Missing**: No built-in way to compute rolling statistics on funding rates within strategies (had to do manual averaging)
- **Missing**: No way to access cross-market data within a strategy running on a single market (e.g., "check BTC trend while trading SOL")
- The v2 API (`ctx.market_order()` etc.) is intuitive and well-documented in the strategy templates

### Data Quality
- Hourly candles from Drift API were complete with no gaps
- Funding rates from 4 venues were well-normalized to hourly
- Bitget funding rates appear to be near-zero across the entire period — possible normalization issue or genuine behavior
- **Missing**: No way to check data quality from the API (gap detection, stale data alerts)

---

## Performance Observations

- Individual backtest execution: ~5-10 seconds for 2017 hourly candles — acceptable
- Optimization (50 trials): ~60-90 seconds — fast enough for interactive use
- Server memory: Appears to accumulate state from many backtests without cleanup. After ~100+ backtests in one session, saw occasional 500 errors.
- **Suggestion**: Add periodic cleanup of old backtest results (keep last 50, expire after 1 hour)

---

## Summary of Priorities

| Priority | Item | Type |
|----------|------|------|
| **P0** | Fix 500 errors on results endpoint | Bug |
| **P0** | Fix default params applied to custom code | Bug |
| **P1** | Multi-market backtest aggregation | Feature |
| **P1** | Walk-forward validation endpoint | Feature |
| **P1** | Fix results endpoint returning non-JSON while running | Bug |
| **P2** | Cross-market optimizer | Feature |
| **P2** | Data freshness endpoint | Bug |
| **P2** | Backtest list/cancel endpoints | Feature |
| **P3** | Multi-market CLI download | Feature |
| **P3** | Configurable concurrency limit | Feature |

---

## Overall Assessment

Flint is **production-quality for individual backtesting and strategy development**. The data pipeline, strategy API, and indicator library are well-designed and intuitive. The main gap is in **multi-market workflows** — parameter optimization, walk-forward validation, and aggregated reporting across markets require significant manual scripting.

The bugs found (500 errors, default params leak) are real but have workarounds. The feature requests (walk-forward, multi-market optimization) would transform Flint from "great single-market backtester" to "complete research platform."

**Score: 7.5/10** — Excellent foundation, needs reliability hardening and multi-market workflow support.
