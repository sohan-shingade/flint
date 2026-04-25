# Restructure: Trust → Structure → Depth → Polish → CI → Portfolio

Closes the entire 6-phase restructure plan, the bulk of the deferred
backlog, and all 5 D-6.4-replay slices end-to-end. 41 commits this
session, +8000 / -2400 LOC, 1861 → 2070 tests, ruff hard-fail clean,
vite build clean, 30/30 cargo tests, 91/91 across the full replay
surface (event log + replay primitive + snapshots + engine writer
hooks + REST + MCP + auto-compaction + E2E parity + Rust ledger
parity).

Bumps version 1.3.1 → 1.4.0.

## Highlights by phase

### Phase 1 — Trust & correctness (shipped)
Phase 1 work landed earlier in the session as commits `d7e80e5` →
`35cbf24`. Recap: parity reports, PIT audit, custom data ingest,
sandbox subprocess isolation, 26 PIT_METADATA blocks, force-close
correctness fix in Rust + Python, cross-market terminals.

### Phase 2 — Structural cleanup (shipped, full close)
**D-2.1.b: BacktestContext god class → 7 manager classes**

Every piece of mutable state now lives in one of seven dedicated
owners under `flint/execution/`:

| Manager | Owns |
|---|---|
| `PositionManager` | open + closed-trade dicts |
| `CashManager` | cash, allocator, fees / tx / funding counters |
| `FillRecorder` | recorded fills + diagnostic log |
| `OrderQueue` | pending limit/stop/TP queue + this-bar market queue |
| `FundingLedger` | per-market + per-venue funding history |
| `BorrowLedger` | Jupiter borrow rates + paid-borrow ledger |
| `MarketDataFeed` | cross-market candles + orderbook + OI |

**Caller sites all migrated**: every `_apply_fill`, `apply_funding`,
`check_liquidations`, `process_pending_orders`, `process_market_orders`,
`close_all_positions`, `set_candle`, `account`, `positions`,
`pending_orders` body now routes through the right manager. Legacy
property aliases retained for tests; new code uses managers directly.

**D-4.7-full**: `flint/services/{strategies,backtest,journal,data,paper}.py`
pulls work-doing code out of FastAPI routes into a service layer that
MCP, scripts, and notebooks call directly. Strategy-template registry
has one source of truth (was duplicated across 3 places).

**D-2.2-internal**: every store mutation routes through `_sql_*`
wrappers that hold the lock; routes never touch `store._conn` directly.

### Phase 3 — Depth on wedge (shipped)
**D-3.4-rust + D-3.1-rust**: Rust ports of `TxCostModel` and
`OrderbookFiller` (PyO3, 2.24× and 3.52× speedups, 1e-9 parity tests).

**D-3.3-maker-detection**: `FillResult.is_maker` flag wires through
the Rust fill pipeline; resting-limit fills tag maker; Drift
(-2 bps rebate) and Hyperliquid (1 bp) maker rates verified through
end-to-end PyO3 tests.

**D-3.5-orchestrator**: `flint/risk/portfolio_orchestrator.py:PortfolioMarginEngine`
composes MarginEngine + VenueAllocator + PortfolioRiskEngine into one
pre-trade check facade. `BacktestContext.market_order` consults the
orchestrator; rejection comes back tagged `MARGIN`/`ALLOCATOR`/`PORTFOLIO`
so the warn-line names which engine vetoed.

### Phase 4 — Product polish (shipped)
**D-4.3-websocket end-to-end**:
- Per-session routes `/ws/paper/{id}` and `/ws/live/{id}`
- `ConnectionManager` with monotonic per-channel seq + 500-deep replay
  ring buffer (`?since=<seq>` opt-in) + `ping(channel)` heartbeat
- `useWebSocket<T>` hook with 1→2→5→10→30s reconnect backoff +
  30s heartbeat-stale detection
- PaperTradingEngine emits `{type: tick}` per bar and `{type: trade}`
  per closed trade
- LiveExecutionContext emits `{type: fill}` from `_handle_fill`
  (fire-and-forget via `ensure_future`)
- PaperTrading.tsx + LiveMonitor.tsx pages bound: live equity / trades /
  fills overlay polled state, with `WS LIVE` / `CONNECTING` / `OFFLINE`
  indicator dot

**D-4.2-backoff-full**: `useBackoffPoll<T>` + 3-hook migration shipped.

**D-1.4-ui**: paper reconciliation upload (multipart CSV) + UI panel.

### Phase 5 — CI (shipped)
**D-5.1-ruff**: ruff configured to F-class only (real bugs); 315
auto-fixes + 26 manual; CI flipped from soft to hard fail.

