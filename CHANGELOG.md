# Changelog

User-visible changes per release. For per-commit detail see `git log` or
the GitHub release notes (linked from each entry).

Versioning: `MAJOR.MINOR.PATCH` — minor bump on breaking API change,
patch on additive features and fixes.

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
