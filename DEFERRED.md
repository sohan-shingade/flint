# Deferred Work

Tracker for items scoped in a phase but split into sibling PRs because
bundling would make review infeasible or because validation needs something
the in-session run couldn't provide (testnet access, UI smoke, real data).

Rule: **an item sits here until it has an owner + ETA, then it moves to an
active phase item.** Nothing leaves Flint's todo surface silently.

Updated: 2026-04-24 (after full deferred-queue execution pass — 12 items
closed, remainder scoped with honest blockers).

---

## Closed this pass

**Phase 1 tail** — all shipped:

- ✅ **D-1.1.b** — Rust `close_all()` accepts a `FeeModel` and charges the
  synthetic exit fill (was `fee=0.0`).
- ✅ **D-1.2-CI** — `.github/workflows/parity.yml` shipped in Phase 5.3.
- ✅ **D-1.3-providers** — 22 provider modules declared `PIT_METADATA`.
  Audit re-run: 26/26 ✓.
- ✅ **D-1.4-api** — `GET /api/v1/paper/{session_id}/reconciliation`
  returns engine-side fill summary. UI panel + venue-log POST =
  D-1.4-ui (below).
- ✅ **D-1.5-data-pins** — `docs/how-to/pin-notebook-fixtures.md` documents
  both lightweight + heavy fixture workflows.
- ✅ **D-1.6-byo-fills** — `CustomCSVProvider(table="fills")` parses user
  fill logs; `CustomDataImport.fills` returns parsed records.

**Phase 2 tail** — partial:

- ✅ **D-2.2-internal** — `journal/storage.py` + `paper/session_store.py`
  migrated off raw `_store._conn` via new `FlintStore._sql_*` wrappers.
- ✅ **D-2.4.b** — `/api/v1/backtest/run` routes user-supplied inline code
  (`req.code`) and `user:*` paths through `run_strategy_in_sandbox` in
  sandbox-compatible configurations.

**Phase 4 tail** — all shipped:

- ✅ **D-4.1-wedge** — README hero rewritten around the perp-lab wedge;
  comparison table split into "vs DeFi-native tools" vs "vs general crypto
  bots"; `examples/` replaced with `notebooks/` in Try It; CEX-live
  honestly labeled "Planned".
- ✅ **D-4.2-backoff** — 18 `.catch(() => {})` sites across 10 UI files now
  log structured errors.
- ✅ **D-4.5-ui** — `useBacktest` exposes `cancel()` + auto-POSTs `/cancel`
  on unmount. BacktestLab shows a CANCEL button while running.
- ✅ **D-4.7-mcp-inprocess (MVP)** — MCP HTTP base URL configurable via
  `FLINT_API_URL` env var (was hardcoded to `127.0.0.1:8000`).

---

## Sequenced execution plan

**Full delivery plan for the 16 items below:**
[`docs/specs/deferred-execution-plan.md`](docs/specs/deferred-execution-plan.md).

5 waves, single-engineer ~22 weeks or 3-engineer parallel ~12 weeks.
Critical path: god-class breakup → live-context merge → live-deploy API →
Jito bundles.

---

## Still deferred

### D-1.4-ui — Reconciliation UI panel + POST variant

- Multipart CSV upload on `POST /api/v1/paper/{session_id}/reconciliation`,
  invokes `scripts/reconcile_fills.reconcile()`, returns full diff;
  PaperTrading page gets "Reconcile" button + file picker + results table.
- Effort: ~3 days.

### D-2.1.b — Break up BacktestContext god class

- 973-LOC refactor, ~40 call sites. Needs dedicated PR + regression sweep
  between each extraction.
- Prerequisites: none.
- Effort: ~2 weeks.

### D-2.1.c — Merge LiveContext + LiveExecutionContext

- Requires Drift devnet + HL testnet smoke tests. Blocked on D-2.1.b +
  `.github/workflows/live-smoke.yml` secrets.
- Effort: ~1 week.

### D-2.1.d — Separate PaperContext from PaperBroker wiring

- Prerequisites: D-2.1.b.
- Effort: ~1 week.

### D-3.1-rust — Rust `OrderbookFiller`

- Prerequisites: D-3.4-rust (Rust fill pipeline shell).
- Effort: ~3 days once D-3.4 lands.

### D-3.3-maker-detection — Rust fill pipeline tracks maker/taker role

- Prerequisites: D-3.4-rust.
- Effort: ~2 days.

### D-3.4-rust — Rust `LatencyStage` + `PartialFillStage` + `FillPipeline`

- Dedicated Rust engineering. New modules in
  `rust/src/engine/{latency,partial_fill,fill_pipeline}.rs` + PyO3 wiring
  + parity tests.
- Effort: ~1 week.

### D-3.5-orchestrator — Unified `PortfolioMarginEngine` in `BacktestContext`

- Prerequisites: D-2.1.b.
- Effort: ~1 week.

### D-4.2-backoff-full — Per-hook exponential backoff

- Silent catches now logged (shipped). Remaining: 1s → 2s → 5s → 10s → 30s
  exponential backoff in each polling hook + per-hook error banners.
- Effort: ~3 days.

### D-4.3-websocket — Paper + Live WebSocket streams

- Server `/ws/paper/{id}` + `/ws/live/{id}` + reconnecting `useWebSocket`
  hook + page migration + fallback-to-polling path.
- Effort: ~1 week.

### D-4.7-full — Full MCP in-process service layer

- Current: HTTP URL configurable (MVP shipped).
- Remaining: `flint/services/{backtest,paper,data,journal}.py` callable
  without FastAPI. MCP tools + REST routes both go through services.
- Effort: ~3 days.

### D-5.1-ruff-fixes — Tighten ruff soft-fail to hard-fail

- Current: `ruff check` + `ruff format --check` run with `|| echo`.
- Needed: full-repo auto-fix pass, then flip to hard-fail.
- Effort: ~1 day.

### D-6.1-unified — Shared-capital PortfolioBacktestEngine

- Prerequisites: D-2.1.b.
- Effort: ~3 weeks.

### D-6.4-replay — Portfolio event-sourcing + replay

- D-2.2-internal storage cleanup is done (✓); still needs event
  sequence numbering + `PortfolioSnapshot` compaction table +
  `replay(session_id, target_ts) -> BookState`.
- Effort: ~2-3 weeks.

### D-6.5-api — `/api/v1/live/preview` + `/api/v1/live/start`

- Two-step confirmation + kill switch + mainnet gate + UI deploy modal.
- Prerequisites: T5.6 live-smoke secrets configured + D-2.1.c.
- Effort: ~3 weeks.

### D-6.6-proof — Funding dislocation arb proof notebook + mainnet checklist

- Prerequisites: D-1.4-ui + D-6.5-api.
- Effort: ~1 week.

### D-6.7-jito — Jito bundle integration

- Prerequisites: D-6.5-api.
- Effort: ~2 weeks.

---

## How to pick something up

1. Claim it: PR that sets `Owner:` and `ETA:` on the row above.
2. Move it: if the item is substantial (>1 day), create a dedicated phase
   spec entry or add to an existing phase spec; link both ways.
3. Close it: when merged, delete the row from this file and update
   [TRUST_ARTIFACTS.md](TRUST_ARTIFACTS.md) if the item is a Phase 1 trust
   artifact.

Do not let items silently age. If a deferred item has been sitting for
more than 30 days without an owner, re-evaluate whether it still matters.
