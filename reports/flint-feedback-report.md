# Flint Platform Feedback Report

## Overview

This report documents the experience of using Flint v0.3 (dev branch) to develop a quantitative trading strategy for SOL-PERP. The process involved data exploration, strategy prototyping, parameter optimization, and backtesting.

## What Worked Well

### 1. Data Pipeline (9/10)
Flint's data pipeline is the standout feature. Downloading months of hourly candles from Drift's API was a single function call (`DriftCandleProvider.fetch_candles()`). Cross-market data (SOL, BTC, ETH) loaded quickly and the format was consistent. The `store.query_candles()` interface made it easy to slice data by date range.

### 2. Cross-Market Strategy Support (8/10)
The ability to pass `Dict[str, List[Candle]]` to `BacktestEngine.run()` and access other markets via `ctx.get_candles("BTC-PERP")` inside strategies was excellent. This is what makes multi-asset strategies possible and is a major differentiator from simpler backtesting tools.

### 3. Strategy API (8/10)
The v2 execution context API (`ctx.market_order()`, `ctx.stop_order()`, etc.) is well-designed. Impact-aware pre-trade checks via `ctx.get_impact_price()` helped avoid bad entries. The `parameters()` classmethod for Optuna integration is clean.

### 4. Fill Pipeline (7/10)
The composable fill pipeline (impact + latency + partial fill) adds realism that most backtesting tools lack. The sqrt participation model is a good approximation when orderbook data isn't available.

## Bugs Found During Development

### Bug 1: In-Memory Store Doesn't Persist (Major UX Issue)
`FlintStore()` creates an in-memory DuckDB by default. Downloaded candles disappear when the script exits. Every new script invocation requires re-downloading data. This is extremely wasteful when iterating on strategies.

**Impact:** Had to combine data download + analysis + backtesting into single scripts, or re-fetch data every time.

**Suggestion:** Default to a file-based DB (`~/.flint/data.duckdb`) and only use in-memory for tests.

### Bug 2: Optimizer Doesn't Support Multi-Market (Blocker)
`StrategyOptimizer` expects `List[Candle]` but multi-market strategies need `Dict[str, List[Candle]]`. Passing a dict causes `TypeError: unhashable type: 'slice'` at `self.candles[:split]`.

**Impact:** Had to write a manual random-search optimization loop instead of using the built-in Optuna optimizer. This meant no walk-forward validation, no pruning, and slower convergence.

**Suggestion:** Make `StrategyOptimizer.candles` accept `Union[List[Candle], Dict[str, List[Candle]]]` and handle the dict case in `optimize()` and `walk_forward()`.

### Bug 3: Impact Coefficients Were ~10x Too High (Fixed)
The default sqrt model coefficients (k=0.05-0.1) charged ~200bps on normal-sized orders because candle volume is a sample of total market volume. Fixed by reducing to k=0.002-0.01.

**Impact:** All strategies appeared unprofitable until this was diagnosed and fixed.

### Bug 4: Latency Model Double-Counted Delay (Fixed)
Orders placed during bar N had `order.ts == candle.ts`, so any latency > 0 caused them to miss the current bar entirely. On hourly candles, 1 second of simulated latency became 1 hour of actual delay. Fixed by using bar close time for eligibility.

**Impact:** Strategies lost trades to phantom delays.

### Bug 5: Default Venue Config Had 100% Margin (Fixed)
The "default" venue had `initial_margin=1.0` and `max_leverage=1`, meaning margin tracking effectively disabled trading after the first position. Fixed to Drift-like defaults (10%/10x).

**Impact:** All strategies appeared broken with margin tracking enabled.

### Bug 6: Stop Orders Filled at Trigger Price (Fixed)
Stop-loss orders filled at the exact trigger price regardless of candle gap. In reality, stops execute as market orders and should fill at the current market price (possibly worse than trigger). Fixed to use min/max of trigger vs close.

### Bug 7: `ctx.get_impact_price()` Only Used Orderbook (Fixed)
The pre-trade impact check returned `None` when no orderbook data existed, silently skipping the check. But the fill pipeline used the sqrt model and charged real impact. Fixed to fall through to the same impact model.

## Feature Requests

### 1. Multi-Market Optimizer (Priority: High)
The optimizer needs to accept `Dict[str, List[Candle]]` for multi-market strategies. Currently must write custom optimization loops.

### 2. Persistent Store by Default (Priority: High)
Default to file-based DuckDB. The in-memory store is fine for tests but terrible for iterative research.

### 3. Strategy Performance Analytics (Priority: Medium)
After backtesting, would love to see:
- Trade-by-trade breakdown with entry/exit z-scores
- Rolling Sharpe ratio chart
- Regime-specific performance (e.g., high-vol vs low-vol periods)
- Monte Carlo simulation of the strategy's equity curve

Flint has `analytics/monte_carlo.py` and `analytics/tearsheet.py` but these aren't easily accessible from the strategy development workflow.

### 4. Walk-Forward Out of the Box (Priority: Medium)
Walk-forward validation for multi-market strategies. The current `walk_forward()` method on `StrategyOptimizer` only works with single-market data.

### 5. Funding Rate Integration in Backtest (Priority: Medium)
The backtest engine loads funding rates but strategies need `ctx.get_funding_rate()` to access them. For funding-based strategies, it would help if the engine automatically showed whether funding data was available for the backtest period (a "data availability" check before running).

### 6. Strategy Template for Beta-Hedged Approaches (Priority: Low)
Add a template that demonstrates the beta-hedged pattern. This is the most sophisticated strategy type that Flint can support, and there's no template for it.

## Overall Assessment

**Flint is an impressive tool for Solana-focused quant research.** The data pipeline, multi-market backtesting, and realistic fill simulation are genuinely useful. The v2 execution API is well-designed and the strategy loader's AST validation is smart.

The main friction points are:
1. **Bugs in the execution engine** (5 bugs found, all now fixed) that made results unreliable until diagnosed
2. **Missing multi-market support in the optimizer** forced manual optimization
3. **In-memory store** made iterative development painful

With these issues addressed, Flint would be a compelling platform for serious Solana perp strategy development. The beta-hedged strategy achieving Sharpe 2.90 with realistic execution costs demonstrates the platform's potential.

**Rating: 7.5/10** — Powerful when it works, but needs polish on edge cases and developer ergonomics.
