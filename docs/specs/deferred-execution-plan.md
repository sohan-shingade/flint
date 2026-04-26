# Deferred Execution Plan

Sequenced delivery plan for the 16 items still in [`DEFERRED.md`](../../DEFERRED.md).
Optimized for **maximum unlocks per week** with realistic parallelization.

This file is the living roadmap; each item below has a corresponding row
in `DEFERRED.md`. When an item ships, mark it done in both files.

Updated: 2026-04-24.

---

## Executive plan summary

| Wave | Calendar | Items shipped | Net change |
|---|---|---|---|
| **1 — Unblock + parallel quick wins** | weeks 1–3 | 6 items | ExecutionContext refactor lands · Rust fill pipeline lands · UI polish complete |
| **2 — Architecture follow-on** | weeks 4–5 | 5 items | Live-context merged · Margin orchestrator · Rust orderbook + maker detection |
| **3 — Live deploy gate** | weeks 6–9 | 3 items | `/api/v1/live/start` ships · WebSocket streams · Proof notebook |
| **4 — Solana + portfolio specialization** | weeks 10–14 | 2 items | Jito bundles · Shared-capital portfolio backtest |
| **5 — Capstone** | weeks 14–17 | 1 item | Event sourcing + time-travel replay |

**Sequential single-engineer:** ~20 weeks of focused work (5 months).
**3-engineer parallel:** ~12 weeks calendar (3 months).
**Critical path:** D-2.1.b → D-2.1.c → D-6.5-api → D-6.7-jito (12 weeks).

---

## Dependency graph

```
                  ┌─────────────────────────────────────────┐
                  │           D-2.1.b (god class)          │  ← biggest unlock (4 children)
                  │              L, 2 weeks                 │
                  └────┬────┬────┬─────────────────────────┘
                       │    │    │
                       ▼    ▼    ▼
                  D-2.1.c  D-2.1.d  D-3.5-orchestrator
                  (live)   (paper)   (margin facade)
                   L,1w    M,1w      L,1w
                       │           │
                       ▼           ▼
                  D-6.5-api    D-6.1-unified
                  (live deploy)  (portfolio bt)
                   XL,3w           XL,3w
                       │
                  ┌────┴────┐
                  ▼         ▼
              D-6.6-proof   D-6.7-jito
              M,1w           L,2w


  D-3.4-rust ──┬── D-3.1-rust (M,3d)
  (fill pipe)  └── D-3.3-maker  (M,2d)
   L, 1w


  Independent (no deps):
    D-1.4-ui (M,3d)              D-4.7-full (M,3d)
    D-4.2-backoff-full (M,3d)    D-5.1-ruff (S,1d)
    D-4.3-websocket (L,1w)       D-6.4-replay (XL,2-3w)
```

---

## Wave 1 — Unblock + parallel quick wins (weeks 1–3)

**Goal:** land the two biggest dependency unlocks (D-2.1.b, D-3.4-rust) and
clear all UI polish in parallel. After this wave, every subsequent
architectural item has its prerequisites met.

### Track A — Architecture (engineer-1, 2 weeks)

#### W1.A.1 — D-2.1.b: Break up `BacktestContext` god class · L · 2w

Critical path. Highest unlock count.

- [ ] **Step 1:** Extract `flint/execution/position_manager.py:PositionManager`
  - Move `_positions` dict, `add_position`, `close_position`, PnL methods
  - Pass into `BacktestContext.__init__` instead of owning state
  - Run `pytest tests/test_backtest.py tests/test_paper.py` between each move
- [ ] **Step 2:** Extract `flint/execution/order_queue.py:OrderQueue`
  - Move pending-order list, stop/limit processing, cancel logic
- [ ] **Step 3:** Extract `flint/execution/funding_tracker.py:FundingTracker`
  - Per-position funding accrual + payment scheduling
- [ ] **Step 4:** Extract `flint/execution/borrow_tracker.py:BorrowTracker`
  - Jupiter borrow cost integration
- [ ] **Step 5:** Extract `flint/execution/market_data_feed.py:MarketDataFeed`
  - Cross-market candle history, orderbook + OI snapshots
- [ ] **Step 6:** Reduce `BacktestContext` to <300 LOC composing the above
- [ ] **Step 7:** Update ~40 caller sites in `flint/backtest/engine.py`,
  `flint/portfolio/engine.py`, `flint/analytics/tearsheet.py`, tests

