# Phase 3 — Depth on the Wedge

**Owner:** TBD
**Duration:** 8-12 weeks
**Blocks:** Phase 6
**Hard gate:** §§1.1-1.3 green, §§2.1-2.2 green

Takes Flint from "toy backtester" to "credible perp-strategy lab." Implements `docs/specs/execution-upgrade-v0.3.md` with a Rust-first discipline.

---

## Items

- [3.1 Orderbook-walk fills (Rust + Python)](#31-orderbook-walk-fills-rust--python)
- [3.2 Rust feature-parity matrix + explicit fallback](#32-rust-feature-parity-matrix--explicit-fallback)
- [3.3 Maker/taker fee split in Rust](#33-makertaker-fee-split-in-rust)
- [3.4 Partial fills + latency stage in Rust](#34-partial-fills--latency-stage-in-rust)
- [3.5 Multi-venue positions + shared margin](#35-multi-venue-positions--shared-margin)
- [3.6 Slippage calibration reports](#36-slippage-calibration-reports)

---

## 3.1 Orderbook-walk fills (Rust + Python)

**Goal:** `OrderbookFillModel` walks L2 depth, rejects size-exceeds-liquidity, attributes slippage per fill. Works in both engines.

### State today

- Python: `flint/execution/fill_models.py:OrderbookFillModel._walk_book()` implemented.
- Rust: `rust/src/engine/fills.rs` has a stub; `OrderbookFillModel` not implemented.
- No rejection logic for orders exceeding aggregate book depth.

### Tasks

**T3.1.a — Python hardening**
- `OrderbookFillModel.fill()` rejects if `order.size > sum(level.size for level in book.asks[:depth])` (or bids on sell).
- Returns `FillResult(rejected=True, reason="insufficient_liquidity")` instead of silently underfilling.
- Per-fill slippage attributed: `fill.slippage_bps = (vwap_price - book.mid) / book.mid * 10_000`.

**T3.1.b — Rust port**
- `rust/src/engine/fills.rs:OrderbookFiller` struct.
- Consumes `Orderbook` from `rust/src/types.rs` (add L2 levels if missing).
- Implements the same rejection + VWAP walk.
- Add `FillResult.slippage_bps` field (sync with Python).

**T3.1.c — Integration**
- `FillPipeline` in both engines routes to `OrderbookFiller` when the market has L2 data for the bar, falls back to `ImpactStage` (vAMM/sqrt/flat) otherwise.
- `BacktestContext` tracks `fill.slippage_bps` and surfaces in trade log.

**T3.1.d — Tests**
- `tests/test_orderbook_fill.py`:
  - 10-level book, size = 50% of L1 depth → single-level fill.
  - Size = 150% of L1+L2 combined → rejected.
  - Size = 80% of total book → multi-level VWAP fill, slippage matches hand computation.
- `tests/test_rust_orderbook_parity.py` — same scenarios, Rust must match Python.

### Acceptance

- Orders exceeding book depth are rejected (not silently underfilled).
- Rust + Python produce identical `FillResult` on all scenarios.
- Surface in UI trade log: slippage_bps per trade visible in BacktestLab results.

### Effort

~2 weeks.

---

## 3.2 Rust feature-parity matrix + explicit fallback

**Problem:** today, using a Rust-incompatible feature silently falls back to Python. User thinks they're getting 10-50x; actually Python runs. Or worse: Rust runs but silently omits a feature and produces different numbers.

### Target

Capability contract between Python driver and Rust engine:

```python
@dataclass
class RustCapabilities:
    fill_models: set[str]           # {"close", "next_bar_open", "slippage", "orderbook", "impact_pipeline"}
    fee_models: set[str]             # {"flat", "maker_taker", "drift_native", "hyperliquid_native"}
    supports_partial_fills: bool
    supports_tx_costs: bool
    supports_monte_carlo: bool
    supports_multi_venue: bool
    supports_margin_liquidation: bool
```

### Tasks

**T3.2.a — `RustEngine.capabilities() -> RustCapabilities`**
- Exposed via PyO3 from `rust/src/lib.rs`.
- Returned once per process at startup.

**T3.2.b — Dispatch + logging**
- `BacktestEngine.run(config)` logs which engine ran (`rust` or `python`) and why (if Python: which unsupported feature triggered fallback).
- Stored in `BacktestResult.engine_used` + `BacktestResult.fallback_reason`.

**T3.2.c — `--rust-required` flag**
- CLI: `flint backtest --rust-required ...` errors with clear message if fallback would trigger.
- API: `BacktestConfig(rust_required=True)` same behavior.

**T3.2.d — Publish capability doc**
- New: `docs/reference/rust-engine.md` with current capability table.
- Auto-generated from `RustCapabilities` on build.

### Acceptance

- Running a backtest with orderbook fills on a Rust-only build succeeds (after 3.1), or errors clearly with `--rust-required`.
- `BacktestResult.engine_used` is always set.
- README no longer claims "drop-in 10-50x"; claims "up to Nx on supported features" with link to capability doc.

### Effort

~1 week.

---

## 3.3 Maker/taker fee split in Rust

**State today:** Python supports maker rebates and per-venue fee schedules (Drift: 10 bps taker, -2 bps maker rebate; HL: 3.5 bps taker, 1 bp maker). Rust uses a flat fee model.

### Tasks

**T3.3.a — `FeeModel` trait in Rust**
- `rust/src/engine/fees.rs:FeeModel`:
  - `fn fee(&self, order: &Order, fill: &FillResult) -> f64`
  - Implementations: `FlatFee`, `MakerTakerFee`, `DriftFee`, `HyperliquidFee`
- `VenueConfig` carries a `fee_model: FeeModel` field.

**T3.3.b — Port Python fee models**
- `DriftFee` — 10 bps taker, -2 bps maker, per-market overrides.
- `HyperliquidFee` — 3.5 bps taker, 1 bp maker, tiered by 30-day volume (optional).

**T3.3.c — Parity test**
- `tests/test_rust_fee_parity.py` — identical `FeeModel` output on 1k trades.

### Acceptance

- Rust matches Python fee computation to 1e-9.
- Maker rebates visible in Rust backtest result.
- Capability matrix updated.

### Effort

~3 days.

---

## 3.4 Partial fills + latency stage in Rust

**State today:** Python has `PartialFillStage` and `LatencyStage` inside `FillPipeline`. Rust does not.

### Tasks

**T3.4.a — `LatencyStage` in Rust**
- `rust/src/engine/latency.rs` — mirrors `flint/execution/latency.py`:
  - Order eligible at `order.ts + latency_ms + jitter(seed)` where `jitter` uses the threaded seed (from 1.1.c).
  - Fill deferred to next bar if eligible after bar close.

**T3.4.b — `PartialFillStage` in Rust**
- `rust/src/engine/partial_fill.rs` — mirrors `flint/execution/partial_fill.py`:
  - If `size > max_bar_volume * participation_rate`, split into multiple partial fills across bars.

**T3.4.c — Pipeline composition**
- `rust/src/engine/fill_pipeline.rs` — composes `LatencyStage → ImpactStage → PartialFillStage` identical to Python.

**T3.4.d — Parity test**
- Extend `tests/test_rust_python_parity.py` to cover fills with latency + partial across multi-bar execution.

### Acceptance

- Rust `FillPipeline` exists and produces identical output to Python on shared features.
- Capability matrix: `supports_partial_fills = True`, `supports_latency = True`.

### Effort

~1 week.

---

## 3.5 Multi-venue positions + shared margin

**State today:** Python `MultiVenueLiveContext` exists; margin engine has per-venue logic but cross-venue collateral sharing is fragile. Capital transfer latency not modeled.

### Tasks

**T3.5.a — Cross-venue collateral model**
- `flint/execution/margin.py:PortfolioMarginEngine`:
  - Tracks equity per venue + cross-collateral flow.
  - Each venue has `isolated_margin` (can't be used by others) vs `cross_margin` (can be drawn by connected venues subject to transfer latency).

**T3.5.b — Transfer latency model**
- `flint/execution/capital.py:CapitalTransfer`:
  - `transfer(from_venue, to_venue, amount, request_ts) -> settle_ts`
  - Default latencies: Drift↔HL = 30 seconds (bridge), CEX↔CEX = 5 seconds, CEX↔DeFi = 60 seconds.
- Strategy can query `ctx.available_capital(venue)` which respects in-flight transfers.

**T3.5.c — Liquidation events**
- `PortfolioMarginEngine.check_liquidation(ts)` runs every bar close.
- Per-venue maintenance margin rules:
  - Drift: 5% maintenance
  - HL: 2.5% maintenance
  - CEX presets per `venue_config.py`
- Liquidation event emitted into `BacktestResult.liquidations: list[LiquidationEvent]`.

**T3.5.d — UI surface**
- Paper / Backtest result pages show per-venue equity + liquidation events if any.

### Acceptance

- A funding-arb strategy with leveraged positions on both Drift + HL correctly attributes funding per venue and liquidates at each venue's MMR.
- Capital transfer latency surfaces: strategy can't instantly move $1M from Drift to HL; transfer takes 30s and is visible in trade log.
- `tests/test_multi_venue_margin.py` validates all three cross-margin scenarios.

### Effort

~2 weeks.

---

## 3.6 Slippage calibration reports

**State today:** `flint calibrate` CLI exists in `flint/backtest/calibration.py`; stores raw stats. No automated impact-curve fit, no report, no drift detection.

### Tasks

**T3.6.a — Impact-curve fit**
- `flint calibrate --market SOL-PERP --fills fills.csv`:
  - Fits power-law and sqrt impact models to user-provided fill CSV (via Phase 1.6 custom data ingest).
  - 5-fold cross-validation for out-of-sample R².
  - Writes calibrated coefficients to `flint.yaml` under `venues.{venue}.impact_coefficient`.

**T3.6.b — Drift detection**
- On re-calibration: if new `impact_coefficient` differs from stored by >15%, emit warning in report and require `--force` to overwrite.

**T3.6.c — Markdown report**
- Emits `artifacts/calibration/{market}-{venue}-{date}.md`:
  - Raw fill stats (count, mean slippage, p50/p95)
  - Fit coefficients (power-law α, sqrt β)
  - CV R² scores
  - Drift vs prior calibration
  - Recommended action

**T3.6.d — UI panel**
- FillAnalysis page: upload CSV → calibration report inline.

### Acceptance

- `flint calibrate` end-to-end on a real 30-day fill CSV produces a report with all four sections.
- Warning surfaces on >15% drift.
- Calibrated values round-trip through `flint.yaml`.

### Effort

~1 week. Depends on 1.6 (custom data ingest).

---

## Dependencies

```
3.1 (orderbook fills) ──► 3.2 (capability matrix)
3.3 (fees)            ──► 3.2
3.4 (partial+latency) ──► 3.2
                        └► Phase 6.2 (book-level risk)
3.5 (multi-venue)     ──► Phase 6.1 (multi-strategy backtest)
3.6 (calibration)     ──► Phase 6.6 (funding arb reference)
```

Start 3.1 + 3.3 + 3.5 in parallel. 3.2 lands last (depends on 3.1, 3.3, 3.4). 3.6 starts after 1.6.

---

## Exit criteria (Phase 3 complete)

1. Orderbook-walk fills work in both engines; size-exceeds-liquidity rejected.
2. Rust capability matrix published; `--rust-required` flag works.
3. Maker/taker and per-venue fee models in Rust at parity.
4. Partial fills + latency in Rust at parity.
5. Multi-venue backtest with shared margin and capital transfer latency produces liquidations at the right thresholds.
6. `flint calibrate` produces a drift-aware report and round-trips into `flint.yaml`.
7. **Funding-arb backtest vs paper divergence is explainable (not noise) and < 10bps over 30 days on SOL-PERP.** (from ROADMAP §2 exit criteria)

Until all seven are green, Phase 6 does not start.

## Deferred sibling PRs

Phase 3 lands the Python hardening + capability matrix + fee-model trait +
calibration reports. Rust parity on the impact / partial / latency paths is
split into dedicated sibling PRs because each needs focused Rust engineering
and cargo-side validation. Tracker: [`DEFERRED.md`](../../DEFERRED.md).

- **D-3.1-rust** — Rust `OrderbookFiller` struct matching the Python
  `OrderbookFillModel` hardening
- **D-3.3-maker-detection** — Rust fill pipeline tracks maker vs taker role
  (enables the `FeeModel::compute_fee_with_role` path to actually receive
  `is_maker=true` from resting limit fills)
- **D-3.4-rust** — Rust `LatencyStage` + `PartialFillStage` + composed
  `FillPipeline` matching Python
- **D-3.5-orchestrator** — `PortfolioMarginEngine` facade threaded through
  `BacktestContext` (blocked on Phase 2 D-2.1.b god-class breakup)

Until D-3.4-rust lands, `flint_core.capabilities()` correctly reports
`supports_partial_fills=False` and `supports_latency_stage=False` so
`BacktestEngine` falls back to Python automatically for those features.
