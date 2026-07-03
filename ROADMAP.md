# Flint Roadmap

Short index of where the project stands. Per-release notes live in
[`CHANGELOG.md`](CHANGELOG.md).

**Wedge:** the best local backtester + paper-trading lab for Solana
DEX + perp strategies. Venue-agnostic by design — **Hyperliquid** is
the live venue today; **Phoenix**, **Jupiter** spot, and **batch /
bulk** order routing are the planned expansion path.

Trust first, depth second, breadth last.

---

## Status (2026-04-26)

| Phase | Summary | State |
|---|---|---|
| 1 | Trust & correctness — parity, reconciliation, PIT audit, seeds, 4 proof notebooks, custom data ingest | shipped |
| 2 | Structural cleanup — ExecutionContext consolidation, store abstraction, config unification, sandbox isolation. D-2.1.b (7 managers) + D-2.1.d (PaperContext) shipped; D-2.1.c blocked on testnet creds | shipped (incl. v1.5.0 PaperContext unification) |
| 3 | Depth on wedge — execution upgrade v0.3 | shipped (Rust TxCostModel + OrderbookFiller + maker/taker fees) |
| 4 | Product polish — README, UI, WebSocket, capabilities | shipped (D-4.3 closed via hybrid WS, README rewrite v1.5.2) |
| 5 | CI & testing — matrix, Rust CI, parity gate, sandbox escape tests | shipped (ruff hard-fail on F-class, parity weekly) |
| 6 | Portfolio & cross-venue live | foundations shipped (D-3.5 orchestrator, D-6.1 + 3 refinements, D-6.4-replay 5/5 slices); live-trading chain (D-6.5/6/7) blocked on testnet creds |

Per-release notes: [`CHANGELOG.md`](CHANGELOG.md).

---

## Rules of engagement

1. No feature without a proof artifact (notebook or checked-in report).
2. Every PR: does this sharpen the DEX + perp lab wedge, or dilute it? Dilution blocks merge.
3. No silent network calls, no telemetry, no `curl | bash` required.
4. Rust engine must declare its capabilities; silent fallback is banned.
5. Single source of truth for docs (all guides under `docs/`).
6. **Drift is dropped** (offline post-hack) — not a supported venue. New code must not introduce Drift dependencies; legacy Drift code paths stay dormant. Hyperliquid + Pyth are the live sources; Phoenix / Jupiter / bulk land as new connectors.

---

## Out of scope (permanent)

- General-purpose crypto trading platform
- Hosted / multi-user / SaaS
- Options, structured products, spot-only strategies
- MEV bots (sandwich, sniping) — research only
- Data marketplace / resale / hosted data service
- Venues outside the Solana-DEX + perp roadmap (Hyperliquid live; Phoenix, Jupiter, bulk planned)

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