**Acceptance:** existing 1861-test sweep stays green. New
`tests/test_components_isolation.py` asserts each component instantiable +
testable without `BacktestContext`. `BacktestContext` LOC count <300.

**Files modified:** `flint/execution/{position_manager,order_queue,funding_tracker,borrow_tracker,market_data_feed,backtest_context}.py` (5 new + 1 trimmed) · 40+ caller sites

### Track B — Rust pipeline (engineer-2, 1 week)

#### W1.B.1 — D-3.4-rust: `LatencyStage` + `PartialFillStage` + Rust `FillPipeline` · L · 1w

Unlocks D-3.1 + D-3.3.

- [ ] **Step 1:** `rust/src/engine/latency.rs` — port `flint/execution/latency.py`
  with seeded ChaCha8Rng. `cargo test`.
- [ ] **Step 2:** `rust/src/engine/partial_fill.rs` — port partial-fill
  splitting across bars when `size > max_bar_volume × participation`.
- [ ] **Step 3:** `rust/src/engine/fill_pipeline.rs` — composes
  `latency → impact → partial` identical to Python.
- [ ] **Step 4:** Extend `runner.rs` to dispatch into `FillPipeline` instead
  of the flat fill model when caller selects pipeline mode.
- [ ] **Step 5:** Extend `lib.rs` PyO3 wrapper to accept pipeline config
  from Python (latency seed, jitter, impact coefficient, participation rate).
- [ ] **Step 6:** Flip `flint_core.capabilities()` flags:
  `supports_partial_fills=True`, `supports_latency_stage=True`.
- [ ] **Step 7:** Extend `tests/test_rust_python_parity.py` with
  partial-fill + latency multi-bar scenarios; assert byte-identical.

**Acceptance:** `cargo test --release` green · all parity tests green ·
capability matrix lists new flags · benchmark shows Rust-on-pipeline
≥ 10× Python on a 1k-bar fixture.

### Track C — UI polish (engineer-3, ~1.5 weeks parallel)

#### W1.C.1 — D-5.1-ruff-fixes: Hard-fail ruff · S · 1d

- [ ] `ruff check --fix flint/ tests/ scripts/`
- [ ] Manual sweep for what auto-fix can't handle
- [ ] Flip `.github/workflows/ci.yml` ruff steps from `|| echo` to hard-fail
- [ ] One PR; merge fast before other branches drift

#### W1.C.2 — D-4.7-full: MCP in-process service layer · M · 3d

- [ ] Create `flint/services/__init__.py`
- [ ] Extract `flint/services/backtest.py:run_backtest(config) → BacktestResult`
- [ ] Extract `flint/services/paper.py:{start,stop,status,list}`
- [ ] Extract `flint/services/data.py:{ohlcv,funding,download}`
- [ ] Extract `flint/services/journal.py:{list_runs,get_run,compare}`
- [ ] Routes in `flint/api/routes/*.py` become thin adapters
- [ ] MCP tools in `flint/mcp_server.py` import services directly
- [ ] Add `tests/test_mcp_standalone.py` — proves `python -m flint.mcp_server`
  works without `flint serve` running

**Acceptance:** grep `httpx|requests` in `flint/mcp_server.py` returns 0 ·
new test passes · backtest end-to-end identical via REST and MCP.

#### W1.C.3 — D-1.4-ui: Reconciliation UI panel + POST variant · M · 3d

- [ ] `POST /api/v1/paper/{id}/reconciliation` accepts multipart CSV
- [ ] Server side calls `scripts/reconcile_fills.reconcile()` on the upload
- [ ] PaperTrading.tsx adds a "Reconcile" button + `<input type="file">`
- [ ] Results table: matched/orphan counts, p50/p95/p99 bps deltas, ts deltas
- [ ] Loading state + schema-error toast + empty-log toast
- [ ] `tests/test_reconciliation_endpoint.py` — fixture CSV roundtrips

**Acceptance:** UI smoke (Playwright): upload sample fills, see filled
table; Cypress-style happy + error paths · PaperTrading still passes
existing tests.

#### W1.C.4 — D-4.2-backoff-full: Per-hook exponential backoff · M · 3d

- [ ] Generic `useBackoffPoll<T>(fetcher, opts)` hook with
  AbortController + 1s→2s→5s→10s→30s schedule
- [ ] Migrate `useBacktest`, `useOptimize`, `usePaperTrading`,
  `useLiveMonitor`, `useFillAnalysis` to use it
