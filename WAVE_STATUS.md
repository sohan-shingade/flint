# Wave Status — Live Tracker

Per-item state for the 16-item deferred backlog. Plan:
[`docs/specs/deferred-execution-plan.md`](docs/specs/deferred-execution-plan.md).

**Update this file every time an item moves status.** Last touch
timestamps surface stalls.

Updated: 2026-04-24 (start of Wave 1 execution).

---

## Legend

| State | Meaning |
|---|---|
| 🔴 **Not started** | No code or design PR |
| 🟡 **In progress** | Branch open, partial code merged |
| 🟢 **Shipped** | Merged to `restructure`, tests green |
| ⏭️ **Blocked** | Waiting on a dependency that hasn't shipped |
| 🚫 **Won't do** | Deliberately dropped (with rationale) |

---

## Wave 1 — weeks 1–3

| ID | State | Effort | Owner | Last update | Notes |
|---|---|---|---|---|---|
| D-2.1.b | 🟡 | 2w | claude | 2026-04-24 | Steps 1–2 of 7 shipped: `PositionManager` + `CashManager` extracted, BacktestContext composes both via property aliases · Steps 3–7 (FillRecorder, FundingLedger, BorrowLedger, OrderbookCache, Risk surface) remain |
| D-3.4-rust | 🟢 | 1w | claude | 2026-04-24 | `engine/tx_costs.rs` + PyO3 `TxCostModel` class · 14 cargo tests · 13 parity tests (1e-9) · Rust 2.24x faster on the tight loop · `supports_tx_costs = true` in Rust capabilities |
| D-5.1-ruff | 🟢 | 1d | claude | 2026-04-24 | Auto-fix sweep done · ruff configured to F-class only · CI hard-fails on `ruff check` |
| D-4.7-full | 🟢 | 3d | claude | 2026-04-24 | Services layer (strategies/backtest/journal/data/paper) shipped · MCP backtest+journal in-process · 12 standalone tests |
| D-1.4-ui | 🟢 | 3d | claude | 2026-04-24 | POST /paper/{id}/reconciliation accepts multipart CSV · PaperTrading.tsx Reconcile button + results panel · 6 endpoint tests |
| D-4.2-backoff-full | 🟢 | 3d | claude | 2026-04-24 | `useBackoffPoll<T>` hook with 1→2→5→10→30s schedule + AbortController · `useLiveMonitor`, `usePaperPortfolio`, `useSessionStatus` migrated · 5 unit tests · 127/127 vitest green |

## Wave 2 — weeks 4–5

| ID | State | Effort | Prereq | Notes |
|---|---|---|---|---|
| D-2.1.d | ⏭️ | 1w | D-2.1.b | Separate `PaperContext` |
| D-3.5-orchestrator | ⏭️ | 1w | D-2.1.b | `PortfolioMarginEngine` in BacktestContext |
| D-2.1.c | ⏭️ | 1w | D-2.1.b + testnet secrets | Merge LiveContext + LiveExecutionContext |
| D-3.1-rust | 🟢 | 3d | D-3.4-rust ✓ | `engine/orderbook_fill.rs` walks bids/asks for VWAP fill · PyO3 `OrderbookFiller` · 9 cargo + 13 parity tests (1e-9) · 3.52x speedup over Python |
| D-3.3-maker-detection | 🟢 | 2d | D-3.4-rust ✓ | Shipped: `FillResult.is_maker` flag, resting-limit path tags maker=true, `compute_fee_with_role` replaces `compute_fee`, `RustEngine(fee_model="drift"/"hyperliquid"/"maker_taker")` exposed — 6 maker-rebate tests green |

## Wave 3 — weeks 6–9

| ID | State | Effort | Prereq | Notes |
|---|---|---|---|---|
| D-6.5-api | ⏭️ | 3w | D-2.1.c + secrets | `/api/v1/live/start` two-step confirmation |
| D-4.3-websocket | ⏭️ | 1w | D-4.2-backoff-full | Paper + Live WS streams |
| D-6.6-proof | ⏭️ | 1w | D-1.4-ui + D-6.5-api | Funding dislocation arb proof notebook |

## Wave 4 — weeks 10–14

| ID | State | Effort | Prereq | Notes |
|---|---|---|---|---|
| D-6.7-jito | ⏭️ | 2w | D-6.5-api | Real Jito bundle integration |
| D-6.1-unified | ⏭️ | 3w | D-2.1.b + D-3.5-orchestrator | Shared-capital portfolio backtest |

## Wave 5 — weeks 14–17

| ID | State | Effort | Prereq | Notes |
|---|---|---|---|---|
| D-6.4-replay | ⏭️ | 2-3w | D-2.2-internal ✓ | Event sourcing + time-travel replay |

---

## Already shipped (closed pre-Wave-1)

