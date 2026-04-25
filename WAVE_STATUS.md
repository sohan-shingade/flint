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
| D-2.1.b | 🟢 | 2w | claude | 2026-04-24 | All 7 state-extraction steps + caller-site migration shipped. Every state mutation in BacktestContext now goes through one of the seven managers (`_pm.set/delete/record_close`, `_cm.debit/credit/add_*`, `_fr.record/log`, `_oq.add_pending/cancel*/drain_market`, `_fl.add`, `_bl.record*/add_paid`, `_mdf.add_*`). Public `borrow_payments` property added so engine.py no longer reaches into private state. 262/262 regression green; legacy aliases retained for tests but no longer used internally |
| D-3.4-rust | 🟢 | 1w | claude | 2026-04-24 | `engine/tx_costs.rs` + PyO3 `TxCostModel` class · 14 cargo tests · 13 parity tests (1e-9) · Rust 2.24x faster on the tight loop · `supports_tx_costs = true` in Rust capabilities |
| D-5.1-ruff | 🟢 | 1d | claude | 2026-04-24 | Auto-fix sweep done · ruff configured to F-class only · CI hard-fails on `ruff check` |
| D-4.7-full | 🟢 | 3d | claude | 2026-04-24 | Services layer (strategies/backtest/journal/data/paper) shipped · MCP backtest+journal in-process · 12 standalone tests |
| D-1.4-ui | 🟢 | 3d | claude | 2026-04-24 | POST /paper/{id}/reconciliation accepts multipart CSV · PaperTrading.tsx Reconcile button + results panel · 6 endpoint tests |
| D-4.2-backoff-full | 🟢 | 3d | claude | 2026-04-24 | `useBackoffPoll<T>` hook with 1→2→5→10→30s schedule + AbortController · `useLiveMonitor`, `usePaperPortfolio`, `useSessionStatus` migrated · 5 unit tests · 127/127 vitest green |

## Wave 2 — weeks 4–5

| ID | State | Effort | Prereq | Notes |
|---|---|---|---|---|
| D-2.1.d | ⏭️ | 1w | D-2.1.b | Separate `PaperContext` |
| D-3.5-orchestrator | 🟢 | 1w | D-2.1.b ✓ | `flint/risk/portfolio_orchestrator.py:PortfolioMarginEngine` composes MarginEngine + VenueAllocator + PortfolioRiskEngine into one pre-trade check facade · BacktestContext.market_order routes through it · 16 tests + 207-test regression green |
| D-2.1.c | ⏭️ | 1w | D-2.1.b + testnet secrets | Merge LiveContext + LiveExecutionContext |
| D-3.1-rust | 🟢 | 3d | D-3.4-rust ✓ | `engine/orderbook_fill.rs` walks bids/asks for VWAP fill · PyO3 `OrderbookFiller` · 9 cargo + 13 parity tests (1e-9) · 3.52x speedup over Python |
| D-3.3-maker-detection | 🟢 | 2d | D-3.4-rust ✓ | Shipped: `FillResult.is_maker` flag, resting-limit path tags maker=true, `compute_fee_with_role` replaces `compute_fee`, `RustEngine(fee_model="drift"/"hyperliquid"/"maker_taker")` exposed — 6 maker-rebate tests green |

## Wave 3 — weeks 6–9

| ID | State | Effort | Prereq | Notes |
|---|---|---|---|---|
| D-6.5-api | ⏭️ | 3w | D-2.1.c + secrets | `/api/v1/live/start` two-step confirmation |
| D-4.3-websocket | 🟡 | 1w | D-4.2-backoff-full ✓ | Both pages bound. Endpoints + ConnectionManager + useWebSocket hook + paper tick/trade + live fill broadcasts shipped (slices 1+2+2b). UI: PaperTrading.tsx (slice 3) and LiveMonitor.tsx (slice 4) both subscribe to their session's WS. Paper overlays live equity / unrealized PnL / trade-count on the polled snapshot. Live merges WS fills into polled fills (deduped by order_id+ts) so the fills tape updates without waiting for next poll. Both pages show `WS LIVE`/`CONNECTING`/`OFFLINE` indicator. 23 backend + 6 hook tests + 133/133 vitest + vite build clean. Drop-polling-entirely migration deferred |
| D-6.6-proof | ⏭️ | 1w | D-1.4-ui + D-6.5-api | Funding dislocation arb proof notebook |

## Wave 4 — weeks 10–14