- [ ] Each hook surfaces `errorCount`, `lastError`, `nextRetryIn`
- [ ] Per-hook `<ErrorBanner feature="paper" />` when offline
- [ ] Confirm hooks abort in-flight on unmount

**Acceptance:** kill server mid-session, banners appear within 10s,
retry button works · no double-poll on remount · console clean.

---

## Wave 2 — Architecture follow-on (weeks 4–5)

**Goal:** ride D-2.1.b's unlock to land 4 dependent items; finish Rust
parity by porting orderbook + maker detection.

### Track A (engineer-1, 2 weeks)

#### W2.A.1 — D-2.1.d: Separate `PaperContext` from PaperBroker · M · 1w

**Prereq:** D-2.1.b ✓

- [ ] New `flint/execution/paper_context.py:PaperContext` implements `ExecutionContext`
- [ ] Composes `PositionManager` + `FundingTracker` + `MarketDataFeed`
  from D-2.1.b
- [ ] Owns `PaperBroker` (broker = fills only; context = state)
- [ ] `flint/paper/engine.py` instantiates `PaperContext` instead of
  `LiveExecutionContext(paper_mode=True)`
- [ ] `flint/backtest/parity.py` swaps in `PaperContext` for parity testing

**Acceptance:** `tests/test_paper.py` + `tests/test_parity.py` green ·
parity divergence still <2% on the canonical fixture.

#### W2.A.2 — D-3.5-orchestrator: `PortfolioMarginEngine` in `BacktestContext` · L · 1w

**Prereq:** D-2.1.b ✓

- [ ] New `flint/risk/portfolio_orchestrator.py:PortfolioMarginEngine`
- [ ] Composes existing `MarginEngine` + `VenueAllocator` + `Transfer` +
  Phase 6 `PortfolioRiskEngine`
- [ ] `BacktestContext.market_order` consults orchestrator pre-trade;
  rejects on margin / risk violations
- [ ] Per-venue `LiquidationEvent` populated into `BacktestResult.liquidations`
- [ ] `tests/test_portfolio_margin_e2e.py` — funding-arb on Drift+HL
  with leveraged positions, per-venue funding accrues independently,
  per-venue liquidation fires at correct MMR (Drift 5%, HL 2.5%)

**Acceptance:** end-to-end test green · cross-venue funding-arb backtest
emits per-venue events.

#### W2.A.3 — D-2.1.c: Merge `LiveContext` + `LiveExecutionContext` · L · 1w

**Prereq:** D-2.1.b ✓ + repo secrets configured

- [ ] **PRE:** configure `DRIFT_DEVNET_KEYPAIR` + `HL_TESTNET_KEY` repo
  secrets so `.github/workflows/live-smoke.yml` can run
- [ ] Manual smoke run before refactor: place + cancel $0.01 limit on
  Drift devnet + HL testnet — establish baseline
- [ ] Collapse `LiveContext` (216 LOC) into `LiveExecutionContext` (651 LOC)
  — keep the richer variant
- [ ] Migrate callers: `flint/execution/{drift_live,hyperliquid_live,
  ccxt_live,jupiter_live,multi_venue_live}.py`
- [ ] Run live-smoke again post-refactor: same orders succeed
- [ ] Delete `flint/execution/live_context.py` if it ends up dead

**Acceptance:** live-smoke workflow green on both venues · existing
mocked live tests green · zero behavior change on devnet/testnet.

### Track B (engineer-2, 1 week)

#### W2.B.1 — D-3.1-rust: Rust `OrderbookFiller` · M · 3d

**Prereq:** D-3.4-rust ✓

- [ ] `rust/src/engine/orderbook_fill.rs:OrderbookFiller` mirrors Python
- [ ] L2 data marshaling: `OrderbookSnapshot` PyO3 ↔ Rust struct
- [ ] Reject when `size > sum(level.size)` on taking side
- [ ] Per-fill `impact_bps` attribution = `(vwap - mid) / mid * 10_000`
- [ ] `tests/test_rust_orderbook_parity.py` — 9 scenarios from Python suite

**Acceptance:** parity 1e-6 across all scenarios · capability matrix
flips `supports_orderbook_walk=True`.

#### W2.B.2 — D-3.3-maker-detection: Maker/taker role in Rust pipeline · M · 2d

**Prereq:** D-3.4-rust ✓

- [ ] Add `is_maker: bool` to Rust `FillResult`
- [ ] Order lifecycle tracks: resting limit fill = maker;
  crossing market or marketable limit = taker