These already landed across phase commits + the deferred-execution
batch (see git log on branch `restructure`):

| ID | Phase | Notes |
|---|---|---|
| Phase 1 (T1.1.a..g + parity) | Phase 1 | Force-close + cross-market + seeds + tx_cost gate |
| Phase 1 T1.3 (PIT + determinism) | Phase 1 | 26/26 providers PIT-declared after D-1.3 batch |
| Phase 1 T1.6 (custom data) | Phase 1 | CSV/Parquet ingest + provenance |
| Phase 1 T1.2 (parity report) | Phase 1 | `scripts/run_parity_report.py` |
| Phase 1 T1.4 (reconcile CLI) | Phase 1 | `scripts/reconcile_fills.py` |
| Phase 1 T1.5 (proof notebooks) | Phase 1 | 3 jupytext notebooks |
| Phase 1 T1.7-sb (status board) | Phase 1 | `TRUST_ARTIFACTS.md` |
| Phase 2 T2.2 (store encapsulation) | Phase 2 | 10 FlintStore methods, route migration |
| Phase 2 T2.3 (BacktestConfig) | Phase 2 | Dataclass + checksum + from_yaml |
| Phase 2 T2.4 (sandbox) | Phase 2 | `flint/strategy/sandbox.py` |
| Phase 2 T2.1.a/e (Protocol + portability) | Phase 2 | Conformance test guards future drift |
| Phase 3 T3.2 (capability matrix) | Phase 3 | `/api/v1/capabilities` + `rust_required` flag |
| Phase 3 T3.3 (maker/taker fees) | Phase 3 | Rust + Python fee model trait |
| Phase 3 T3.1 (Python orderbook hardening) | Phase 3 | Reject + impact_bps |
| Phase 3 T3.6 (calibration reports) | Phase 3 | `scripts/calibrate.py` |
| Phase 4 T4.1 (auto-counts) | Phase 4 | `scripts/update_readme_counts.py` |
| Phase 4 T4.2 (ConnectionBanner) | Phase 4 | UI banner + 18 silent-catch fixes (D-4.2 partial) |
| Phase 4 T4.4 (lazy Monaco) | Phase 4 | `React.lazy` |
| Phase 4 T4.5 (cancellation) | Phase 4 | Engine `BacktestCancelled` + UI `CANCEL` button (D-4.5 done) |
| Phase 4 T4.6 (capabilities route) | Phase 4 | `/api/v1/capabilities` ships |
| Phase 5 T5.1-T5.6 (CI hardening) | Phase 5 | Matrix + Rust + parity + smoke workflows |
| Phase 6 T6.1-T6.6 (portfolio scaffolding) | Phase 6 | RiskEngine + correlation objective + dislocation arb |
| BUG-1..4 fixes (smoke run) | Smoke | live/sessions column · engine_used · version · Monaco CDN |
| D-1.1.b · D-1.2-CI · D-1.3 · D-1.4-api · D-1.5 · D-1.6 | Deferred batch | Phase 1 tail |
| D-2.2-internal · D-2.4.b | Deferred batch | Phase 2 tail |
| D-4.1-wedge · D-4.2 (silent catches) · D-4.5-ui · D-4.7-mcp MVP | Deferred batch | Phase 4 tail |
| Phase-6 portfolio combined-equity force-close fix | Smoke | Discovered + fixed during full sweep |

---

## Test sweep state

- **1861 pass · 7 skip · 0 fail** as of 2026-04-24 14:35 UTC
- 4 smoke regressions (BUG-1..4) all captured by red→green tests
- `tests/test_smoke_regressions.py` runs in <60s

---

## Active branch

`restructure` — 12 commits ahead of `main`, pushed to `origin/restructure`.
PR-ready: `https://github.com/sohan-shingade/flint/pull/new/restructure`.

---

## Active work this session (Wave 1 begun)

