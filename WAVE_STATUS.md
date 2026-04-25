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
| D-2.1.b | 🟡 | 2w | claude | 2026-04-24 | Step 1 of 7 shipped: `PositionManager` extracted, BacktestContext composes via legacy dict aliases · Steps 2–7 (CashManager, FillRecorder, etc.) deferred |
| D-3.4-rust | 🔴 | 1w | TBD | 2026-04-24 | Rust port; needs cargo + PyO3 work |
| D-5.1-ruff | 🟢 | 1d | claude | 2026-04-24 | Auto-fix sweep done · ruff configured to F-class only · CI hard-fails on `ruff check` |
| D-4.7-full | 🟢 | 3d | claude | 2026-04-24 | Services layer (strategies/backtest/journal/data/paper) shipped · MCP backtest+journal in-process · 12 standalone tests |
| D-1.4-ui | 🔴 | 3d | TBD | 2026-04-24 | Multipart CSV upload + UI panel for reconciliation |
| D-4.2-backoff-full | 🔴 | 3d | TBD | 2026-04-24 | Generic `useBackoffPoll<T>` hook + migrate 5 hooks |

## Wave 2 — weeks 4–5

| ID | State | Effort | Prereq | Notes |
|---|---|---|---|---|
| D-2.1.d | ⏭️ | 1w | D-2.1.b | Separate `PaperContext` |
| D-3.5-orchestrator | ⏭️ | 1w | D-2.1.b | `PortfolioMarginEngine` in BacktestContext |
| D-2.1.c | ⏭️ | 1w | D-2.1.b + testnet secrets | Merge LiveContext + LiveExecutionContext |
| D-3.1-rust | ⏭️ | 3d | D-3.4-rust | Rust `OrderbookFiller` |
| D-3.3-maker-detection | ⏭️ | 2d | D-3.4-rust | Maker/taker role tracking |

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
- **Next:** D-1.4-ui (multipart CSV upload + reconciliation panel) — last Wave 1 unblocker.
