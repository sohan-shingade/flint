# Flint — Implementation Plan

Master plan for restructuring Flint into a credible local backtest + paper-trading lab for Drift + Hyperliquid perp strategies. Derived from the 2026-04-23 audit.

Supersedes the detailed sections of `ROADMAP.md` while preserving its trust-first ordering. `ROADMAP.md` is now a short index; this file is the operational plan; `docs/specs/phase-*.md` are the executable specs.

---

## Wedge

**The best local backtester + paper-trading lab for Drift + Hyperliquid perp strategies.**

Everything below sharpens that wedge. Anything that doesn't is deliberately out of scope (see bottom).

---

## Rules of engagement

1. **No new user-facing surface until Phase 1 is shipped and Phase 2 is in.** Bug fixes, internal cleanup, and proof artifacts only. New features wait.
2. **Every PR must answer: "does this sharpen the Drift + HL perp wedge, or dilute it?"** Dilution blocks merge regardless of code quality.
3. **No feature lands without a proof artifact.** If it can't be shown end-to-end in a notebook or a checked-in report, it doesn't ship to the front page.
4. **No silent network calls, no telemetry, no `curl | bash` required.** `pip install -e .` must be a working path forever.
5. **Rust engine must declare its capabilities.** Any feature not implemented in Rust must be detectable from Python; silent fallback is banned.
6. **Single source of truth for docs.** Every claim in `README.md`, the UI docs page, and MCP guide resources traces back to one markdown file under `docs/`.

---

## Phase summary

| # | Phase | Spec | Duration | Unblocks |
|---|---|---|---|---|
| 1 | Trust & correctness | [phase-1](docs/specs/phase-1-trust-correctness.md) | 6-10 weeks | All subsequent phases |
| 2 | Structural cleanup | [phase-2](docs/specs/phase-2-structural-cleanup.md) | 4-6 weeks (parallel) | Rust parity, strategy portability |
| 3 | Depth on wedge | [phase-3](docs/specs/phase-3-depth-on-wedge.md) | 8-12 weeks | Portfolio + live |
| 4 | Product polish | [phase-4](docs/specs/phase-4-product-polish.md) | 4-6 weeks (parallel) | Credibility, UX |
| 5 | CI & testing | [phase-5](docs/specs/phase-5-ci-testing.md) | 2-3 weeks (parallel) | Regression confidence |
| 6 | Portfolio & cross-venue live | [phase-6](docs/specs/phase-6-portfolio-cross-venue.md) | 3+ months | End-state feature set |

## Sequencing

```
Phase 1 (trust) ─┬─► Phase 3 (depth) ──► Phase 6 (portfolio + live)
                 │
Phase 2 (struct) ┤   (2 is required for 3.1, 3.2)
                 │
Phase 5 (CI) ────┘   (5 is required for 1.2, 1.4, 3.6 gates)

Phase 4 (polish) runs in parallel throughout, never on the critical path.
```

**Hard gates:**
- Don't start Phase 3 until §§1.1-1.3 are green.
- Don't start Phase 6 until §§3.1-3.5 are green.
- Phase 4 never blocks Phase 1.

---

## Quick wins (Day 0)

Cheap credibility repairs. Total effort: under one full workday.

