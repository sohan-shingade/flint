# Deferred Work

Tracker for items scoped in a phase but split into sibling PRs because bundling would make review infeasible or because validation needs something the in-session run couldn't provide (testnet access, UI smoke, real data).

Rule: **an item sits here until it has an owner + ETA, then it moves to an active phase item.** Nothing leaves Flint's todo surface silently.

Updated: 2026-04-23.

---

## Phase 2 deferred

### D-2.1.b — Break up `BacktestContext` god class

- **Spec:** [phase-2 §2.1](docs/specs/phase-2-structural-cleanup.md#21-executioncontext-consolidation)
- **Why deferred:** `flint/execution/backtest_context.py` = 973 LOC, ~40 downstream call sites. Extracting `PositionManager` / `OrderQueue` / `FundingTracker` / `BorrowTracker` / `MarketDataFeed` is a mechanical refactor that touches half the backtest code path. Interleaving with Phase 2 plumbing in one diff produces an unreviewable PR.
- **Interface contract already locked:** `tests/test_context_portability.py` asserts every concrete `ExecutionContext` subclass implements every abstract method. Future refactor cannot silently drop a method.
- **Prerequisites:** none — can start anytime.
- **Validation required:** full pytest sweep (~10 min) on each extraction step. No external dependencies.
- **File targets:**
  - Split source: `flint/execution/backtest_context.py`
  - New modules: `flint/execution/position_manager.py`, `order_queue.py`, `funding_tracker.py`, `borrow_tracker.py`, `market_data_feed.py`
  - Caller updates: everywhere `BacktestContext` is instantiated (~12 sites in `flint/backtest/engine.py`, `flint/portfolio/engine.py`, tests)
- **Success metric:** `BacktestContext` under 300 LOC; new modules each under 250 LOC; existing test suite green.
- **Estimated effort:** ~2 weeks (1 engineer, focused).
- **Owner:** TBD · **ETA:** TBD

### D-2.1.c — Merge `LiveContext` + `LiveExecutionContext`

- **Spec:** [phase-2 §2.1.c](docs/specs/phase-2-structural-cleanup.md#21-executioncontext-consolidation)
- **Why deferred:** touches every live venue integration. No unit test covers "live trading still works"; needs real testnet round-trips.
- **Prerequisites:** D-2.1.b landed (shared components extracted first).
- **Validation required:**
  - Drift devnet: place + cancel a $0.01 SOL-PERP limit order, verify fill event surfaces via new context.
  - Hyperliquid testnet: same.
  - Jupiter devnet (if applicable).
  - Each venue's dry-run path end-to-end.
- **File targets:**
  - Collapse: `flint/execution/live_base.py` + `flint/execution/live_context.py`
  - Migrate callers: `flint/execution/drift_live.py`, `hyperliquid_live.py`, `ccxt_live.py`, `jupiter_live.py`, `multi_venue_live.py`
- **Success metric:** one `LiveExecutionContext` class; no duplicated order-lifecycle code; testnet smoke passes on all four venues.
- **Estimated effort:** ~1 week.
- **Owner:** TBD · **ETA:** TBD

### D-2.1.d — Separate `PaperContext`

- **Spec:** [phase-2 §2.1.d](docs/specs/phase-2-structural-cleanup.md#21-executioncontext-consolidation)
- **Why deferred:** paper trading logic currently threads through `PaperBroker` + `LiveContext` + `paper/engine.py`. Clean separation needs coordinated change to `PaperBroker` internals; can't be bundled with Phase 2 plumbing.
- **Prerequisites:** D-2.1.b (needs `PositionManager` + `FundingTracker` + `MarketDataFeed` extracted first so PaperContext can compose them).
- **Validation required:** existing paper trading tests (`tests/test_paper.py`, `test_parity.py`) remain green; ParityTest divergence within current thresholds.
- **File targets:**
  - New: `flint/execution/paper_context.py:PaperContext`
  - Modified: `flint/paper/engine.py` (drive PaperContext instead of LiveContext in replay mode)
  - `flint/backtest/parity.py` (ParityTest swaps in PaperContext)
- **Success metric:** `PaperContext` implements `ExecutionContext`; `PaperBroker` owns fills only; parity test still passes.
- **Estimated effort:** ~1 week.
- **Owner:** TBD · **ETA:** TBD

### D-2.4.b — Wire sandbox into `/api/v1/backtest/run` for user strategies

- **Spec:** [phase-2 §2.4](docs/specs/phase-2-structural-cleanup.md#24-user-strategy-subprocess-isolation)
- **Why deferred:** switching routing for user-uploaded strategies changes the error surface the UI sees (`StrategyTimeoutError` / `StrategyMemoryError`). UI needs new banner/toast handling; no current UI tests cover this path. Pairs naturally with Phase 4.2 error-recovery work.
- **Prerequisites:** Phase 4.2 (UI error-recovery banner).
- **Validation required:**
  - Upload a `while True: pass` strategy → UI shows timeout banner + clears stale polling.
  - Upload a memory bomb → UI shows memory-limit banner.
  - Normal strategies still run unchanged.
- **File targets:**
  - `flint/api/routes/backtest.py` — route uploaded code through `run_strategy_in_sandbox` when strategy path matches `strategies/user/*.py` or inline code from `user_strategies.py`.
  - `ui/src/hooks/useBacktest.ts` — surface new error codes.
  - `ui/src/pages/BacktestLab.tsx` — banner.
- **Success metric:** user cannot hang the server with a malicious strategy via the API; clear UI feedback on sandbox enforcement.
- **Estimated effort:** ~3 days (plus paired UI work).
- **Owner:** TBD · **ETA:** TBD

### D-2.2-internal — Migrate journal + paper/session_store helpers off raw `store._conn`

- **Spec:** [phase-2 §2.2.c](docs/specs/phase-2-structural-cleanup.md#22-wrap-raw-store-access)
- **Why deferred:** `flint/journal/storage.py` (~15 sites) and `flint/paper/session_store.py` (~10 sites) predate the CLAUDE.md rule. They are internal helpers, not routes — rule is silent on them but the smell is the same.
- **Scope:** fold the SQL from these helpers into `FlintStore` as `save_journal_run` / `get_journal_run` / `list_journal_runs` / `save_paper_session` / etc. Delete the helper classes; callers use `FlintStore` directly.
- **Prerequisites:** none.
- **Validation required:** journal + paper tests (`tests/test_paper.py`, `test_parity.py`, backtest journal paths) stay green.
- **File targets:** delete `flint/journal/storage.py` and `flint/paper/session_store.py`; expand `flint/store.py`; migrate callers.
- **Success metric:** grep `store\._conn|store\._lock` finds hits only in `flint/store.py` (currently also finds hits in journal/storage + paper/session_store).
- **Estimated effort:** ~1 week.
- **Owner:** TBD · **ETA:** TBD

---

## Phase 6 deferred

### D-6.1-unified — Shared-capital PortfolioBacktestEngine

- **Spec:** [phase-6 §6.1](docs/specs/phase-6-portfolio-cross-venue.md#61-multi-strategy-backtest)
- **Why deferred:** current `flint.portfolio.engine.PortfolioEngine` runs each strategy in an ISOLATED capital bucket and sums their equity curves. True shared-pool semantics (strategy A's losses drain strategy B's budget, pre-trade `PortfolioRiskEngine` gate on every order) requires `BacktestContext` god-class breakup (D-2.1.b) first so position/cash state can be threaded through a single portfolio-level account.
- **Prerequisites:** D-2.1.b (BacktestContext breakup) + T6.2 PortfolioRiskEngine (now shipped).
- **Validation:** backtest with 3 strategies on $100k total capital; sum(per-strategy PnL) == combined PnL to 1e-6; correlation matrix computed from per-strategy returns; order rejected pre-trade when book-level RiskLimits breach.
- **Estimated effort:** ~3-4 weeks (follows D-2.1.b).

### D-6.4-replay — Portfolio event-sourcing + replay

- **Spec:** [phase-6 §6.4](docs/specs/phase-6-portfolio-cross-venue.md#64-portfolio-replay)
- **Why deferred:** requires every state-changing event (order, fill, cancel, funding payment, liquidation) to gain a monotonic sequence number stored in DuckDB. `PortfolioSnapshot` compaction table. `replay(session_id, target_ts) -> BookState` function. ~2-3 weeks of storage + serialization work.
- **Prerequisites:** D-2.2-internal (journal/storage migrated to FlintStore) to avoid duplicating event-sourcing in two places.
- **Estimated effort:** ~2-3 weeks.

### D-6.5-api — /api/v1/live/start + two-step confirmation

- **Spec:** [phase-6 §6.5](docs/specs/phase-6-portfolio-cross-venue.md#65-apiv1livestart)
- **Why deferred:** `/api/v1/live/preview` + `/api/v1/live/start` + confirmation-token flow + mainnet gate + kill-switch endpoint + UI multi-step modal. Touches live code paths; needs Phase 5 T5.6 smoke tests to prove end-to-end first. Without the smoke harness running against real secrets, shipping this would be negligent.
- **Prerequisites:** T5.6 smoke workflow actually runs (secrets configured) + D-2.1.c live-context merge.
- **Estimated effort:** ~3 weeks.

### D-6.6-proof — Funding dislocation arb proof notebook + mainnet checklist

- **Spec:** [phase-6 §6.6](docs/specs/phase-6-portfolio-cross-venue.md#66-funding-dislocation-arb-reference)
- **Why deferred:** strategy file ships (T6.6). Proof notebook (`notebooks/funding_dislocation_arb.py` mirroring the Phase 1.5 set) + `docs/how-to/deploy-funding-arb.md` checklist (30-day backtest → 7-day paper → reconciliation <5bps → 0.1x mainnet → monitor 48h → ramp) need real data + reconciliation tooling from Phase 4.
- **Prerequisites:** D-1.4-api (reconciliation UI) + D-6.5-api (live deploy route).
- **Estimated effort:** ~1 week.

### D-6.7-jito — Jito bundle integration

- **Spec:** [phase-6 §6.7](docs/specs/phase-6-portfolio-cross-venue.md#67-jito-bundle-integration)
- **Why deferred:** `flint/execution/jito_client.py` submitting bundles to the Jito Block Engine RPC. Proper Solana RPC engineering; requires solana-py bundle support + tip-account signing + confirmation polling. Today `SolanaTxCostModel` estimates tips as flat lamports — adequate for backtest cost modeling, not for live execution.
- **Prerequisites:** D-6.5-api (live deploy surface).
- **Estimated effort:** ~2 weeks.

---

## Phase 4 deferred

### D-4.1-wedge — README editorial wedge rewrite

- **Spec:** [phase-4 §4.1](docs/specs/phase-4-product-polish.md#41-readme-rewrite)
- **Why deferred:** T4.1 shipped auto-counts + "4-tier" misnomer fix. The larger editorial rewrite (hero blurb refocus, split comparison tables for CEX vs DeFi-perp, remove TradingView row, replace `examples/` with `notebooks/` in "Try It" section, trust-artifacts badge) is an editorial call and touches ~100 lines of prose. Safer as its own PR.
- **Prerequisites:** none.
- **Estimated effort:** ~1 day.

### D-4.2-backoff — Hook-level polling backoff + silent-catch cleanup

- **Spec:** [phase-4 §4.2](docs/specs/phase-4-product-polish.md#42-ui-error-recovery)
- **Why deferred:** T4.2 shipped `ConnectionBanner.tsx` + fixed the App-level silent catch. Remaining work: exponential backoff (1s → 2s → 5s → 10s → 30s) in every polling hook (`useBacktest`, `useOptimize`, `usePaperTrading`, `useLiveMonitor`, `useFillAnalysis`), replace 9 remaining `.catch(() => {})` sites with logged + user-facing handlers, per-hook error banners. Needs manual browser testing per hook.
- **Prerequisites:** none.
- **Estimated effort:** ~3 days.

### D-4.3-websocket — Paper + Live WebSocket streams

- **Spec:** [phase-4 §4.3](docs/specs/phase-4-product-polish.md#43-paperlive-websocket)
- **Why deferred:** FastAPI `/ws/paper/{id}` + `/ws/live/{id}` routes + reconnecting `useWebSocket` hook + migration of PaperTrading + LiveMonitor pages + fallback-to-polling path. ~1 week; changes server + UI surface; needs browser testing.
- **Prerequisites:** D-4.2-backoff (backoff logic reused in reconnect).
- **Estimated effort:** ~1 week.

### D-4.5-ui — Wire cancellation UI

- **Spec:** [phase-4 §4.5](docs/specs/phase-4-product-polish.md#45-backtestoptimization-cancellation)
- **Why deferred:** engine-side cancellation ships (T4.5) + route worker honors cancel + `BacktestCancelled` exception cleans state. UI work: `useBacktest` POSTs `/cancel` on unmount, cancel button on BacktestLab results panel, banner when status="cancelled". Needs UI testing.
- **Prerequisites:** T4.2 banner surface.
- **Estimated effort:** ~1 day.

### D-4.7-mcp-inprocess — MCP in-process service layer

- **Spec:** [phase-4 §4.7](docs/specs/phase-4-product-polish.md#47-mcp-in-process-mode)
- **Why deferred:** ~2-3 day refactor: extract `flint/services/{backtest,paper,optimization,data,journal}.py` modules that don't depend on FastAPI; route handlers and MCP tools both call service fns instead of double-hopping through HTTP. Requires coordinated change across ~15 files. Safer as dedicated PR.
- **Prerequisites:** none (can parallel with anything).
- **Estimated effort:** ~3 days.

---

## Phase 3 deferred

### D-3.3-maker-detection — Maker-vs-taker fill role detection in Rust

- **Spec:** [phase-3 §3.3](docs/specs/phase-3-depth-on-wedge.md#33-makertaker-fee-split-in-rust)
- **Why deferred:** T3.3 landed the `FeeModel::{MakerTaker,Drift,Hyperliquid}` variants + `compute_fee_with_role(is_maker)` API. What's missing is the Rust fill pipeline actually tracking whether each fill was maker (resting limit that got hit) vs taker (crossed the book); today it always treats fills as taker. Wiring maker detection requires touching `VenueFiller` + order-lifecycle state, which overlaps with D-3.4-rust.
- **Prerequisites:** D-3.4-rust (Rust fill pipeline port).
- **Validation:** parity test asserting that a resting limit fill in Rust uses maker bps while a crossing market fill uses taker bps.
- **Estimated effort:** ~2 days once D-3.4-rust lands.

### D-3.4-rust — Port PartialFillStage + LatencyStage to Rust

- **Spec:** [phase-3 §3.4](docs/specs/phase-3-depth-on-wedge.md#34-partial-fills--latency-stage-in-rust)
- **Why deferred:** proper Rust engineering — new `rust/src/engine/latency.rs` + `partial_fill.rs` + `fill_pipeline.rs` modules composing latency → impact → partial in the same order as Python. Needs cargo tests, PyO3 wiring, parity tests. ~1 week of focused Rust work; bundling with in-session Phase 3 plumbing would be reckless.
- **Prerequisites:** none — Rust side of `FillPipeline` can be built incrementally.
- **Validation:** extend `tests/test_rust_python_parity.py` to run a multi-bar partial-fill scenario; byte-identical results with same seed.
- **Rust capability flags flip:** `supports_partial_fills=True`, `supports_latency_stage=True` in `flint_core.capabilities()`.
- **Estimated effort:** ~1 week.

### D-3.1-rust — Rust port of OrderbookFillModel

- **Spec:** [phase-3 §3.1](docs/specs/phase-3-depth-on-wedge.md#31-orderbook-walk-fills-rust--python)
- **Why deferred:** T3.1 hardened the Python `OrderbookFillModel` (depth rejection + impact_bps attribution). Rust port writes a matching `OrderbookFiller` struct in `rust/src/engine/fills.rs` and feeds it L2 data through the PyO3 API. Rust `FillPipeline` needs to land first (D-3.4-rust) so orderbook dispatch slots into the impact stage.
- **Prerequisites:** D-3.4-rust.
- **Validation:** `tests/test_rust_orderbook_parity.py` — same single-level / multi-level / rejection scenarios as the Python suite, Rust must match exactly.
- **Estimated effort:** ~3 days once D-3.4-rust lands.

### D-3.5-orchestrator — Unified PortfolioMarginEngine in BacktestContext

- **Spec:** [phase-3 §3.5](docs/specs/phase-3-depth-on-wedge.md#35-multi-venue-positions--shared-margin)
- **Why deferred:** primitives (MarginEngine + VenueAllocator + Transfer) all ship and are tested in composition (`tests/test_multi_venue_margin_integration.py`). The missing piece is a `PortfolioMarginEngine` facade that BacktestContext consults on every order → check per-venue margin, debit/credit the right bucket, emit per-venue `LiquidationEvent`s into `BacktestResult`. Wiring into `BacktestContext` is coupled to the god-class breakup (D-2.1.b); doing it on top of the current 973-LOC file guarantees a merge conflict with that work.
- **Prerequisites:** D-2.1.b (`BacktestContext` broken up).
- **Validation:** end-to-end backtest with multi-venue funding-arb strategy, both venues holding leveraged positions, verify per-venue funding accrual + per-venue liquidation at each venue's MMR.
- **Estimated effort:** ~1 week once D-2.1.b lands.

---

## Phase 1 deferred / follow-ups

### D-1.1.b — Rust force-close exit fees

- **Spec:** [phase-1 T1.1.f](docs/specs/phase-1-trust-correctness.md#11-rust-python-parity-fixes)
- **Why deferred:** `rust/src/engine/positions.rs:198` constructs the force-close `FillResult` with `fee: 0.0`. Python path charges exit fees. Tolerance-bounded in `tests/test_rust_python_parity.py::test_parity_force_close_pnl_relaxed` (within 0.2% of initial capital). Fix = compute fee via `FeeModel` trait in Rust.
- **Prerequisites:** Phase 3.3 (Rust `FeeModel` trait) — fix lands naturally there.
- **Validation required:** force-close parity test tightens to 0.05% tolerance.
- **Estimated effort:** ~2 hours once Phase 3.3 lands.

### D-1.2-CI — Parity report CI gate

- **Spec:** [phase-1 T1.2](docs/specs/phase-1-trust-correctness.md#12-parity-report-pipeline)
- **Why deferred:** script ships + thresholds enforced via exit code, but CI workflow wiring is part of [Phase 5.3](docs/specs/phase-5-ci-testing.md#53-parity-report-ci-gate) so it lives with the rest of the CI hardening.
- **Prerequisites:** Phase 5 starts.
- **Estimated effort:** ~1 day.

### D-1.3-providers — Remaining 22 provider PIT declarations

- **Spec:** [phase-1 T1.3.a](docs/specs/phase-1-trust-correctness.md#13-pit-audit-and-deterministic-seeds)
- **Why deferred:** `scripts/audit_pit.py` ships + 3 flagship providers declared. Remaining 22 providers need per-module review (confirm what `ts` actually represents in each source API). Can be incremental — one provider per PR.
- **Prerequisites:** none — contributors can pick up any single provider.
- **Validation required:** running `python scripts/audit_pit.py --fail-on-warn` should exit 0 once all declared.
- **Estimated effort:** ~30 min per provider × 22 = ~11 hours total.

### D-1.4-api — Reconciliation API + UI panel

- **Spec:** [phase-1 T1.4](docs/specs/phase-1-trust-correctness.md#14-reconciliation-tooling)
- **Why deferred:** CLI + 14-test matching logic ship this phase. `GET /api/v1/paper/{session_id}/reconciliation` endpoint + `PaperTrading.tsx` panel = Phase 4 work (UI-coupled, same reason as D-2.4.b).
- **Prerequisites:** Phase 4 starts.
- **Estimated effort:** ~3 days.

### D-1.5-data-pins — Replace `PIN-ME` placeholders in proof notebooks

- **Spec:** [phase-1 T1.5](docs/specs/phase-1-trust-correctness.md#15-proof-notebooks)
- **Why deferred:** notebooks ship with `EXPECTED_CANDLE_HASH = "PIN-ME"` pending a canonical data snapshot committed to `artifacts/proof-data/`. Needs 30-day of SOL-PERP candles downloaded + hashed + committed as fixture.
- **Prerequisites:** decision on whether to commit large-ish (~1MB) fixture files to git or stash them in an artifact store.
- **Estimated effort:** ~1 hour once decided.

### D-1.6-byo-fills — Custom fill log ingest

- **Spec:** [phase-1 T1.6.d](docs/specs/phase-1-trust-correctness.md#16-custom-dataset-ingest)
- **Why deferred:** CSV/Parquet candles + funding ship. BYO fill log uses the same format but goes through reconciliation (D-1.4) and calibration (Phase 3.6) — fold in with whichever lands first.
- **Prerequisites:** D-1.4-api or Phase 3.6 (whichever first).
- **Estimated effort:** ~3 hours (format already documented; just add `load_fills_csv` helper).

---

## How to pick something up

1. Claim it: PR that sets `Owner:` and `ETA:` on the row above.
2. Move it: if the item is substantial (>1 day), create a dedicated phase spec entry or add to an existing phase spec; link both ways.
3. Close it: when merged, delete the row from this file and update [`TRUST_ARTIFACTS.md`](TRUST_ARTIFACTS.md) if the item is a Phase 1 trust artifact.

Do not let items silently age. If a deferred item has been sitting for more than 30 days without an owner, re-evaluate whether it still matters. Out-of-date deferrals go stale just like out-of-date comments.