- [ ] `FeeModel::compute_fee_with_role(fill, fill.is_maker)` populated
- [ ] `tests/test_rust_maker_detection.py` — limit-rests-then-hit ↔ maker;
  cross-market ↔ taker; fee diverges by maker_bps vs taker_bps

**Acceptance:** Drift maker rebates appear in Rust backtests · parity test
asserts identical fees between Python `DriftFeeModel(is_maker)` and Rust
`FeeModel::Drift.compute_fee_with_role(_, true)`.

---

## Wave 3 — Live deploy gate (weeks 6–9)

**Goal:** ship `/api/v1/live/start` with two-step confirmation. After
this, UI can deploy live sessions safely. WebSocket streams + funding
arb proof notebook land in parallel.

### Track A (engineer-1, 3 weeks)

#### W3.A.1 — D-6.5-api: `/api/v1/live/preview` + `/start` + kill switch · XL · 3w

**Prereq:** T5.6 secrets + D-2.1.c

- [ ] **Week 1 — server side:**
  - [ ] `POST /api/v1/live/preview` returns dry-run summary + `confirmation_token` (60s TTL)
  - [ ] `POST /api/v1/live/start` requires token; launches session
  - [ ] `POST /api/v1/live/{id}/kill` flattens + cancels within 5s
  - [ ] Mainnet gate: env var `FLINT_MAINNET_ENABLED=1` + CLI prompt
  - [ ] Token storage in process memory (no persistence; expiry enforced)
- [ ] **Week 2 — UI flow:**
  - [ ] Multi-step deploy modal in `LiveMonitor.tsx`:
    strategy → markets → venues → capital → risk limits → preview → confirm
  - [ ] Mainnet warning banner with red border + double-confirm checkbox
  - [ ] Kill button on session row with confirm dialog
- [ ] **Week 3 — validation:**
  - [ ] CSRF protection: token + same-origin check
  - [ ] Race conditions on kill (double-kill, kill-during-preview)
  - [ ] 30-day devnet round-trip campaign (T5.6 smoke runs daily)
  - [ ] Mainnet-dry-run validation (5 venues × 5 markets)
  - [ ] Document `docs/how-to/deploy-live.md` with full checklist

**Acceptance gates:**
- 30 consecutive devnet smoke runs green
- Mainnet path provably unreachable without env var + UI confirm + token
- Kill switch flattens within 5s under sustained load
- CSRF attack simulation rejected

### Track B (engineer-2, 1 week)

#### W3.B.1 — D-4.3-websocket: Paper + Live WebSocket streams · L · 1w

**Prereq:** D-4.2-backoff-full ✓

- [ ] Server: `flint/api/websocket.py` adds `/ws/paper/{id}` + `/ws/live/{id}`
- [ ] Stream `{equity, unrealized_pnl, last_trade}` on engine tick
- [ ] Reconnect: client sends `last_seen_ts`; server replays missed events
- [ ] Heartbeat every 10s; close on 30s silence
- [ ] Generic `useWebSocket<T>` hook with reconnect backoff
- [ ] Migrate `usePaperTrading`, `useLiveMonitor`, `FillAnalysis` to WS
- [ ] Fallback: when WS fails 3x, fall through to polling with banner
- [ ] `tests/test_websocket_streams.py` — happy path + reconnect + fallback

**Acceptance:** kill server mid-session, UI reconnects within 5s
without losing state · 0 polls in 60s with 1 active paper session
(was 30) · works behind a corporate firewall (auto-fallback).

### Track C (quant + engineer-3, 1 week)

#### W3.C.1 — D-6.6-proof: Funding dislocation arb proof notebook · M · 1w

**Prereq:** D-1.4-ui ✓ + D-6.5-api ✓

- [ ] `notebooks/funding_dislocation_arb.py` (jupytext) — 30 days SOL-PERP
  - Pin candle + funding sha256
  - Backtest with seed=42
  - Paper replay on same data
  - Reconciliation report inline (uses D-1.4-ui upload pipeline)
  - Plots: spread over time, per-leg P&L, total arb P&L, parity vs live
- [ ] `docs/how-to/deploy-funding-arb.md` — 6-step checklist:
  1. 30-day backtest on historical data
  2. 7-day paper session on live data
  3. Verify reconciliation < 5bps
  4. Deploy mainnet at 0.1× intended capital (uses D-6.5-api)
  5. Monitor 48h; verify risk guards engage on synthetic dislocations
  6. Ramp capital
