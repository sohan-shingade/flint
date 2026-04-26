# Flint Roadmap

Short index. Operational detail lives in
[`WAVE_STATUS.md`](WAVE_STATUS.md) (delivery log) +
[`DEFERRED.md`](DEFERRED.md) (open backlog) +
`docs/specs/phase-*.md` (executable specs).

**Wedge:** the best local backtester + paper-trading lab for Drift +
Hyperliquid perp strategies.

Trust first, depth second, breadth last.

---

## Status (2026-04-26)

| Phase | Summary | Spec | State |
|---|---|---|---|
| 1 | Trust & correctness — parity, reconciliation, PIT audit, seeds, 4 proof notebooks, custom data ingest | [phase-1](docs/specs/phase-1-trust-correctness.md) | shipped |
| 2 | Structural cleanup — ExecutionContext consolidation, store abstraction, config unification, sandbox isolation. D-2.1.b (7 managers) + D-2.1.d (PaperContext) shipped; D-2.1.c blocked on testnet creds | [phase-2](docs/specs/phase-2-structural-cleanup.md) | shipped (incl. v1.5.0 PaperContext unification) |
| 3 | Depth on wedge — execution upgrade v0.3 | [phase-3](docs/specs/phase-3-depth-on-wedge.md) | shipped (Rust TxCostModel + OrderbookFiller + maker/taker fees) |
| 4 | Product polish — README, UI, WebSocket, capabilities | [phase-4](docs/specs/phase-4-product-polish.md) | shipped (D-4.3 closed via hybrid WS, README rewrite v1.5.2) |
| 5 | CI & testing — matrix, Rust CI, parity gate, sandbox escape tests | [phase-5](docs/specs/phase-5-ci-testing.md) | shipped (ruff hard-fail on F-class, parity weekly) |
| 6 | Portfolio & cross-venue live | [phase-6](docs/specs/phase-6-portfolio-cross-venue.md) | foundations shipped (D-3.5 orchestrator, D-6.1 + 3 refinements, D-6.4-replay 5/5 slices); live-trading chain (D-6.5/6/7) blocked on testnet creds |

Trust artifacts status: [`TRUST_ARTIFACTS.md`](TRUST_ARTIFACTS.md).
Wave delivery log: [`WAVE_STATUS.md`](WAVE_STATUS.md).
Open + blocked backlog: [`DEFERRED.md`](DEFERRED.md).
Per-release notes: [`CHANGELOG.md`](CHANGELOG.md).

---

## Rules of engagement

1. No feature without a proof artifact (notebook or checked-in report).
2. Every PR: does this sharpen the Drift + HL perp wedge, or dilute it? Dilution blocks merge.
3. No silent network calls, no telemetry, no `curl | bash` required.
4. Rust engine must declare its capabilities; silent fallback is banned.
5. Single source of truth for docs (all guides under `docs/`).
6. **Drift is offline post-hack** — new code must not introduce Drift dependencies; any code that touches Drift needs a Hyperliquid / Pyth / DuckDB-cache fallback.

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
