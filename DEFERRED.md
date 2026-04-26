# Deferred Work

Tracker for items scoped in a phase but split into sibling PRs because
bundling would make review infeasible or because validation needs something
the in-session run couldn't provide (testnet access, UI smoke, real data).

Rule: **an item sits here until it has an owner + ETA, then it moves to an
active phase item.** Nothing leaves Flint's todo surface silently.

Updated: 2026-04-25 (post-v1.5.2 — full deferred-queue refresh; 14 items
closed since 2026-04-24, only the testnet-blocked live-trading chain
remains).

---

## Closed since 2026-04-24

**Phase 1 tail** — all shipped:

- ✅ **D-1.1.b** — Rust `close_all()` accepts a `FeeModel` and charges the
  synthetic exit fill (was `fee=0.0`).
- ✅ **D-1.2-CI** — `.github/workflows/parity.yml` shipped in Phase 5.3.
- ✅ **D-1.3-providers** — 22 provider modules declared `PIT_METADATA`.
  Audit re-run: 26/26 ✓.
- ✅ **D-1.4-api** — `GET /api/v1/paper/{session_id}/reconciliation`
  returns engine-side fill summary.
- ✅ **D-1.4-ui** — `POST /api/v1/paper/{id}/reconciliation` accepts
  multipart CSV, parses via `parse_venue_fills_csv_text`, runs
  `reconcile()`. PaperTrading.tsx "RECONCILE FILLS" button + results
  panel mirrors the parity report layout. 6 endpoint tests.
- ✅ **D-1.5-data-pins** — `docs/how-to/pin-notebook-fixtures.md`.
- ✅ **D-1.6-byo-fills** — `CustomCSVProvider(table="fills")`.

**Phase 2 tail** — all shipped:

- ✅ **D-2.1.b** — All 7 state-extraction steps shipped + caller-site
  migration. BacktestContext now composes PositionManager, CashManager,
  FillRecorder, OrderQueue, FundingLedger, BorrowLedger, MarketDataFeed.
- ✅ **D-2.1.d** — `flint/paper/context.py:PaperContext` ships in v1.5.0.
  Replaces `PaperBroker` + `LiveContext` (both deleted). Positions keyed
  by `(venue, market)` so same-market opposite-leg multi-venue arb works.
  Schema migration on `paper_positions`.
- ✅ **D-2.2-internal** — `journal/storage.py` + `paper/session_store.py`
  migrated off raw `_store._conn` via `FlintStore._sql_*` wrappers.
- ✅ **D-2.4.b** — `/api/v1/backtest/run` routes user-supplied inline code
  through `run_strategy_in_sandbox`.

**Phase 3 tail** — all shipped:

- ✅ **D-3.1-rust** — `engine/orderbook_fill.rs` PyO3 `OrderbookFiller`
  with VWAP walk + impact_bps. 9 cargo + 13 parity tests (1e-9). 3.52×
  speedup.
- ✅ **D-3.3-maker-detection** — `FillResult.is_maker` flag, resting-
  limit path tags maker=true, `compute_fee_with_role`, `RustEngine(
  fee_model="drift"|"hyperliquid"|"maker_taker")`.
- ✅ **D-3.4-rust** — `engine/tx_costs.rs` PyO3 `TxCostModel`. 14 cargo
  + 13 parity tests. 2.24× speedup.
- ✅ **D-3.5-orchestrator** — `flint/risk/portfolio_orchestrator.py:
  PortfolioMarginEngine` composes MarginEngine + VenueAllocator +
  PortfolioRiskEngine; BacktestContext.market_order routes through it.

**Phase 4 tail** — all shipped:

- ✅ **D-4.1-wedge** — README hero rewritten around the perp-lab wedge.
  Further refresh in v1.5.2 (548 → 222 lines, proof-first restructure).
- ✅ **D-4.2-backoff** — silent catches logged.
- ✅ **D-4.2-backoff-full** — `useBackoffPoll<T>` ships with the
  1s → 2s → 5s → 10s → 30s schedule + AbortController. 3 hooks migrated.
- ✅ **D-4.3-websocket** — Endpoints + ConnectionManager + replay
  buffer + `useWebSocket` hook + paper/live broadcasts. v1.4.2 closed
  the wave with `useHybridPoll` (WS-primary, polling at 30s when WS
  healthy, 2s when dead) — explicitly supersedes the "drop polling
  entirely" path because a single WS bug with no fallback = dead UI.
- ✅ **D-4.5-ui** — `useBacktest.cancel()` + auto-POST `/cancel` on
  unmount. BacktestLab CANCEL button.
- ✅ **D-4.7-mcp-inprocess (MVP)** — MCP HTTP base URL configurable.
- ✅ **D-4.7-full** — `flint/services/{strategies,backtest,journal,
  data,paper}.py` ship; MCP tools + REST routes both call through
  services. 12 standalone MCP tests.

**Phase 5 tail** — all shipped:

- ✅ **D-5.1-ruff-fixes** — F-class hard-fail; 315 auto-fixed; 26 real
  bugs resolved.

**Phase 6 tail** — foundation + refinements shipped:

- ✅ **D-6.1-unified (foundation)** — `SharedCapitalPortfolioEngine`
  runs N strategies on one shared `BacktestContext`. Tagged
  `_TaggedContextProxy` per strategy.
