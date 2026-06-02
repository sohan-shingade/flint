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

An abstract base class with a uniform API (`market_order`, `limit_order`, `stop_order`, state accessors, data queries). The strategy never knows whether it's running on historical candles, a paper broker, or a live venue connector — it calls the same methods either way. This is why "deploy to paper" is one click in the UI, and why a strategy that works in backtest actually runs against Hyperliquid without code changes.

See [concepts/execution-contexts.md](execution-contexts.md) for the semantic differences between the three.

#### `BacktestContext` composes seven managers

The simulation context isn't a god class — it's a thin orchestrator that delegates state to seven dedicated owners in `flint/execution/`:

| Manager | Owns |
|---|---|
| `PositionManager` | Open + closed-trade dicts |
| `CashManager` | Cash, optional `VenueAllocator`, running counters (fees / tx cost / funding) |
| `FillRecorder` | Recorded-fill list + diagnostic log messages |
| `OrderQueue` | Pending limit/stop/TP queue + this-bar market queue |
| `FundingLedger` | Per-market and per-venue funding history |
| `BorrowLedger` | Jupiter Perps borrow rates + paid-borrow ledger |
| `MarketDataFeed` | Cross-market candle history + orderbook snapshots + OI |

Pre-trade checks flow through one facade — `flint/risk/portfolio_orchestrator.py:PortfolioMarginEngine` — that composes the venue-level `MarginEngine`, the per-venue `VenueAllocator`, and the book-level `PortfolioRiskEngine`. `BacktestContext.market_order` consults the orchestrator once per order; rejection comes back tagged with the source component (`MARGIN`/`ALLOCATOR`/`PORTFOLIO`) so the warn-line names which engine vetoed.

#### Event sourcing + replay

When `BacktestContext` is built with `event_log_writer + event_session_id`, every order submit, fill, funding payment, liquidation, and Jupiter borrow cost is appended to a `portfolio_events` DuckDB table (`flint/portfolio/event_log.py`). Two read primitives sit on top:

- `flint/portfolio/replay.py:replay(store, session_id, target_ts, initial_capital) → BookState` folds the event stream into the book's exact state at any point in the past. Fast-forwards via `flint/portfolio/snapshots.py:SnapshotStore` when a snapshot exists at-or-before `target_ts`.
- REST: `GET /api/v1/replay/{id}/{events,state,summary}`. MCP: `replay_summary`, `replay_state`, `list_replay_events`. UI: the `/replay` page in the browser.

Replay reproduces final state byte-for-byte against the live `BacktestContext.account.cash` — pinned by `tests/test_event_log_engine_hooks.py::TestEndToEndReplayParity`.

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
- **Per-venue honesty.** A DEX CLOB (Hyperliquid) and a CEX orderbook behave differently. Flint models each natively rather than through a lowest-common-denominator adapter. See [fill-pipeline.md](fill-pipeline.md).
- **Explicit safety.** Risk lives as a separate layer, enforced in all three engines. The kill switch is the same code path in paper and live.

## Not in this doc

- Fill model specifics → [fill-pipeline.md](fill-pipeline.md)
- Margin + capital internals → [margin-capital.md](margin-capital.md)
- Regime system → [regimes.md](regimes.md)
- Risk guard chain → [risk-model.md](risk-model.md)
- Backtest vs live divergence → [backtests-vs-reality.md](backtests-vs-reality.md)