### Phase 6 — Portfolio (foundations shipped)
**D-6.1-unified**: `flint/portfolio/shared_engine.py:SharedCapitalPortfolioEngine`
runs N strategies on **one** shared `BacktestContext`, so cash, fees,
funding, borrow, and the orchestrator's pre-trade margin gauntlet
all see the actual book. Per-strategy `_TaggedContextProxy` tags
order_ids with `strategy_name:`; closed-trade `exit_order_id` lets PnL
flow back to the actual closer (was even-split-by-market in the
foundation slice).

**D-6.4-replay (closed end-to-end, 5/5 slices)**:
- `portfolio_events(session_id, seq, ts, kind, payload)` table +
  `EventLogWriter` (thread-safe, monotonic per-session seq)
- `BookState` + `fold(events, initial_capital, seed=)` + `replay()`
  primitive
- `portfolio_snapshots` + `SnapshotStore` for compaction; replay
  fast-forwards via `latest_before(target_ts)` → `read_after_seq` →
  `fold(seed=snapshot)`
- `BacktestContext._emit(kind, payload)` writer hooks: zero overhead
  when `event_log_writer + event_session_id` not set; otherwise emits
  on every order submit/cancel, fill, funding, liquidation, borrow
- REST: `GET /api/v1/replay/{id}/{events,state,summary}`
- MCP: `replay_summary`, `replay_state`, `list_replay_events`
- UI: `/replay` page with session loader, real timeline slider
  (range bounded to first/last event ts), step controls
  (← PREV / NEXT → / ⏮ START / END ⏭), state cards, positions
  table, color-coded event-tail panel (50 most recent folded events)
- Auto-compaction: BacktestContext's `snapshot_every` ctor kwarg
  (default 10_000) drives `_emit` to fold + persist a fresh
  BookState every N events. Default disabled (no overhead unless
  caller wires a SnapshotStore).
- Rust ledger ports: `flint_core.FundingLedger` + `flint_core.BorrowLedger`
  with PyO3 bindings (`add`/`latest`/`recent`/`by_venue`,
  `record`/`record_payment`/`add_paid`/`cumulative_at`). 7 cargo +
  7 Python↔Rust parity tests pinned to 1e-9.

**Load-bearing parity tests**:
- `tests/test_event_log_engine_hooks.py::TestEndToEndReplayParity` —
  replay over the live-emitted log reproduces `BacktestContext.account.cash`
  byte-for-byte.
- `tests/test_replay_e2e_backtest.py` — same parity over a real
  `MACrossoverStrategy` run with auto-compaction enabled.
- `tests/test_auto_compaction.py::TestSnapshotPreservesReplayCorrectness` —
  snapshot fast-forward replay never produces a divergent state.

## Test sweep

```
2070 passed · 7 skipped · 0 failed
```

(Skipped suites are missing optional deps — `ccxt`, `eth_account`,
`solders` — none are code regressions.)

UI: `133 vitest · vite build clean`. Rust: `30 cargo tests`.

## Files reorganized

New modules under `flint/`:
- `execution/{position_manager,cash_manager,fill_recorder,order_queue,funding_ledger,borrow_ledger,market_data_feed}.py`
- `services/{strategies,backtest,journal,data,paper}.py`
- `risk/portfolio_orchestrator.py`
- `portfolio/{shared_engine,event_log,replay,snapshots}.py`
- `api/routes/replay.py`
- 3 new MCP tools

New UI pages + hooks:
- `ui/src/pages/Replay.tsx`
- `ui/src/hooks/{useWebSocket,useReplay}.ts`

New Rust modules (PyO3-exposed):
- `rust/src/engine/{tx_costs,orderbook_fill,funding_ledger,borrow_ledger}.rs`
- PyO3 classes: `flint_core.TxCostModel`, `flint_core.OrderbookFiller`,
  `flint_core.FundingLedger`, `flint_core.BorrowLedger`
- `supports_tx_costs`, `supports_orderbook_walk`,
  `supports_maker_taker_fees` capability flags flipped to `true`

## Migration notes for users

- `pip install -U flint-trading` (1.4.0)
- No breaking API changes: every old method still works through the
  legacy property aliases. New code should read state via
  `ctx._pm.values()`, `ctx._cm.cash`, `ctx.account` etc. directly.
- New `event_log_writer` + `event_session_id` ctor kwargs on
  `BacktestContext` are opt-in; without them, behavior is identical
  to 1.3.1.
- New `portfolio_risk` ctor kwarg routes book-level checks through
  the new `PortfolioMarginEngine`.

## Follow-on work

- `D-2.1.c` (live context merge) — needs testnet secrets
- `D-2.1.d` (paper context split) — needs deliberate API design pass
- `D-6.5-api` (live deploy two-step) — needs testnet secrets
- `D-6.6-proof` (funding-arb proof notebook) — needs `D-6.5-api`
- `D-6.7-jito` (real Jito bundle integration) — needs `D-6.5-api`

WAVE_STATUS.md tracks per-item state; ROADMAP.md tracks phase-level.