| ID | State | Effort | Prereq | Notes |
|---|---|---|---|---|
| D-6.7-jito | ⏭️ | 2w | D-6.5-api | Real Jito bundle integration |
| D-6.1-unified | 🟡 | 3w | D-2.1.b ✓ + D-3.5-orchestrator ✓ | Foundation shipped: `flint/portfolio/shared_engine.py:SharedCapitalPortfolioEngine` runs N strategies on one shared `BacktestContext` (one cash pool, one position book, one orchestrator gauntlet). Strategy attribution via `strat:bt-N` order_id tagging. 8 tests; 270/270 regression green. Refinements (per-strategy capital caps, dollar-neutral rebalancing, attribution-by-trade-not-fill) deferred |

## Wave 5 — weeks 14–17

| ID | State | Effort | Prereq | Notes |
|---|---|---|---|---|
| D-6.4-replay | 🟡 | 2-3w | D-2.2-internal ✓ | Slices 1+2+3+4 shipped: event log writer/reader + `BookState` fold/replay + snapshot compaction + BacktestContext writer hooks. End-to-end parity verified: replay over the live-emitted log reproduces final state byte-for-byte. 59 tests across the four replay test files. Time-travel UI (slice 5) deferred |

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

- **2038 pass · 7 skip · 0 fail** as of 2026-04-25 (post-Wave-3 sweep)
- Skipped suites: `test_ccxt_live`, `test_hyperliquid_live`, `test_wallet`,
  `test_connectors`, `test_hyperliquid_client` — all missing-dep failures
  on the editable install (`ccxt`, `eth_account`, `solders` not present).
  None are code regressions.
- 4 smoke regressions (BUG-1..4) from earlier in session all captured by
  red→green tests.

---

## Active branch