- [ ] Proof artifact: `artifacts/proof-data/funding_dislocation_arb-2026-Q2.parquet`
  + checksum pinned in notebook

**Acceptance:** notebook runs top-to-bottom on a clean clone in <10 min
with sample data; emits parity artifact with <2% PnL divergence.

---

## Wave 4 — Solana + portfolio specialization (weeks 10–14)

**Goal:** real Jito bundles for Drift live + true shared-capital
portfolio backtest. Two long XL items in parallel.

### Track A (Solana expert, 2 weeks)

#### W4.A.1 — D-6.7-jito: Real Jito bundle integration · L · 2w

**Prereq:** D-6.5-api ✓

- [ ] **Week 1:** core integration
  - [ ] `flint/execution/jito_client.py` — bundle constructor + tip-account
    selector + RPC submit
  - [ ] Drift transaction + tip transfer atomically packaged
  - [ ] Confirmation polling (poll every 100ms up to 5s)
  - [ ] Fallback to standard Solana RPC when bundle rejected
- [ ] **Week 2:** integration + benchmark
  - [ ] `DriftLiveContext` opt-in via `venue_config.drift.jito_enabled = True`
  - [ ] Benchmark vs standard RPC: latency, inclusion rate, fee delta
  - [ ] `docs/reference/jito-integration.md` with measured numbers
  - [ ] `tests/integration/test_jito_smoke.py` (gated by `FLINT_JITO_SMOKE=1`)

**Acceptance:** mainnet order via Jito confirms within 2 slots typical ·
benchmark shows ≥30% latency improvement over standard RPC on busy slots ·
fallback exercised + verified.

### Track B (engineer-1, 3 weeks)

#### W4.B.1 — D-6.1-unified: Shared-capital `PortfolioBacktestEngine` · XL · 3w

**Prereq:** D-2.1.b ✓ + D-3.5-orchestrator ✓

- [ ] **Week 1:** capital pool model
  - [ ] `PortfolioBacktestConfig` — strategies + per-strategy capital_pct
    + total_capital + shared `MarginConfig`
  - [ ] Replace per-strategy isolated buckets with shared `PortfolioMarginEngine`
- [ ] **Week 2:** P&L attribution
  - [ ] Each position carries `strategy_id` field; PnL computed from fills
    keyed to that strategy
  - [ ] Margin netting: long Drift + short HL on same market reduces
    margin requirement; attribution still per-strategy
- [ ] **Week 3:** UI + tests
  - [ ] `ui/src/pages/PortfolioLab.tsx` — multi-strategy runner
  - [ ] Plots: aggregate equity, per-strategy equity, capital utilization,
    correlation matrix
  - [ ] `tests/test_portfolio_unified.py` — 3 strategies × 2 markets
    × $100k; `sum(per_strategy_pnl) == combined_pnl` to 1e-6

**Acceptance:** 3-strategy backtest shows shared-capital draw + per-strategy
attribution · book-level risk caps reject orders pre-trade.

---

## Wave 5 — Capstone (weeks 14–17)

#### W5.1 — D-6.4-replay: Portfolio event-sourcing + time-travel replay · XL · 2-3w

**Prereq:** D-2.2-internal ✓ (already shipped)

- [ ] **Week 1:** event schema + writer
  - [ ] `portfolio_events(session_id, seq, ts, kind, payload_json)` table
  - [ ] Writer hooks in: order-submit, fill, cancel, funding, liquidation
  - [ ] Monotonic seq enforced via DuckDB `seq INTEGER PRIMARY KEY AUTOINCREMENT`
- [ ] **Week 2:** snapshot + replay
  - [ ] `PortfolioSnapshot(session_id, seq, book_state_json)` compaction
    every 10k events
  - [ ] `replay(session_id, target_ts) -> BookState`:
    1. Find latest snapshot before `target_ts`
    2. Replay events forward
    3. Return positions + cash + funding-accrued + pending-orders
  - [ ] `tests/test_replay.py` — assert replayed state byte-identical
    to live state at the target_ts
- [ ] **Week 3:** UI + perf
  - [ ] PortfolioLab + PaperTrading: time-travel slider on equity chart;
    click point → modal shows book state at that ts
  - [ ] Performance: replay on 30-day session <1s
  - [ ] Index tuning: `(session_id, seq)`, `(session_id, ts)`