- ✅ **D-6.1 refinements (v1.5.1)** — per-strategy capital caps
  (`strategy_capital_caps={"alice": 0.4}`); attribution-by-trade via
  `_Position.entry_order_id` (closed-trade dicts now carry it; engine
  attribution falls through tagged-exit → tagged-entry → per-market
  even split); `dollar_imbalance(ctx)` accessor.
- ✅ **D-6.4-replay** — All 5 slices: event log, fold/replay primitive,
  snapshot compaction with fast-forward, BacktestContext writer hooks,
  REST API + MCP tools + UI page (`Replay.tsx` with session loader,
  ts scrubber, state cards, positions table). Auto-compaction +
  E2E parity over a real strategy + Rust ports of FundingLedger /
  BorrowLedger included in Wave 5 closeout.

**Trust polish (v1.5.x):**

- ✅ **Multi-venue funding correctness ladder** — Option A (engine
  per-venue query) + Option C (PaperContext architectural fix); the
  4th proof notebook `notebooks/multi_venue_funding_arb.py` proves
  D-2.1.d structural correctness end-to-end (per-leg attribution to 1e-6).
- ✅ **BacktestContext.apply_funding venue filter** — `funding_rate.source`
  now filters legs (back-compat fallback when no leg matches the source).
  Pre-existing bug uncovered while writing the proof notebook.
- ✅ **Price-source fallback chain** — `flint/paper/price_sources/`
  HL → Drift → DuckDB last-candle. Live PnL ticker survives Drift
  outage; ConnectionBanner-style health visible at
  `/api/v1/system/price-sources`.
- ✅ **D-2.1.c structural prep** — `LiveExecutionContext` composes the
  same 7 managers as PaperContext (empty placeholders this slice).
  Foundation in place; full wiring still needs testnet creds.

---

## Still deferred

Only the testnet-blocked live-trading chain remains. None of these can
land without real venue credentials in CI; they're sequenced behind
`.github/workflows/live-smoke.yml` secrets being configured.

### D-2.1.c — `LiveExecutionContext` full manager wiring

- Manager composition shipped (placeholders). Remaining: route venue
  fill events through `_fr.record(fill)` + `_pm.set/delete` + `_cm.
  debit/credit` so live mirrors paper's state model. Concrete subclass
  (CCXT first, then Drift / HL) refactor follows.
- Prerequisites: testnet credentials in `.github/workflows/live-smoke.yml`.
- Effort: ~1 week post-secrets.

### D-6.5-api — `/api/v1/live/preview` + `/api/v1/live/start`

- Two-step confirmation + kill switch + mainnet gate + UI deploy modal.
- Prerequisites: D-2.1.c shipped + testnet secrets.
- Effort: ~3 weeks.

### D-6.6-proof — Funding dislocation arb proof notebook + mainnet checklist

- v1.5.1 already shipped `notebooks/multi_venue_funding_arb.py` proving
  the *structural* path (positions, funding ledgers, attribution). What
  this row still needs: a paper-to-live walk-through with a real Drift
  testnet + HL testnet leg pair, a written mainnet pre-flight checklist,
  and a captured artifact showing the strategy ran for ≥24h without
  manual intervention.
- Prerequisites: D-6.5-api.
- Effort: ~1 week post-D-6.5.

### D-6.7-jito — Jito bundle integration

- Real Jito bundle submission for the priority-fee path on Solana.
- Prerequisites: D-6.5-api.
- Effort: ~2 weeks post-D-6.5.

---

## Open non-blocked polish (no spec entry yet)

These haven't been scoped into a phase — they're bug-fix or quality-of-
life items surfaced by recent ships. Each is small enough to land
opportunistically.

### Drift-S3 backfill fallback to HL/Pyth

- `flint/paper/engine.py:backfill_candle_gap` tries Drift Data API,
  then Drift S3, then gives up. With Drift offline post-hack, both
  legs fail and resume tests have to monkey-patch the helper.
- Fix: thread the same fallback chain idea from `flint/paper/
  price_sources/` so backfill walks HL → Drift → Pyth. Or just pull
  HL candles directly when Drift is unreachable (HL covers the same
  perp markets via `hyperliquid_candles.py`).
- Effort: ~1 day.

### Per-strategy Sharpe / drawdown attribution

- `SharedPortfolioResult` carries `per_strategy_pnl` + trade counts but
  not per-strategy Sharpe / drawdown. Add `per_strategy_sharpe`,
  `per_strategy_max_drawdown`, `per_strategy_equity_curve` (synthesized
  from tagged fills + closed-trade attribution).
- Effort: ~3 hours.

### Reconciliation UI bps histogram

- Currently text-only p50/p95/p99 panel. Add a small histogram of
  matched-fill bps deltas + an orphan-fills tab so visual outliers
  are obvious at a glance.
- Effort: ~half a day.

---

## How to pick something up

1. Claim it: PR that sets `Owner:` and `ETA:` on the row above.
2. Move it: if the item is substantial (>1 day), create a dedicated
   phase spec entry or add to an existing phase spec; link both ways.
3. Close it: when merged, move the row to "Closed since" with a note
   on what shipped + a release link, and update
   [TRUST_ARTIFACTS.md](TRUST_ARTIFACTS.md) if the item is a Phase 1
   trust artifact.

Do not let items silently age. If a deferred item has been sitting for
more than 30 days without an owner, re-evaluate whether it still matters.
