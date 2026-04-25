# Flint Roadmap

Short index. Operational detail lives in [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) and `docs/specs/phase-*.md`.

**Wedge:** the best local backtester + paper-trading lab for Drift + Hyperliquid perp strategies.

Trust first, depth second, breadth last.

---

## Status (2026-04-25)

| Phase | Summary | Spec | State |
|---|---|---|---|
| 1 | Trust & correctness — parity, reconciliation, PIT audit, seeds, proof notebooks, custom data ingest | [phase-1](docs/specs/phase-1-trust-correctness.md) | shipped |
| 2 | Structural cleanup — ExecutionContext consolidation, store abstraction, config unification, sandbox isolation, repo cleanup | [phase-2](docs/specs/phase-2-structural-cleanup.md) | shipped (D-2.1.b 7-manager extraction + caller migration done) |
| 3 | Depth on wedge — execution upgrade v0.3 | [phase-3](docs/specs/phase-3-depth-on-wedge.md) | shipped (incl. Rust TxCostModel + OrderbookFiller, maker/taker fees) |
| 4 | Product polish — README, UI, WebSocket, capabilities | [phase-4](docs/specs/phase-4-product-polish.md) | shipped (D-4.3-websocket: 5 slices) |
| 5 | CI & testing — matrix, Rust CI, parity gate, sandbox escape tests | [phase-5](docs/specs/phase-5-ci-testing.md) | shipped (ruff hard-fail on F-class) |
| 6 | Portfolio & cross-venue live | [phase-6](docs/specs/phase-6-portfolio-cross-venue.md) | foundations shipped (D-3.5 orchestrator, D-6.1-unified shared-capital engine, D-6.4-replay 4/5 slices) |

Trust artifacts status: [`TRUST_ARTIFACTS.md`](TRUST_ARTIFACTS.md)

Wave-by-wave deferred work + state: [`WAVE_STATUS.md`](WAVE_STATUS.md)

Deferred sibling-PR backlog: [`DEFERRED.md`](DEFERRED.md)

---

## Recently done

- **Wave 1 (6/6)**: ruff hard-fail CI · `flint/services/*` extraction · `PositionManager` step 1 · paper reconciliation upload + UI panel · `useBackoffPoll<T>` + 3-hook migration · Rust `TxCostModel` (PyO3, 2.24× speedup, 1e-9 parity)
- **Wave 2**: D-2.1.b full close (7 managers extracted + every call site migrated) · D-3.1-rust Rust `OrderbookFiller` (3.52× speedup) · D-3.3 maker/taker tagging on the Rust fill path · D-3.5-orchestrator `PortfolioMarginEngine` facade
- **Wave 3 portfolio + UX**: `SharedCapitalPortfolioEngine` with `exit_order_id` PnL attribution · D-4.3-websocket end-to-end (per-session `/ws/paper|live/{id}` endpoints, ConnectionManager replay buffer, `useWebSocket<T>` hook with backoff + heartbeat, both UI pages bound)
- **Wave 5 replay**: D-6.4-replay slices 1+2+3+4 — event log, fold/replay primitive, snapshot compaction with fast-forward, BacktestContext writer hooks
- Phase 7 correctness (strategy catalog ↔ builder parity, MC Sharpe annualization, journal stores `total_return_pct`, volume-zero warnings)
- Dual-venue candle path (Drift + Hyperliquid), Pyth as price source of truth
- Hyperliquid funding history back to 2023-06-08 at native 1h cadence
- MIT license, version alignment, `.venv` purged from git

---

## Rules of engagement

1. No new user-facing surface until Phase 1 ships and Phase 2 is in.
2. Every PR: does this sharpen the Drift + HL perp wedge, or dilute it? Dilution blocks merge.
3. No feature without a proof artifact (notebook or checked-in report).
4. No silent network calls, no telemetry, no `curl | bash` required.
5. Rust engine must declare its capabilities; silent fallback is banned.
6. Single source of truth for docs (all guides under `docs/`).

Full detail: [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md).

---

## Out of scope (permanent)

- General-purpose crypto trading platform
- Hosted / multi-user / SaaS
- Options, structured products, spot-only strategies
- MEV bots (sandwich, sniping) — research only
- Data marketplace / resale / hosted data service
- Chains beyond Solana + Hyperliquid

---

## Dependencies & sequencing

```
Phase 1 ─┬─► Phase 3 ──► Phase 6
         │
Phase 2 ─┤
         │
Phase 5 ─┘

Phase 4 runs in parallel throughout.
```

Don't start Phase 3 without 1.1-1.3. Don't start Phase 6 without 3.1-3.5. This ordering is load-bearing.
