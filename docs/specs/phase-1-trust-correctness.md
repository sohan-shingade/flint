# Phase 1 — Trust & Correctness

**Owner:** TBD
**Duration:** 6-10 weeks
**Blocks:** all subsequent phases
**Exit criteria:** see [`TRUST_ARTIFACTS.md`](../../TRUST_ARTIFACTS.md#exit-criteria-phase-1-complete)

Firms buy reproducibility before features. Phase 1 proves the current surface is honest; nothing new ships until it lands.

---

## Items

- [1.1 Rust↔Python parity fixes](#11-rust-python-parity-fixes)
- [1.2 Parity report pipeline](#12-parity-report-pipeline)
- [1.3 PIT audit + deterministic seeds](#13-pit-audit-and-deterministic-seeds)
- [1.4 Reconciliation tooling](#14-reconciliation-tooling)
- [1.5 Proof notebooks](#15-proof-notebooks)
- [1.6 Custom dataset ingest](#16-custom-dataset-ingest)
- [1.7 Trust artifacts status board](#17-trust-artifacts-status-board)

---

## 1.1 Rust↔Python parity fixes

**Goal:** Rust and Python engines return numerically identical `EngineResult` on all shared features. Same strategy, same candles, same seed → same output.

### Audit findings (verified 2026-04-23)

| # | Bug | Location | Severity |
|---|---|---|---|
| a | Sharpe uses bar-frequency in Rust, trade-frequency in Python MC path | `rust/src/runner.rs:209-218` | high |
| b | `FillResult.tx_cost` never accumulates into `EngineResult.total_tx_costs`; `test_tx_cost_deducted` fails | `rust/src/engine/fills.rs`, `rust/src/engine/positions.rs`, `flint/backtest/engine.py` result plumbing | high |
| c | `VenueFiller` RNG hardcoded to `seed_from_u64(42)` — not threaded from Python | `rust/src/engine/venue_fills.rs:28` | medium |
| d | Zero-volume warning only fires on Python pre-check, not Rust path | `flint/backtest/engine.py:225-231` | low-medium |
| e | Python `LatencyStage` defaults `random.Random()` unseeded (system time) | `flint/execution/latency.py:27` | medium |
| f | Equity-curve overwrite on force-close skews last bar | `flint/backtest/engine.py:531` | low |
| g | Lookahead in extra-market histories | `flint/backtest/engine.py:451` | high for cross-market |

### Tasks

**T1.1.a — Sharpe annualization parity**
- Port trade-frequency Sharpe from `flint/analytics/monte_carlo.py:~79` into `rust/src/runner.rs`.
- Replace `periods_per_year = 365.25 * 24.0 * 3600.0 / res_s` with a per-trade-frequency computation driven by `trades.len()` and backtest duration.
- Add unit test in `rust/src/engine/metrics.rs` confirming Rust Sharpe equals Python Sharpe to 1e-6 on a fixed 1k-trade fixture.

**T1.1.b — tx_cost end-to-end**
- Extend `FillResult` struct in `rust/src/types.rs` with `tx_cost: f64` (already present — verify).
- Populate `FillResult.tx_cost` in both `rust/src/engine/fills.rs` and Python `flint/execution/fill_pipeline.py`.
- Accumulate into `EngineResult.total_tx_costs` in `rust/src/engine/positions.rs:PositionManager::apply_fill` and `BacktestContext._finalize_fill` (Python).
- Fix existing regression: `tests/test_tx_cost_integration.py:test_tx_cost_deducted` must pass.

**T1.1.c — RNG seed threading**
- Add `seed: u64` parameter to `VenueFiller::new` in `rust/src/engine/venue_fills.rs:28`.
- Expose via PyO3 API in `rust/src/lib.rs` — `run_backtest(candles, config, seed)`.
- Remove hardcoded `42`.
- Python `BacktestEngine.run(..., seed: int | None = None)` accepts and forwards. Default: `hash((strategy.name, candles[0].ts)) & 0xFFFFFFFFFFFFFFFF` (deterministic but strategy-local).

**T1.1.d — Zero-volume warning parity**
- Move check in `flint/backtest/engine.py:225-231` above the `if can_use_rust:` branch so warning fires regardless of engine.
- Alternatively, port to Rust and emit via PyO3-returned warnings.

**T1.1.e — Latency RNG default seed**
- `flint/execution/latency.py:27` — require `seed` or derive deterministically. Deprecate `random.Random()` default.
- Update all callers in `flint/execution/fill_pipeline.py` + strategy tests.

**T1.1.f — Equity curve append, not overwrite**
- `flint/backtest/engine.py:531` — change `equity_curve[-1] = ...` to `equity_curve.append(...)` at force-close. Add final-bar marker field so metrics can distinguish.

**T1.1.g — Extra-market lookahead fix**
- `flint/backtest/engine.py:451` — `combined[candle.market] = history` (exclude current candle for all secondary markets). Current primary market keeps the current bar as "now".
- Add regression test: strategy that peeks at `ctx.get_candles("BTC-PERP")` on bar N and asserts last entry is bar N-1.

### Acceptance criteria

- `pytest tests/ -v` green on Linux + macOS, Py 3.10/3.11/3.12.
- `pytest tests/test_tx_cost_integration.py::test_tx_cost_deducted` passes.
- `cd rust && cargo test` green.
- New test `tests/test_rust_python_parity.py` asserts `EngineResult` equality on a 10-candle fixture across 3 strategies (MA crossover, RSI, funding arb) with the same seed. Tolerance: 1e-6 on PnL, exact on fill counts, exact on Sharpe.
- Audit re-run: all seven bugs above go from "present" to "gone".

### Effort

~5-7 days for one experienced contributor with PyO3 familiarity.

---

## 1.2 Parity report pipeline

**Goal:** one command produces a reproducible, checked-in report showing backtest vs paper divergence.

### Deliverables

1. `scripts/run_parity_report.py <strategy> <market> <days>` — runs:
   - Backtest on a fixed seed.
   - Paper broker on the same candle range (replay mode).
   - Computes equity-curve delta, per-trade residuals, fill-time delta.
   - Emits `artifacts/parity/{strategy}-{market}-{date}.md` with a stable format.
2. CI job (Phase 5.3) runs this for a reference strategy (`funding_momentum_v4`) and fails if any metric exceeds thresholds:
   - PnL divergence > 2% of initial capital over 30 days
   - Fill-time MAE > 60 seconds
   - Per-trade notional residual p95 > 5 bps
3. At least one reference report checked into `artifacts/parity/` per release.

### File targets

- New: `scripts/run_parity_report.py`
- New: `artifacts/parity/.gitkeep`
- New: `artifacts/parity/funding_momentum_v4-SOL-PERP-2026-04.md` (example)
- Modify: `.github/workflows/ci.yml` — add parity job (depends on Phase 5)
- Modify: existing `flint/backtest/parity.py` to emit markdown format

### Acceptance

- `python scripts/run_parity_report.py funding_momentum_v4 SOL-PERP 30` succeeds on a clean clone with downloaded sample data.
- Report is byte-identical across runs with the same seed.
- CI fails on any threshold breach.

### Effort

~1 week. Depends on 1.1.

---

## 1.3 PIT audit and deterministic seeds

**Goal:** eliminate point-in-time leaks; every RNG path is seedable and a seed produces byte-identical results.

### Tasks

**T1.3.a — PIT audit script**
- New: `scripts/audit_pit.py`
- For each provider under `flint/providers/*.py`, verify:
  - Candle timestamps are bar-close (not bar-open) and consistent
  - Funding events stamped at accrual, not settlement
  - Orderbook snapshots timestamped at exchange time, not poll time
  - Open interest snapshots at block/slot time, not poll time
- Emits `artifacts/pit/{provider}-{date}.md` with verdict per provider.
- CI runs it on every push; fails if any provider regresses.

**T1.3.b — Seed plumbing end-to-end**
- Thread `seed: int` through: `BacktestEngine.run` → `FillPipeline` → `LatencyStage` → `ImpactStage` (sqrt model has a noise term) → `PartialFillStage` → `MonteCarloBootstrap`.
- Same seed → identical equity curve, identical trades, identical MC CIs.
- UI: expose seed input on Lab page (advanced panel).
- API: `/api/v1/backtest/run` accepts optional `seed` field.
- MCP: `run_backtest` tool accepts `seed`.

**T1.3.c — Determinism regression test**
- New: `tests/test_determinism.py`
- For each of 3 flagship strategies: run twice with seed=42, assert every byte of `BacktestResult` identical (except wall-clock fields).

### Acceptance

- PIT audit produces reports for all providers, green.
- Determinism test green.
- Same seed → byte-identical equity curves, PnL, trade list across Python engine runs.

### Effort

~1 week.

---

## 1.4 Reconciliation tooling

**Goal:** match engine-recorded fills against actual venue fills; mismatches visible in UI.

### Tasks

**T1.4.a — `scripts/reconcile_fills.py`**
- CLI: `flint reconcile --session <paper_session_id>` (add to `flint/cli.py`).
- Fetches actual Drift / HL fills for the session window (via existing connectors).
- Diffs against engine-recorded fills from `live_fills` table.
- Emits `artifacts/reconciliation/{session_id}-{date}.md` with:
  - Count match / extra / missing
  - Per-fill price delta, size delta, timestamp delta
  - Summary p50/p95/p99

**T1.4.b — API endpoint**
- New: `GET /api/v1/paper/{session_id}/reconciliation` — returns reconciliation JSON.
- Wraps same logic as CLI.

**T1.4.c — UI panel**
- Add to `ui/src/pages/PaperTrading.tsx` — "Reconciliation" section on session detail view.
- Table of deltas, pass/fail badge.

**T1.4.d — Store method**
- Add `FlintStore.get_session_fills(session_id)` — replaces any raw `_conn` access needed by the script.

### Acceptance

- Running reconciliation on a 30-day paper session emits a report with < 10bps p95 price delta.
- UI shows the pass/fail badge on the session detail.
- Handles no-match case gracefully (session never traded).

### Effort

~1-2 weeks. Depends on 1.2.

---

## 1.5 Proof notebooks

**Goal:** three Jupyter notebooks per flagship strategy, each showing end-to-end backtest → paper → parity with checksums.

### Deliverables

- New: `notebooks/funding_arb.ipynb`
- New: `notebooks/basis_trade.ipynb`
- New: `notebooks/funding_momentum_v4.ipynb`

Each notebook:

1. Pins data provider checksums (SHA-256 of downloaded parquet files).
2. Runs backtest with fixed seed.
3. Runs paper replay on same candles.
4. Runs parity report inline.
5. Embeds equity curve, drawdown, trade log, parity deltas as static cells.
6. Includes "reproduce this" box at top with exact commands.

### Replacement in README

- Remove `examples/*.py` from "Try It Yourself" section.
- Replace with: "Run `jupyter lab notebooks/funding_arb.ipynb` — pinned data, fixed seed, end-to-end trust."

### Acceptance

- Each notebook runs top-to-bottom in < 10 min from a clean clone + `flint init`.
- Parity delta cell shows < 2% PnL divergence.
- Notebooks are checked in with outputs cleared (CI re-runs them).

### Effort

~1-2 weeks. Depends on 1.2.

---

## 1.6 Custom dataset ingest

**Goal:** users bring their own CSV/Parquet candles, funding, orderbook, fills. Same pipeline, no code changes.

### Tasks

**T1.6.a — Schema**
- Document strict schemas at `docs/reference/custom-data-schema.md`:
  - Candles: `ts_epoch_s (int64, UTC, bar-close), open, high, low, close, volume, market, resolution_s`
  - Funding: `ts_epoch_s, market, venue, rate, interval_s`
  - Orderbook: `ts_epoch_s, market, venue, bid_prices[], bid_sizes[], ask_prices[], ask_sizes[]`
  - Fills: `ts_epoch_s, market, venue, side, size, price, fee`
- Validation: monotonic timestamps, UTC, no duplicates, declared resolution matches actual spacing.

**T1.6.b — `CustomCSVProvider` / `CustomParquetProvider`**
- New: `flint/providers/custom.py` — `DataProvider` subclass.
- Config in `flint.yaml`:
  ```yaml
  custom_providers:
    - name: my_data
      type: csv
      path: ./data/my_candles.csv
      markets: ["custom:BTC-SPOT"]
      resolution_s: 3600
  ```
- Market name prefix `custom:` enforced so custom markets never collide with Drift/HL names.

**T1.6.c — Provenance metadata**
- Every custom row gets a `source_hash` column (SHA-256 of the source file) stored in DuckDB alongside candles.
- Proof notebooks can pin exact input versions.

**T1.6.d — Bring-your-own fill log**
- Reconciliation (1.4) and slippage calibration (Phase 3.6) both accept user-supplied fill CSVs via the same provider interface.

### Acceptance

- User points at a CSV, runs `flint data import --config flint.yaml`, candles appear in DuckDB.
- Backtest on `custom:BTC-SPOT` runs end-to-end.
- Strict validation rejects: duplicate timestamps, non-UTC, wrong resolution, missing columns.
- Source hash is surfaced in the backtest result and proof notebooks.

### Effort

~1-2 weeks.

---

## 1.7 Trust artifacts status board

**Goal:** public, always-current view of what's shipped vs promised.

### Deliverable

[`TRUST_ARTIFACTS.md`](../../TRUST_ARTIFACTS.md) at repo root.

### Maintenance rules

- Every Phase 1 merge updates the relevant row.
- CI check: if a PR touches `flint/` but doesn't update `TRUST_ARTIFACTS.md`, warn (not block).
- Links to live spec for each row.

### Acceptance

- Board exists and accurately reflects status as of the PR merging it.
- Linked from `README.md` and `ROADMAP.md`.
- 🟢 Shipped.

### Effort

~2 hours. Done in PR opening Phase 1.

---

## Dependencies

```
1.1 ──► 1.2 ──► 1.4
               └► 1.5

1.3 (independent)
1.6 (independent)
1.7 (no code deps — just a doc)
```

Start with 1.1 + 1.3 + 1.6 + 1.7 in parallel. 1.2 starts when 1.1 is green. 1.4 + 1.5 follow 1.2.

---

## Out of scope for Phase 1

- New strategies, new data providers for external venues, new UI features. All wait for Phase 3+.
- Rust feature expansion beyond parity with Python on shared features. That's Phase 3.

## Deferred sibling PRs

Some Phase 1 work ships the core + defers tail items to sibling PRs. Tracker:
[`DEFERRED.md`](../../DEFERRED.md).

- **D-1.1.b** — Rust force-close exit fees (folds into Phase 3.3 `FeeModel` trait)
- **D-1.2-CI** — parity report CI gate wiring (lives in Phase 5.3)
- **D-1.3-providers** — PIT_METADATA for remaining 22 providers (incremental, one-per-PR)
- **D-1.4-api** — reconciliation API endpoint + UI panel (Phase 4 UI work)
- **D-1.5-data-pins** — replace `PIN-ME` candle-hash placeholders once canonical fixture committed
- **D-1.6-byo-fills** — custom fill-log ingest helper (lands with D-1.4 or Phase 3.6, whichever first)
