# Architecture

How Flint's subsystems fit together. This is an explanation, not a reference — for exact file / line details see [python-sdk.md](../reference/python-sdk.md) and for API schemas see [rest-api.md](../reference/rest-api.md).

## One picture

```
External APIs  ──►  Providers  ──►  FlintStore (DuckDB)  ──►  Engine (Backtest / Paper / Live)
                                                               │
                                                               ▼
                                                         Strategy.on_candle()
                                                               │  orders
                                                               ▼
                                                         RiskManager
                                                               │
                                                               ▼
                                                         FillPipeline
                                                               │
                                                               ▼
                                                     BacktestResult / live_fills
```

Everything revolves around two abstractions:

1. **`FlintStore`** — single shared DuckDB connection. All market data lands here; all consumers read from here. Thread-safe.
2. **`ExecutionContext`** — uniform order API. Same strategy code runs in backtest, paper, and live because all three implement the same interface.

## Layering

| Layer | Concern | Key modules |
|---|---|---|
| **Providers** | Fetch external data | `flint/providers/` — 26 classes, 3 WebSocket feeds |
| **Store** | Persist everything locally | `flint/store.py` — 12+ DuckDB tables |
| **Strategy** | Describe trading logic | `flint/strategy/` — ABC + 20 templates |
| **Execution** | Turn orders into fills | `flint/execution/` — contexts, fill pipeline, margin, capital |
| **Engines** | Drive bars through strategy + execution | `flint/backtest/engine.py`, `flint/paper/engine.py`, `live_*` |
| **Risk** | Block or stop unsafe orders | `flint/risk/` — guards + EquityMonitor |
| **Analytics** | Score results | `flint/analytics/` — tearsheet, Monte Carlo, correlation |
| **Optimization** | Search parameter space | `flint/optimization/` — Optuna, walk-forward |
| **Surface** | Talk to humans/AIs | `flint/api/`, `flint/cli.py`, `flint/mcp_server.py` |

## The two abstractions that matter

### `FlintStore`

A local DuckDB file behind a threading lock. Always shared — never open a second connection. This is the single source of truth for candles, funding rates, orderbook snapshots, open interest, journal runs, paper/live sessions, etc.

Tables are documented in [reference/data-providers.md §Storage](../reference/data-providers.md#storage).

### `ExecutionContext`

An abstract base class with a uniform API (`market_order`, `limit_order`, `stop_order`, state accessors, data queries). The strategy never knows whether it's running on historical candles, a paper broker, or driftpy — it calls the same methods either way. This is why "deploy to paper" is one click in the UI, and why a strategy that works in backtest actually runs against Drift without code changes.

See [concepts/execution-contexts.md](execution-contexts.md) for the semantic differences between the three.

## Data flow

Per bar, in any engine:

1. A new candle arrives (from DuckDB in backtest; from WebSocket in paper/live).
2. The engine calls `strategy.on_candle(candle, history, ctx)`.
3. The strategy returns `Signal.BUY/SELL/HOLD` or calls `ctx.market_order(...)`.
4. `RiskManager` checks each order against its guard chain. Rejected orders never reach fills.
5. `FillPipeline` converts orders to fills: latency → impact → partial fill.
6. `MarginEngine` updates margin, checks for liquidation.
7. Equity snapshot goes to the equity curve. `EquityMonitor` checks kill switch.
8. (Backtest) loop to next bar. (Paper/live) wait for next WebSocket tick.

## Rust engine

If `flint_core` is installed, backtests dispatch to a Rust implementation with identical semantics and 10–50× speedup. Parity is verified by `tests/test_rust_parity_benchmark.py`. The Python engine remains the fallback.

```bash
pip install maturin
cd rust && maturin develop
```

The Rust code mirrors the Python layout: `runner.rs` orchestrates, `engine/fills.rs` runs the pipeline, `engine/venue_fills.rs` covers per-venue logic, `engine/margin.rs` handles liquidations.

## Why it's built this way

- **Local-first.** DuckDB on disk, FastAPI on loopback. Your data, your machine. No cloud lock-in.
- **Same code, three engines.** Backtest ↔ paper ↔ live symmetry is the core feature. Everything else composes on top.
- **Per-venue honesty.** Drift's vAMM and Hyperliquid's CLOB behave differently. Flint models each natively rather than through a lowest-common-denominator adapter. See [fill-pipeline.md](fill-pipeline.md).
- **Explicit safety.** Risk lives as a separate layer, enforced in all three engines. The kill switch is the same code path in paper and live.

## Not in this doc

- Fill model specifics → [fill-pipeline.md](fill-pipeline.md)
- Margin + capital internals → [margin-capital.md](margin-capital.md)
- Regime system → [regimes.md](regimes.md)
- Risk guard chain → [risk-model.md](risk-model.md)
- Backtest vs live divergence → [backtests-vs-reality.md](backtests-vs-reality.md)