- **D-5.1-ruff (🟢)** — `pyproject.toml` adds `[tool.ruff.lint]` selecting F401/F811/F821/F841 only; ignored E402 (PIT_METADATA pattern), E501, E702/E701, E741. 315 errors auto-fixed by ruff; 26 remaining were real bugs (unused vars, missing TYPE_CHECKING imports). All resolved. CI lint job flipped from `|| echo` soft-fail to hard-fail.
- **D-4.7-full (🟢)** — `flint/services/{__init__,strategies,backtest,journal,data,paper}.py` ships. `run_backtest_sync(req, store)` returns the same tearsheet dict shape as the HTTP route. MCP `run_backtest`, `list_journal_runs`, `compare_runs` now call services in-process; `get_paper_sessions` falls back to a store-only view when no daemon is running. Routes in `journal.py` and `paper.py` thinned to adapters. `tests/test_mcp_standalone.py`: 12 tests, all green. 105/105 regression tests on affected paths green.
- **D-2.1.b Step 1 (🟡 — 1/7)** — `flint/execution/position_manager.py:PositionManager` extracted. BacktestContext composes a manager and exposes `self._positions` / `self._closed_positions` as property aliases that return the manager's underlying dict/list, so existing call sites (`_apply_fill`, `apply_funding`, `check_liquidations`, `close_all_positions`) keep mutating in-place without needing migration in this commit. 8 new unit tests; 90 regression tests on context-using paths green. Steps 2–7 (CashManager, FillRecorder, Risk surface, etc.) deferred to a follow-up.
- **D-1.4-ui (🟢)** — `POST /api/v1/paper/{id}/reconciliation` accepts multipart CSV upload, parses via `scripts.reconcile_fills.parse_venue_fills_csv_text` (extracted alongside the existing path-based loader), runs `reconcile()` against engine fills from `store.get_live_fills()`. 10 MB upload cap, schema errors → 400. PaperTrading.tsx adds a hidden file input wired to a "RECONCILE FILLS" button; results panel mirrors the parity report layout (matched/orphan counts + p50/p95/p99 bps and ts deltas). 6 endpoint tests + UI vite build green.
- **D-4.2-backoff-full (🟢)** — `ui/src/hooks/useBackoffPoll.ts` ships generic polling primitive with exponential schedule (1s → 2s → 5s → 10s → 30s on consecutive errors), AbortController cleanup on unmount, and surfaced `errorCount`/`lastError`/`nextRetryIn`/`loading`. Migrated `useLiveMonitor`, `usePaperPortfolio`, `useSessionStatus` to use it (eliminates ad-hoc setInterval loops). `useBacktest`/`useOptimize` keep their custom poll loops because they're job-completion driven (one-shot to terminal status, not steady-state polling) — `useBackoffPoll`'s mental model is wrong for them. 5 new unit tests; 127/127 vitest green.
- **D-3.4-rust (🟢)** — `rust/src/engine/tx_costs.rs` ports the three `TxCostModel` venue variants (Solana / Hyperliquid / Cex) from Python. PyO3 exposes them as `flint_core.TxCostModel` with `for_venue`, `solana`, `hyperliquid`, `cex` static constructors and an `estimate(market, size, price, urgency) → dict` method. Rust capability flag `supports_tx_costs` flipped to `true`. 14 cargo tests; 13 Python↔Rust parity tests pinned to 1e-9 tolerance covering default/urgent/custom-fee/edge-case (zero size, $1 B notional). Micro-benchmark: ~2.24× speedup on the tight 200k-iteration loop (FFI overhead dominates the single-call benchmark; the bigger win is avoiding the cross-boundary hop when the Rust engine loop calls it per fill).
- **Wave 1 complete (6/6).** All first cuts shipped. Step 2–7 of D-2.1.b (CashManager / FillRecorder / Risk split) remain scoped as Wave 2+ follow-up.
- **Wave 2 in progress.** D-3.3-maker-detection (🟢) shipped early — Rust `FillResult` now carries `is_maker` and the pending-limit fill path tags it `true`; the fee model routes through `compute_fee_with_role`. PyO3 `RustEngine` constructor accepts `fee_model="drift"/"hyperliquid"/"maker_taker"` so callers can observe the rebate end-to-end (Drift -2 bps maker rebate, HL 1 bp maker). 6 new behavioral tests; 163/163 Rust-side regression green.
- **D-3.1-rust (🟢)** — `rust/src/engine/orderbook_fill.rs` ports the `OrderbookFillModel._walk_book` algorithm: side-dependent level walk, depth-rejection gate, VWAP accumulation, signed `impact_bps = (vwap − mid) / mid × 10_000`. PyO3 class `flint_core.OrderbookFiller(reject_on_insufficient_depth=True)` exposes `walk_market(side, size, bids, asks) → dict|None`. Capability flag `supports_orderbook_walk` flipped to `true`. 9 cargo tests + 13 Python↔Rust parity tests across long/short/partial/reject/empty-book/impact-sign. ~3.52× speedup at 20 levels/side, 100k iterations. 176/176 Rust-suite regression green.
- **D-2.1.b Step 2 (🟡 — 2/7)** — `flint/execution/cash_manager.py:CashManager` now owns `_cash`, the optional `_allocator`, and the three running counters (`total_fees`, `total_tx_costs`, `total_funding`). BacktestContext composes a `_cm` and exposes the legacy attribute names as read+write properties so the 20+ existing call sites with `self._cash -= x` / `self._total_fees += f` keep routing through the manager unchanged. 16 unit tests covering both no-allocator and allocator paths, counters, and legacy-alias compound assignments. 134/134 BacktestContext-using regression tests still green. Steps 3–7 deferred.