`restructure` — 28 commits ahead of `main`, pushed to `origin/restructure`.
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
- **D-2.1.b Step 3 (🟡 — 3/7)** — `flint/execution/fill_recorder.py:FillRecorder` owns `_fills` (recorded fill list) and `_log_messages` (diagnostic log). BacktestContext composes a `_fr` and exposes both as read-only property aliases that return the underlying mutable list, so the existing `self._fills.append(...)` and `self._log_messages.append(...)` call sites keep working without migration. 10 unit tests; 127/127 regression on context-using paths green.
- **D-2.1.b Step 4 (🟡 — 4/7)** — `flint/execution/order_queue.py:OrderQueue` owns the resting-order list (`_pending_orders` for limits/stops/TPs) and the this-bar market queue (`_market_orders_queue`). Surface includes `add_pending(order) → bool` (returns False at the 100-order cap), `cancel(id)`, `cancel_all(market=None)`, `add_market`, `drain_market` (atomic swap). BacktestContext exposes both lists as read+write property aliases so existing call sites that reassign (`self._pending_orders = [filtered list]` in `cancel_all` and `process_pending_orders`) keep working. 16 unit tests covering append/cap/cancel/drain/reassignment + integration. 134/134 regression on context-using paths green.
- **D-2.1.b Step 5 (🟡 — 5/7)** — `flint/execution/funding_ledger.py:FundingLedger` owns the per-market history dict and the per-venue split. Strategy-facing helpers (`latest`, `recent`, `by_venue`, `venue_snapshots`) replace the inline implementations in BacktestContext; the four `get_funding_*` methods became one-line delegations. Read-only property aliases `_funding_history` / `_venue_funding` preserve legacy peek-into-internals access patterns. 13 unit tests; 125/125 regression on funding-using paths green.
- **D-2.1.b Step 6 (🟡 — 6/7)** — `flint/execution/borrow_ledger.py:BorrowLedger` owns the Jupiter Perps borrow-rate history dict, the running `total_paid` counter, and the per-trade `payments` ledger that `_apply_fill` writes into for tearsheet attribution. Surface: `record(snapshot)`, `latest(market)`, `recent(market, lookback)`, `cumulative_at(market, ts)`, `record_payment(p)`, `add_paid(amount)`. BacktestContext exposes legacy aliases (`_borrow_history`, `_borrow_payments` read-only; `_total_borrow_paid` read+write) so `self._total_borrow_paid += borrow_cost` compound assignments still work. 13 unit tests; 98/98 regression green.
- **D-2.1.b Step 7 (🟡 — 7/7 state extracted)** — `flint/execution/market_data_feed.py:MarketDataFeed` owns the three market-data caches: `market_histories` (cross-market candle access), `orderbook_history`, `oi_history`. Strategy-facing helpers (`set_histories`, `candles`, `markets`, `add_orderbook`, `latest_orderbook`, `add_open_interest`, `latest_oi`, `oi_recent`) replace the inline implementations in BacktestContext; `set_market_histories`, `get_candles`, `markets`, `add_orderbook_snapshot`, `get_orderbook`, `add_open_interest`, `get_open_interest`, `get_open_interest_history` collapsed to one-line delegations. Read+write property alias on `_market_histories` (engine sometimes reassigns), read-only on `_orderbook_history` and `_oi_history`. 12 unit tests; 176/176 regression green. **All 7 state-extraction steps for D-2.1.b are shipped.** The next work (caller-site migration to explicit `self._cm.debit(...)` / `self._fr.record(...)` etc., and trimming BacktestContext to <300 LOC) is a sibling PR — the manager surfaces are already in place to support it.
- **D-3.5-orchestrator (🟢)** — `flint/risk/portfolio_orchestrator.py:PortfolioMarginEngine` is the single pre-trade check facade. Composes `MarginEngine` (venue-level margin/leverage), `VenueAllocator` (per-venue available cash), and `PortfolioRiskEngine` (book-level gross/net/concentration/VaR). `check_order(order, cash, positions, price, equity) → PortfolioCheck(approved, reason, component)` runs the three checks in priority order (allocator → margin → portfolio); first failure short-circuits and `component` carries the source so the BacktestContext warn-line names which engine vetoed. BacktestContext now constructs `self._pme` always (None-tolerant when no engines supplied), and `market_order` replaces the inline margin block with one orchestrator call. New `portfolio_risk` ctor kwarg threads a `PortfolioRiskEngine` through. 16 unit tests covering no-op pass, priority order, BacktestContext integration (component-tagged log line, reduce_only bypass), edge cases. 207/207 regression green; ruff clean.
- **D-6.4-replay foundation (🟡)** — `flint/portfolio/event_log.py:EventLogWriter` / `EventLogReader` ship the append + read primitives behind a new `portfolio_events(session_id, seq, ts, kind, payload)` DuckDB table. Writer is thread-safe (8-thread concurrent-append test produces a dense 0..N-1 sequence), assigns monotonic per-session seq via in-memory cache backed by `MAX(seq)+1` on cold start, and exposes `append(...)` + `append_many(...)`. Reader exposes `read_all`, `read_until(target_ts)`, `count`, `latest_seq`. Six event kinds defined (`order.submit/cancel`, `fill`, `liquidation`, `funding`, `borrow`). 15 tests covering schema idempotency, sequence integrity, payload JSON round-trip, cross-process seq recovery, thread safety.
- **D-6.4-replay slice 2 — replay primitive (🟡)** — `flint/portfolio/replay.py` ships pure-Python `fold(events, initial_capital, seed=None) → BookState` + storage-backed `replay(store, session_id, target_ts, initial_capital, use_snapshot=True)`. `BookState` carries cash, initial_capital, total_fees / total_tx_costs / total_funding / total_borrow_paid, positions dict (per (venue, market)), realized_pnl, fill_count, liquidation_count, order submit/cancel counters. Fill folder handles open / DCA / partial close / full close / flip cases identically to `BacktestContext._apply_fill`'s post-migration code. Funding, liquidation, borrow folds match the engine's cash-debit semantics. Unknown event kinds ignored for forward compat. `BookState.equity_at(prices)` computes equity given a mark map. 17 tests covering every fold case + storage round-trip + per-session isolation + determinism + a full-lifecycle scenario (open → funding → borrow → close → final cash).
- **D-6.4-replay slice 3 — snapshot compaction (🟡)** — `flint/portfolio/snapshots.py:SnapshotStore` writes `BookState` JSON to a new `portfolio_snapshots(session_id, seq, ts, payload)` table. `latest_before(session_id, target_ts)` returns `(seq, ts, state)` for the most recent snapshot at-or-before the target. `EventLogReader.read_after_seq(session_id, after_seq, target_ts)` fetches only the tail. `replay(..., use_snapshot=True)` (default) fast-forwards: `latest_before` → `read_after_seq` → `fold(seed=snapshot)` — large session replays now skip the early-event scan. `should_compact(events_since_last, every_n_events=10_000)` predicate for engine hooks (slice 4) to call. `INSERT OR REPLACE` on the snapshot upsert lets the compactor re-run idempotently. 15 tests including the load-bearing parity assertion (`use_snapshot=True` and `use_snapshot=False` produce identical state on the same target_ts), seed-fold semantics, latest_before edge cases (target before first snapshot, exact-match, past-end), and JSON round-trip with multiple positions.
- **D-4.3-websocket foundation (🟡)** — `ConnectionManager` extended with monotonic per-channel `seq` stamping on every broadcast + a 500-deep ring buffer of recent envelopes per channel + `connect(..., since_seq=N)` that replays buffered events with `seq > N` before streaming live. `ping(channel)` broadcasts a `{type: "ping"}` heartbeat. New per-session FastAPI routes `/ws/paper/{session_id}` + `/ws/live/{session_id}` map to channels `paper:{id}` / `live:{id}`; query param `?since=<seq>` opts into replay. Legacy `/ws/{channel}` route kept for compat. UI `ui/src/hooks/useWebSocket.ts` hook with the same exponential schedule as `useBackoffPoll` (1s → 2s → 5s → 10s → 30s on consecutive errors) + 30s heartbeat-stale detection (forces reconnect when no message arrives in 30s, even without an explicit error). Replay loop: hook tracks the highest seq seen across reconnects and passes it as `?since=` on the next attempt. 10 backend tests covering broadcast / per-channel seq isolation / all-channel routing / dead-socket pruning / replay-on-reconnect (with + without since) / ring-buffer cap / heartbeat ping / connection introspection. 127/127 vitest still green; vite build clean. Engine-side broadcast hooks (paper/live engines emitting equity/fill ticks to their channels) and migration of `useLiveMonitor`/`usePaperPortfolio`/`useSessionStatus` from polling to WS are deferred to follow-on slices.
- **D-6.4-replay slice 4 — engine writer hooks (🟡)** — `BacktestContext.__init__` accepts optional `event_log_writer` + `event_session_id` kwargs. When both are set, `_emit(kind, payload, ts=...)` appends to the log; when either is unset, emit short-circuits to a no-op (zero overhead on legacy paths). Hooks emit on: `order.submit` (market_order, limit_order, stop_order, take_profit_order — all four order entrypoints), `order.cancel` (cancel and cancel_all when count > 0), `fill` (every `_apply_fill` call, before position mutation so replay sees opens/closes in order), `funding` (every `apply_funding` payment), `liquidation` (every margin-engine-triggered force-close), `borrow` (every `_realize_jupiter_borrow_cost` debit). 12 tests covering: zero-overhead legacy path (no log writer), zero-overhead writer-without-session-id path, every order kind emits with the right payload, cancel emits only on success, fills carry full payload, **end-to-end parity** — replay over the live-emitted log produces state matching `BacktestContext.account.cash` byte-for-byte across open/close, DCA, partial intermediate-state queries. 329/329 regression green across all backtest/portfolio/replay test files. ruff clean.
- **D-6.1-unified foundation (🟡)** — `flint/portfolio/shared_engine.py:SharedCapitalPortfolioEngine` runs multiple strategies against **one** shared `BacktestContext`, so cash, fees, funding, borrow, and the orchestrator's pre-trade margin gauntlet all see the actual book. Each strategy gets a `_TaggedContextProxy` that prefixes its order_ids with `strategy_name:` so fills carry attribution; per-strategy proxies forward all reads (account/positions/funding/orderbook) to the shared context, so a strategy looking at "positions" sees the *whole book* — that's the point. `cancel_all` on a proxy only cancels orders owned by that strategy. 8 unit tests covering construction, shared-pool routing, order tagging, proxy forwarding, isolation. 270/270 regression green. Out of scope for this slice: per-strategy capital caps, dollar-neutral rebalancing, attribution by trade rather than by fill (closed-trades currently split PnL evenly across strategies that touched that market — refinement is a follow-on PR).
- **D-2.1.b caller-site migration (🟢)** — every state-mutating call site in BacktestContext now goes through the right manager. Migrated:
  * `apply_funding` → `_pm.items()` + `_cm.debit/add_funding`
  * `check_liquidations` → `_pm.get/delete/record_close`, `_cm.credit/add_fee`, `_oq.pending = filtered`, `_fr.log`
  * `process_pending_orders` → `_oq.pending` (read+set), `_fr.record` via `_apply_fill`
  * `process_market_orders` → `_oq.drain_market()` atomic swap, `_fr.log`, `_add_pending_or_warn`
  * `_apply_fill` → factored a `_realize_jupiter_borrow_cost` helper + a `_new_position` helper; main body is now ~80 lines of pure routing through `_fr.record / _cm.debit/credit/add_fee/add_tx_cost / _pm.set/get/delete/record_close / _bl.add_paid/record_payment`
  * `close_all_positions` → `_pm.keys/get`
  * `set_candle` → `_pm.update_pnl_for_market` + `_mdf.history_for`
  * `account` / `positions` / `pending_orders` → manager-direct reads
  * `total_*` properties → manager-direct reads
  * `venue_balance/balances/transfer/process_transfers` → `_cm.available/balances/allocator`
  * Internal `_debit_cash` / `_credit_cash` helpers deleted (every caller now uses `_cm` directly)
  * Public `borrow_payments` property added so `flint/backtest/engine.py` no longer reaches into `ctx._borrow_payments` private state.
  Legacy property aliases (`_cash`, `_positions`, `_fills`, etc.) retained for now because tests still exercise them deliberately. 262/262 regression green across backtest/multi-venue/jupiter/funding/paper paths; ruff clean.
