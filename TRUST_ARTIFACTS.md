# Trust Artifacts — Status Board

Live tracker for Phase 1 items. Firms buy reproducibility before they buy features. Every row here is a blocker on Section 2+ work until shipped.

**Legend:**
- 🟢 Shipped — code merged, tested, reproducible from a clean clone
- 🟡 Partial — some subcomponents exist, but exit criteria not met
- 🔴 Not started — no code or design PR
- ⏳ In progress — active PR or branch

Updated: 2026-04-25

---

## Status

| # | Artifact | Status | Spec | Owner | ETA | Blocker |
|---|---|---|---|---|---|---|
| 1.1 | Rust↔Python parity fixes (Sharpe, tx_cost, seeds, zero-vol warning) | 🟢 Shipped — force-close equity append (Rust+Py), cross-market strict `<`, LatencyStage default seed, RNG threaded through Rust `VenueFiller`, tx_cost feature gate (Python path populates correctly). 5 parity tests. | [phase-1 §1.1](docs/specs/phase-1-trust-correctness.md#11-rust-python-parity-fixes) | — | — | — |
| 1.2 | Parity report pipeline + CI gate | 🟢 Shipped — `scripts/run_parity_report.py` emits markdown artifacts with threshold gate; 6 strategies catalogued. CI parity gate (Phase 5.3) shipped — `.github/workflows/` runs the report on every push and fails on divergence > thresholds. | [phase-1 §1.2](docs/specs/phase-1-trust-correctness.md#12-parity-report-pipeline) | — | — | — |
| 1.3 | Point-in-time data audit + deterministic seeds | 🟢 Shipped — `scripts/audit_pit.py` scans all 25 providers, PIT_METADATA pattern established; 3 flagship providers declared. Seed threaded through BacktestEngine → Rust, LatencyStage, MonteCarloBootstrap. `tests/test_determinism.py` (5 tests). | [phase-1 §1.3](docs/specs/phase-1-trust-correctness.md#13-pit-audit-and-deterministic-seeds) | — | — | — |
| 1.4 | Reconciliation tooling (live fills vs engine fills, UI mismatch report) | 🟢 Shipped — `scripts/reconcile_fills.py` + `POST /api/v1/paper/{id}/reconciliation` (D-1.4-ui) accepts multipart CSV upload, parses via `parse_venue_fills_csv_text`, runs `reconcile()` against engine fills. PaperTrading.tsx "RECONCILE FILLS" button + results panel mirror the parity report layout. 14 tests + 6 endpoint tests. | [phase-1 §1.4](docs/specs/phase-1-trust-correctness.md#14-reconciliation-tooling) | — | — | — |
| 1.5 | Proof notebooks per flagship strategy | 🟢 Shipped — `notebooks/funding_arb.py`, `basis_trade.py`, `momentum_breakout.py`, `multi_venue_funding_arb.py` (jupytext-compatible), each pins candle sha256, runs backtest + parity, emits artifact, CI-gated via exit code. `notebooks/README.md`. **`multi_venue_funding_arb.py` (added v1.5.1) proves D-2.1.d structural correctness — same-market opposing legs on Drift+HL with per-venue funding ledgers, exact per-leg attribution, no spillover.** | [phase-1 §1.5](docs/specs/phase-1-trust-correctness.md#15-proof-notebooks) | — | — | — |
| 1.6 | Fix `test_tx_cost_deducted` regression | 🟢 Shipped — FillPipeline with `tx_cost_model` gates Rust off; Python path correctly accumulates `FillResult.tx_cost` → `BacktestResult.total_tx_costs`. All 8 tx_cost tests pass. | [phase-1 §1.1 (bug 2)](docs/specs/phase-1-trust-correctness.md#11-rust-python-parity-fixes) | — | — | — |
| 1.7 | Custom dataset (CSV/Parquet) ingest + provenance | 🟢 Shipped — `flint/providers/custom.py` (CustomCSVProvider + CustomParquetProvider), `docs/reference/custom-data-schema.md`, source_hash provenance, strict validation (OHLCV sanity, monotonic ts, resolution match, unit-error funding check, `custom:*` namespace). 12 tests. | [phase-1 §1.6](docs/specs/phase-1-trust-correctness.md#16-custom-dataset-ingest) | — | — | — |
| 1.7-sb | This status board | 🟢 Shipped | [phase-1 §1.7](docs/specs/phase-1-trust-correctness.md#17-trust-artifacts-status-board) | — | — | — |

---

## Exit criteria (Phase 1 complete)

All of:

1. An outside researcher can clone the repo, run one command, and get a bit-identical parity report on a flagship strategy.
2. That same command runs against a user-supplied CSV with zero code changes.
3. CI runs the parity report and fails on divergence > 5bps over 30 days.
4. Three proof notebooks exist and run end-to-end from a clean clone, pinned to data checksums.
5. Rust and Python engines return **numerically identical** `EngineResult` fields on all shared features (tolerance: 1e-6 on PnL, exact on fill counts).
6. Every RNG path accepts a seed and the same seed produces byte-identical results across runs.

Until all six are green, Section 2 (depth on wedge) does not start.

---

## Deferred tail items

Phase 1 core is 🟢; a few tail items are tracked as sibling PRs in
[`DEFERRED.md`](DEFERRED.md):

- **D-1.1.b** — Rust force-close exit fees (fold into Phase 3.3)
- **D-1.2-CI** — parity report CI gate (Phase 5.3)
- **D-1.3-providers** — 22 remaining PIT_METADATA declarations (incremental)
- **D-1.4-api** — reconciliation REST + UI panel (Phase 4)
- **D-1.5-data-pins** — canonical candle-hash fixture
- **D-1.6-byo-fills** — custom fill-log ingest

These do not gate Section 2+ (the ROADMAP blocker). Core Phase 1 exit
criteria are met via what shipped.

## How to update this board

Every time a Phase 1 PR merges:

1. Update the relevant row's `Status` column.
2. Update `Updated:` at top.
3. If all rows for a phase item go 🟢, update the main [`ROADMAP.md`](ROADMAP.md) "Recently done" section.
4. If a new blocker surfaces, add a row or update the `Blocker` column — do not hide it.

This board is public in both senses: checked into git and linked from the README. That's the point.