**Acceptance:** replay < 1s on 30-day session · positions exact ·
equity within 1e-6 · UI slider scrubs without lag.

---

## Recommended sequence (single engineer, sequential)

If only one engineer is available:

1. **Week 1:** D-5.1-ruff (1 day) + D-3.4-rust (4 days)
2. **Week 2:** D-3.1-rust (3 days) + D-3.3-maker (2 days)
3. **Weeks 3–4:** D-2.1.b (god class breakup, 2 weeks)
4. **Week 5:** D-2.1.d (PaperContext, 1 week)
5. **Week 6:** D-3.5-orchestrator (margin facade, 1 week)
6. **Week 7:** D-2.1.c (live-context merge, 1 week)
7. **Week 8:** D-1.4-ui + D-4.2-backoff-full + D-4.7-full (UI cluster, 1.5 weeks)
8. **Weeks 9–10:** D-4.3-websocket (1 week)
9. **Weeks 11–13:** D-6.5-api (live deploy, 3 weeks)
10. **Weeks 14–15:** D-6.7-jito (Jito bundles, 2 weeks)
11. **Week 16:** D-6.6-proof (funding arb proof, 1 week)
12. **Weeks 17–19:** D-6.1-unified (portfolio bt, 3 weeks)
13. **Weeks 20–22:** D-6.4-replay (event sourcing, 2-3 weeks)

**Total:** 22 weeks ≈ 5.5 months single-engineer.

---

## Recommended sequence (3 engineers, parallel)

Engineer assignments to maximize calendar throughput:

| Engineer | Skills needed | Weeks |
|---|---|---|
| **Eng-A — Architecture** | Python, refactoring, ExecutionContext deep dive | D-2.1.b, D-2.1.d, D-3.5, D-2.1.c, D-6.1, D-6.4 |
| **Eng-B — Rust + Solana** | Rust, PyO3, cargo, Solana RPC, Jito | D-3.4-rust, D-3.1-rust, D-3.3-maker, D-6.7 |
| **Eng-C — UI + APIs** | React, FastAPI, WebSocket, TypeScript | D-1.4-ui, D-4.2-backoff-full, D-4.3-websocket, D-4.7-full, D-6.5-api UI side, D-5.1-ruff |
| **Quant** (~10% time) | Strategy + reproducibility | D-6.6-proof |

**Synchronization points:**
- End of W3: Eng-A finishes D-2.1.b → unblocks Eng-A's wave 2 work + D-6.1
- End of W5: Eng-B finishes Rust ports → Phase 3 exit criterion 7 closeable
- End of W9: D-6.5-api ships → live-deploy story closes
- End of W14: D-6.1 + D-6.7 ship → Flint 2.0 surface complete
- End of W17: D-6.4 ships → Flint 2.0 done

**Calendar:** 12 weeks (3 months) for full backlog drain.

---

## Risk register

| Risk | Mitigation |
|---|---|
| D-2.1.b extraction breaks invariants between components | Run full sweep + parity tests between every step; never extract two components in same PR |
| D-2.1.c lacks testnet smoke validation | Configure repo secrets W1 day 1; baseline + post-refactor smoke runs; refuse to merge without both |
| D-6.5-api leaks mainnet path | Two-engineer review; security checklist; mainnet env var enforced at module load |
| D-6.7-jito calibration drift | Daily benchmark cron after ship; alert on inclusion-rate <80% |
| D-3.4-rust f64 precision drift | Parity test enforces exact equality on equity curve + tolerance 1e-9 on Sharpe |
| D-6.4-replay perf regression | Benchmark gate in CI: 30-day replay <1s |

---

## Tracking

- Each item closed → mark `✓` in this file + move to `DEFERRED.md`'s
  "Closed since" section with a release link
- Each new sibling sub-item discovered → add to `DEFERRED.md` first
- Update `TRUST_ARTIFACTS.md` when D-1.* items move; update
  `WAVE_STATUS.md` when wave-tracked items ship; update `CHANGELOG.md`
  with the user-visible delta on every release

---

**End-state of this plan:** 16 deferred items closed. Branch `restructure`
merged to `main`. Phase 1–6 exit criteria all green. Flint at 1.4 → 2.0
release. Trust artifacts complete. No backlog of architectural debt.

That's 12 weeks of focused work for a 3-engineer team, or a quarter for a
single engineer. Either way, the foundation is what makes Flint a real
quant lab.