| # | Action | Effort | Spec |
|---|---|---|---|
| QW-1 | `git rm --cached dist/ research_analysis.py` | 5 min | [2.5](docs/specs/phase-2-structural-cleanup.md#25-repo-cleanup) |
| QW-2 | Delete or integrate `sidecar/jupiter-perps/` (empty dir) | 5 min | [2.5](docs/specs/phase-2-structural-cleanup.md#25-repo-cleanup) |
| QW-3 | Rename "4-tier fill pipeline" → "3-stage pipeline with 4 impact models" everywhere | 15 min | [4.1](docs/specs/phase-4-product-polish.md#41-readme-rewrite) |
| QW-4 | Thread RNG seed through Rust `VenueFiller::new`, remove hardcoded `42` | 30 min | [1.1](docs/specs/phase-1-trust-correctness.md#11-rust-python-parity-fixes) |
| QW-5 | Move zero-volume warning out of `if can_use_rust:` block in `flint/backtest/engine.py` | 10 min | [1.1](docs/specs/phase-1-trust-correctness.md#11-rust-python-parity-fixes) |
| QW-6 | Add `FlintStore.get_live_equity_history` + `list_live_sessions`, remove raw `_conn` access from `flint/api/routes/live.py` | 1 hr | [2.2](docs/specs/phase-2-structural-cleanup.md#22-wrap-raw-store-access) |
| QW-7 | Fix Rust Sharpe annualization to trade-frequency (match Python MC path) | 30 min | [1.1](docs/specs/phase-1-trust-correctness.md#11-rust-python-parity-fixes) |
| QW-8 | Auto-generate strategy/test/endpoint counts in README via `scripts/build_docs.py` | 1 hr | [4.1](docs/specs/phase-4-product-polish.md#41-readme-rewrite) |
| QW-9 | Add `pytest --timeout=300` to CI | 5 min | [5.1](docs/specs/phase-5-ci-testing.md#51-matrix-and-macos) |
| QW-10 | Lazy-load Monaco editor in UI via `React.lazy` | 1 hr | [4.4](docs/specs/phase-4-product-polish.md#44-lazy-load-monaco) |
| QW-11 | Publish `TRUST_ARTIFACTS.md` status board | 2 hr | [1.7](docs/specs/phase-1-trust-correctness.md#17-trust-artifacts-status-board) |

---

## Trust artifacts status

Live tracker: [TRUST_ARTIFACTS.md](TRUST_ARTIFACTS.md). Updated on every trust-artifact merge.

## Deferred work

Items scoped in a phase but split into sibling PRs (review size, external validation, or UI coupling) live in [DEFERRED.md](DEFERRED.md). Every deferred item has a prerequisites list and effort estimate so pickup is mechanical.

---

## Overpromise-to-reality corrections (tracked under Phase 4.1)

| README/doc claim | Correction |
|---|---|
| "4-tier fill pipeline" | "3-stage pipeline with 4 impact models" |
| "10-50x Rust speedup, drop-in equivalent" | "Rust engine (experimental): up to Nx on supported features; declared capability matrix" |
| "676 tests" | Auto-generated count from `pytest --collect-only` |
| "20 built-in strategies" | Auto-generated count from `flint/strategy/*.py` minus infra |
| "Free data, nothing leaves your machine" (blanket) | Split into "Core data (Drift/HL/Pyth): free" vs "Optional (Birdeye/Helius/CCXT): user provides keys" |
| Aggressive Freqtrade/Hummingbot/TradingView comparison | Split into "DeFi perp wedge" and "adjacent tooling" tables |
| "Go live when ready" (Drift + HL) | Honest scope: CLI supports live, UI is monitor-only (until §6.5 ships) |

---

## Out of scope (permanent)

- General-purpose crypto trading platform
- Hosted / multi-user / SaaS
- Options, structured products, spot-only strategies
- MEV bots (sandwich, sniping) — research only, never product
- Data marketplace / resale / hosted data service
- Chains beyond Solana and Hyperliquid (and EVM-adjacent perps via HL)
- Mobile apps, cloud sync, remote access

---

## Appendix A — Audit origin

This plan derives from the 2026-04-23 audit across six parallel agents (Python core, Rust engine, UI, API/MCP, tests, docs/positioning) plus direct cross-verification. Key findings that drive the plan:

- **Rust/Python parity broken** in Sharpe annualization, RNG seeding, tx_cost plumbing, zero-volume warning. Same backtest returns different numbers on the two engines.
- **Trust artifacts (ROADMAP §1) largely unshipped**: 0.5 of 7 items actually live.
- **ExecutionContext sprawl**: 5 parallel hierarchies, one 973-line god class, ~800 LOC of duplication.
- **Route-level DuckDB leaks** in `flint/api/routes/data.py` + `live.py` violate the store abstraction rule.
- **README overpromises** (4-tier claim, 10-50x drop-in, stale test counts).
- **CI is Linux-only, Py3.11-only, no Rust build, swallows install errors.**
- **Dead/stale artifacts**: `dist/flint_trading-1.1.0-*.whl` committed on a 1.3.1 repo; `research_analysis.py` orphan; `sidecar/jupiter-perps/` empty.

Full audit available in conversation log; key excerpts preserved in each phase spec.

---

## Appendix B — Plan ownership

Each phase spec lists `owner: TBD` fields. Before work begins on a phase, an owner claims it by filing a PR that fills in the `owner:` line and adds an entry to `TRUST_ARTIFACTS.md` with an ETA.
