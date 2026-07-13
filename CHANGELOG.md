# Changelog

User-visible changes per release. For per-commit detail see `git log` or
the GitHub release notes (linked from each entry).

Versioning: `MAJOR.MINOR.PATCH` — minor bump on breaking API change,
patch on additive features and fixes.

---

## [2.0.1] — 2026-07-12

Paper trading for embedders: user-source sessions + headless (poll-driven)
advance. [Release notes](https://github.com/sohan-shingade/flint/releases/tag/v2.0.1).

**Added**
- `services.start_paper_source` / `resume_paper_source` — paper sessions
  over submitted user *source*, mirroring `run_backtest_source`: the
  sandbox/lint gate runs first (invalid source raises a structured
  `SourceValidationError` carrying the `ValidationReport`), and every
  engine step of the returned `SandboxedPaperSession` runs inside the
  OS-isolated sandbox child (D25) — untrusted code never executes in the
  embedding process. Proven bit-for-bit identical to the in-process
  template session's event stream.
- `PaperSession.catch_up(now_ms)` + `LiveFeed.replay_to(now_ms)` —
  advance a paper session with no live WS connection: bars closed since
  the cursor replay from the injected `GapSource` through the same
  engine loop as a reconnect (never skipped, never forward-filled; lake
  shortfalls are degraded `GapRecovery`s and retried). The first call
  anchors the cursor "from now" and persists it in the run head
  (`cursor_bar_start`), so `resume` → `catch_up` survives a restart even
  before any bar was processed.
- `warm_state` — the one shared §6.7 event-log → portfolio fold, used by
  both the in-process session and the sandbox child.

**Fixed**
- Stale `flint.adapters` docstrings claimed the durable user store was
  still pending; they now point at `flint.data.store.DuckDBUserData(path)`
  (ships since the data layer landed), and paper restart-safety over it
  is covered by a test.

---

## [2.0.0] — 2026-07-07

Ground-up rewrite (the `redesign/greenfield` build). [Release notes](https://github.com/sohan-shingade/flint/releases/tag/v2.0.0).

**Changed (BREAKING — new architecture, new APIs)**
- Ports-and-adapters architecture: `core/` → `engine/` → `services/` →
  surfaces (`api/`, `sdk/`, `mcp_srv/`, `agent/`). Surfaces talk only to
  `services/`; every service call takes a `TenantContext`.
- Nautilus Trader is the only simulation substrate — the legacy bar
  engine and the Rust (`rust/flint_core`) engine are deleted. Bar
  strategies run via the bar-lane shim; tick strategies (`TickStrategy`)
  get native L2 matching.
- Tick-data foundations: Tardis vendor adapter, live tick recorder
  (BBO quotes + predicted funding), BOOK_DELTA streaming, coverage
  ledger, granularity tiers with structured rejections.
- Funding is a hard gate: backtests over windows without real funding
  data are rejected with available ranges — never zero-filled.
- No synthetic data anywhere (D26): tests use hand-authored inputs or
  real recorded fragments.
- User strategy code runs in an OS-isolated sandbox subprocess (D25);
  the AST allowlist is lint-grade UX, not the security boundary.
- New web UI (v1 shell), templates registry, funding lab, HL live
  executor with caps + kill switch, one-shot legacy v1.x DuckDB importer.

---

## [1.5.4] — 2026-04-25

Fix the `"drift"` hardcode flagged externally. [Release notes](https://github.com/sohan-shingade/flint/releases/tag/v1.5.4).

**Changed (BREAKING-ish — default behavior flip)**
- `flint.config.FlintConfig.default_venue` field added (default
  `"hyperliquid"`, override via `FLINT_DEFAULT_VENUE` env var or
  `flint.yaml`). Drift is offline post-hack so the default flips
  away from it. Pass `venue="drift"` explicitly anywhere you want
  the old behavior (e.g. when Drift returns).
- `flint.config.default_venue()` resolver — single source of truth.
- 7 call sites flipped from hardcoded `"drift"` to lazy
  `default_venue()` resolution: `PaperContext.__init__`,
  `PaperSession.__init__`, `PaperTradingEngine.start_session`,
  `deploy_session`, `resume_sessions` (recovers persisted venue with
  default-fallback), `paper.py` request schema + body parser,
  `backtest.py` calibration body parser.
- `flint/models.py:OpenInterest.venue = "drift"` left as-is (provider-
  specific data class default for the Drift OI provider; not a call-
  site default for trading APIs). Documented inline.

**Tests**
- Two existing tests that asserted `("drift", "SOL-PERP") in ctx._pm`
  on default-constructed `PaperContext` now pass `venue="drift"`
  explicitly. Test intent unchanged.

---

## [1.5.3] — 2026-04-25

Three open polish items shipped together. [Release notes](https://github.com/sohan-shingade/flint/releases/tag/v1.5.3).

**Added**
- Drift-S3 backfill fallback chain — `backfill_candle_gap` walks
  Hyperliquid → Drift API → Drift S3 so resume catches up via HL when
  Drift is offline. 6 new tests.
- Per-strategy Sharpe / drawdown / equity curve on
  `SharedPortfolioResult`. Initial-capital share renormalized from
  `strategy_capital_caps` (or equal-split). 3 new tests.
- Reconciliation UI bps histogram. `reconcile()` emits
  `price_bps_histogram` (8 log-scale buckets); `PaperTrading.tsx`
  renders inline bars with red bins ≥ 10 bps (CI threshold). 3 new tests.

**Tests:** 2191/2198 (+12 vs v1.5.2). Ruff clean. Vitest 139/139.

---

## [1.5.2] — 2026-04-25

Doc-only release. README rewrite per Apr 2026 product review feedback.
[Release notes](https://github.com/sohan-shingade/flint/releases/tag/v1.5.2).

**Changed**
- README: 548 → 222 lines (-60%). Wedge-first, proof-first.
- Cut: vs-DeFi-tools comparison table, vs-Freqtrade/Hummingbot table,
  CCXT 100+ exchanges section, MEV row, "cross-venue arb" headline.
- Added: Drift offline callout, Proof section (8-row claim → artifact
  map), explicit "What this is NOT" line, honest Limitations section.

---

## [1.5.1] — 2026-04-25

D-6.1 refinements + 4th proof notebook + D-2.1.c structural prep + 2
pre-existing test fixes. [Release notes](https://github.com/sohan-shingade/flint/releases/tag/v1.5.1).

**Added**
- Per-strategy capital caps on `SharedCapitalPortfolioEngine`
  (`strategy_capital_caps={"alice": 0.4}`). Reduce-only orders bypass.
- Attribution-by-trade — `_Position.entry_order_id` field; closed-trade
  dicts now carry both `entry_order_id` and `exit_order_id`. Engine
  attribution falls through tagged-exit → tagged-entry → per-market
  even split (last-resort, never fires post-fix unless a trade lands
  outside the proxy path).
- Portfolio dollar-imbalance accessor:
  `SharedCapitalPortfolioEngine.dollar_imbalance(ctx) → float`.
- 4th proof notebook: `notebooks/multi_venue_funding_arb.py` — proves
  D-2.1.d structural correctness (per-leg attribution to 1e-6).
- `LiveExecutionContext` composes 7 managers (placeholder slice,
  empty containers; full wiring needs testnet credentials).

**Fixed**
- `BacktestContext.apply_funding` now filters by `funding_rate.source`
  with back-compat fallback. Pre-existing bug (Drift rate would book
  against HL legs in same session) discovered while writing the proof.
- `tests/test_smoke_regressions.py::test_engine_used_key_present_in_results`
  pre-seeds synthetic candles instead of hitting offline Drift S3.
- `tests/test_jupiter_integration.py::test_borrow_cost_reduces_equity`
  forces `ClosePriceFill` on both engines so slippage profile matches
  between borrow / no-borrow runs.

---

## [1.5.0] — 2026-04-25

D-2.1.d PaperContext unification — minor bump because public classes
removed. [Release notes](https://github.com/sohan-shingade/flint/releases/tag/v1.5.0).

**Removed (BREAKING)**
- `flint.execution.paper_broker.PaperBroker`
- `flint.execution.live_context.LiveContext`

External code constructing either must migrate to
`flint.paper.context.PaperContext` (same constructor kwargs, same
method surface).

**Added**
- `flint/paper/context.py:PaperContext` — composes the same 7 managers
  as `BacktestContext` post-D-2.1.b. Positions keyed by
  `(venue, market)` tuples so same-market opposite-leg multi-venue arb
  works. 17/17 multi-venue tests pass.

**Schema migration**
- `paper_positions` PK widened from `(session_id, market)` to
  `(session_id, venue, market)`. Migration mirrors the existing
  candles/orderbook venue-column upgrade pattern; idempotent on re-run.

---

## [1.4.2] — 2026-04-25

Multi-venue paper funding correctness (Option A) + WS hybrid hooks.
[Release notes](https://github.com/sohan-shingade/flint/releases/tag/v1.4.2).

**Fixed**
- `PaperBroker.apply_funding(market, rate, mark_price, venue=None)`
  takes optional `venue=` filter. Multi-venue paper sessions now query
  per venue per tick — a Drift rate no longer books against an HL leg.
  7 new tests, 39/39 paper-suite green.
- `paper_funding_payments` table: `venue` column added, PK widened to
  `(session_id, market, venue, ts)`.

**Added**
- `useHybridPoll<TPoll, TWs>` hook — WS-primary, polling secondary at
  30s when WS healthy, 2s when dead. `useSessionStatus` and
  `useLiveMonitor` migrated to it. `usePaperPortfolio` stays poll-only
  (no aggregate WS channel exists). 6 new tests, 139/139 vitest green.

---

## [1.4.1] — 2026-04-25

Price-source fallback chain hotfix.
[Release notes](https://github.com/sohan-shingade/flint/releases/tag/v1.4.1).

**Fixed**
- Live PnL ticker no longer dies when Drift is offline. Replaced single-
  source DLOB poll with a fallback chain: Hyperliquid → Drift DLOB →
  DuckDB last-candle (marks quotes `stale=True`).

**Added**
- `flint/paper/price_sources/` — pluggable `PriceSource` ABC with 4
  built-in implementations (`HyperliquidInfoSource`, `DriftDLOBSource`,
  `LocalDuckDBSource`, `default_chain(store)`).
- `GET /api/v1/system/price-sources` — per-source health for
  ConnectionBanner-style UX.
- 29 new tests (17 source unit + 10 chain semantics + 2 endpoint).

---

## [1.4.0] — 2026-04-25

The big restructure. [Release notes](https://github.com/sohan-shingade/flint/releases/tag/v1.4.0).

**Added**
- Trust artifacts: parity report pipeline + CI gate, reconciliation
  tooling, PIT audit, deterministic seeds, 3 proof notebooks.
- Phase 2 cleanup: `BacktestContext` 7-manager decomposition (D-2.1.b
  full close — 7 state-extraction steps + caller-site migration).
- Phase 3 depth: Rust `TxCostModel` (2.24× speedup), Rust
  `OrderbookFiller` (3.52× speedup), maker/taker fee detection,
  `PortfolioMarginEngine` orchestrator.
- Phase 4 polish: WebSocket per-session endpoints with replay buffer +
  `useWebSocket` hook + heartbeat detection. `useBackoffPoll` hook
  with exponential schedule.
- Phase 5: ruff hard-fail on F-class. CI matrix + parity gate +
  sandbox escape tests.
- Phase 6: `SharedCapitalPortfolioEngine` (foundation),
  `D-6.4-replay` event-sourcing all 5 slices, auto-compaction, E2E
  parity, Rust ports of FundingLedger + BorrowLedger.

---

## Earlier

See `git log v1.3.1..v1.4.0` for detailed commit history of pre-restructure
releases. Significant earlier milestones:

- **1.3.1** — bumped after restructure-branch close
- **1.3.0** — `turnover_annualized` + `calmar_ratio` metrics, MCP + API
  gap fixes
- **1.2.x** — paper trading refactor, MCP integration
