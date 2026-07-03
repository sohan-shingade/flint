# Flint greenfield — build status

The `redesign/greenfield` branch is a ground-up rewrite of Flint on a
ports-and-adapters architecture (canonical spec: [`DESIGN.md`](DESIGN.md)). This
document is the honest ledger of what shipped, what is degraded, and what is
explicitly deferred to v1.x. It is written to be believed: where something is a
shim, a stub, or a not-yet, it says so.

**Test baseline at end of build:** `pytest tests/ -q` → **679 passed, 4 skipped**
(the 4 skips are optional-ML-dependency guards). UI: `ui/` vitest **151 passed**.
Repo-wide `ruff check` clean. Codemap regenerated (`scripts/codemap.py --check`
green, 33 shards).

---

## What shipped, per phase

Commit hashes are the last commit of each slice on `redesign/greenfield`.

### Phase 1 — walking skeleton + security floor
| Slice | Commit | What |
|---|---|---|
| 1.1 scaffold | `177fa18` | Greenfield package tree (§17), pyproject, CLAUDE.md; legacy code dropped. |
| 1.2 core models | `17ac165` | Pure domain models + time / no-look-ahead helpers (§5, §8.2). |
| 1.3 ports + adapters | `86ecf36` | The six ports + local adapters + the two-tenant cross-leak contract test (§2.7). |
| 1.4 event log | `ac7946a` | Emit seam + `event_version` + upcaster registry (§2.10). |
| 1.5 sandbox skeleton | `f5c33ad` | OS-isolation subprocess + RLIMIT + env-scrub (§8.3, D25). |
| 1.6 services front door | `0449295` | `services/` composition root; every call takes a `TenantContext`. |

### Phase 2 — on-demand data layer + HL ingestion + Data API
| Slice | Commit | What |
|---|---|---|
| 2.1 durable store | `751d93e` | DuckDB store adapters + lake layout/cache (§9.0). |
| 2.2 DataManager | `e04565e` | Source chain + the funding **hard gate** (§9, §2.11). |
| 2.3 universe | `dd78436` | Point-in-time membership + exit behavior (D27). |
| 2.4 HL REST + S3 | `13a1967` | HL REST provider + S3 backfiller + ingestion quality bars (§9.1). |
| 2.5 HL WS recorders | `7984f46` | HL WS recorders + Pyth poller + shared normalize (§9.1, §6.7). |
| 2.6 CEX funding | `8fb4370` | Read-only CEX funding (+OI) ingestion via mocked CCXT. |
| 2.7 Data API | `9acb779` | Data API service + local client + durable cache + BYO-vendor lane. |
| 2.8 legacy import | `69aa0ed` | One-shot legacy Flint v1.x DuckDB importer (§19.6). |
| gate scoping | `4260b7f` | Funding hard gate scoped to executable venues; signal-only legs degrade, not reject. |

### Phase 3 — the honest engine
| Slice | Commit | What |
|---|---|---|
| 3.1 per-bar loop | `dfc3fb9` | The locked funding→liquidation per-bar sequence (§6.1). |
| 3.2 fill fidelity | `1a73eee` | CLOB fill tiers A/B/C, chosen by market structure (§6.3). |
| 3.3 funding | `d8fa2fb` | Predicted/final split, cap, oracle-priced settlement (§6.4). |
| 3.4 liquidation | `b1bfd0b` | Tiered maintenance, cross-pool cascade, HL backstop (§6.5). |
| 3.5 accounts + orders | `1f5d39d` | Per-venue accounts, the persisted order state machine, replay/fold. |
| 3.6 strategy iface | `fc00291` | Strategy seam, §8.1 Signal→Order conversion, §8.2 ctx, acceptance. |

### Phase 5 — strategy surface, ML, LiveFeed + paper
| Slice | Commit | What |
|---|---|---|
| 5.1 Strategy API | `32b9696` | Public `Strategy` API + engine bridge (§8.1, D21, D28). |
| 5.2 sandbox screen | `cd786ce` | Line-precise AST screen + ctx value-object proof (§8.3). |
| 5.3 ML | `eb977ff` | Declarative ML strategy, managed model store, feature-causality screen. |
| 5.4 templates | `ed23b8a` | Built-in template registry — 9 templates incl. one LightGBM ML. |
| 5.5 LiveFeed | `015daab` | Venue-event clock + reconnect gap replay (§6.7). |
| 5.6 paper | `f01044f` | The `PaperSession` runner + drift + alerts (§6.7) — restart-safe via the event log. |

### Phase 6 — trust tooling + funding/basis lab
| Slice | Commit | What |
|---|---|---|
| 6.1 walk-forward | `87eea19` (+ `b8df28c` GA) | Walk-forward + Optuna TPE (and a genetic optimizer). |
| 6.2 metrics | `93c373b` | Deflated Sharpe + §11.1 metrics, pinned by goldens (D22). |
| 6.3 lookahead linter | `bd199c0` | Look-ahead / leakage linter (§8.5, D27). |
| 6.4 Run Library | `85f4c78` | Run Library + reproducibility export (§11.2). |
| 6.4b legacy runs | `275a0de` | Legacy run-metadata extraction into the Run Library. |
| 6.5 funding lab | `29ef8da` | Cross-venue funding & basis lab (§10, D28). |

### Arrow-native fill path (§19.4 spike follow-up)
| Commit | What |
|---|---|
| `7ad6f3d` | Arrow-native Tier-A taker book-walk. |
| `768ce9e` | Additive per-bar EQUITY event (§6.1). |

### Phase 7 — surfaces + live executor
| Slice | Commit | What |
|---|---|---|
| 7.1 REST/WS API | `3c4065f` | FastAPI → services only; per-session bearer token + Origin check; real-engine backtest front door (§12). |
| 7.2 SDK + CLI | `da99e44` | `Lab` object + the `flint` CLI; structured JSON logs; §19.2 timing (§12). |
| 7.3 MCP agent | `b3345fc` | `mcp_srv/` + `agent/` — 8 JSON tools; user-source validate+sandbox path (§13). |
| 7.4a UI 1–3 | `1316bd5` | Results/tearsheet, funding heatmap, data explorer; new API-client app shell. |
| 7.4b UI 4–5 | `f2878f9` | Live monitor + run library; funding-lab endpoint; legacy pages deleted (D1). |
| 7.6 in-sandbox run | `ed60ede` | Validated user-source backtests run **inside** the OS sandbox (D25 closure). |
| 7.5 live + closeout | `14b81bd` (+ docs) | HL live executor (caps, kill switch, reconciliation); Part-B cleanup; this closeout (README, STATUS, codemap). |

---

## The HL live executor (7.5) — what is real vs deferred

**Real and tested** (against a mocked venue client — no real key, no real order, D26):

- The D20 safety contract: a live run refuses to start without a positive
  `--max-position-usd` and a venue signing key (resolved server-side from the
  `SecretsPort`; the key lives only in the client, never in the browser or a log line).
- Pre-trade **position cap** — a non-reduce-only order that would breach the notional
  cap is rejected as structured data before it reaches the venue.
- **Daily-loss halt** — crossing `--max-daily-loss-usd` cancels resting orders,
  flattens open positions, and refuses new orders.
- The **kill switch** — `flint live --stop --run-id <id>` / `--all` / `--flatten`, plus
  the UI-callable `POST /api/v1/live/{run_id}/stop`.
- **Reconciliation** — on reconnect, local folded state is diffed against the venue
  clearinghouse and mismatches surface as structured drift alerts; neither side is
  silently adopted.
- The order state machine and event-log persistence are the **same** ones paper and
  backtest use, so a folded live book matches a folded backtest book field-for-field
  (restart-safety proven by a resume-folds-without-double-counting test).

**Deferred to v1.x** (documented, not hidden):

- **Real Hyperliquid order transport.** `HyperliquidLiveClient` is a structurally
  complete shim: it holds the signing key privately and each call raises
  `LiveVenueUnavailable` with an actionable message until the real signing/HTTP
  transport is wired. The executor logic above is complete and proven against the
  `LiveVenueClient` seam; `flint live` arms a run (persists the head, enforces caps,
  readies the kill switch) but places no order in this build.
- **The continuous live-feed submit loop** (LiveFeed → per-bar strategy → `submit`)
  is not wired for live, mirroring the recorder WebSocket deferral below.

---

## Standing deferrals (v1.x)

**D28 venue/scope deferrals** (by design, out of v1 scope):
- Binance (and other CEX) **execution** — CEX data is read-only in v1.
- Jupiter oracle-pool executable venue (data/lab only in v1); Phoenix expansion.
- Cross-venue two-leg backtests.
- Phase 4 (its scope folded forward / not part of this build).

**Serve-time composition:**
- Real DuckDB user/market adapters + a wired `DataManager` provider chain at `flint
  serve` — `create_app`/`Lab` default to in-memory adapters + a bare `DataManager`;
  the durable adapters are injected at serve (7.1 deferral #3, still open).
- The `flint` console script is declared in `pyproject` but the editable install
  predates it — run `pip install -e .` once to put `.venv/bin/flint` on PATH. The CLI
  works today via `python -m flint.sdk.cli …`.

**Feature deferrals across the build:**
- **User-source optimize is template-only** — source strategies validate + backtest
  today; threading a compiled spec through the walk-forward runner is additive future
  work.
- **Online / incremental ML** — the model store is batch-fit in v1.
- **Dedicated alerts store** — alert configs are held in-memory per tenant (5.6-g).
- **GapSource lake-adapter wiring** — the LiveFeed gap-recovery source is not yet
  backed by the durable lake adapter.
- **`fill_mode` override** — recorded in the run summary but does not override the
  engine's structure-driven fill-tier selection (Tier A/B/C is data-driven).
- **Reproduce CLI verb** — `export --run-id` emits the ReproBundle and
  `runlib.reproduce`/`BundleRunner` exist as library functions, but no `flint
  reproduce` verb was added.
- **Recorder WebSocket connect** — `recorder start` prints the capture plan; the live
  venue WebSocket connect is a foreground self-hosted process not exercised in the
  mocked suite (§20).
- **Real dense-day Tier-A spike re-run** — needs HL S3 archive credentials; the
  Arrow fill path is anchored to the canonical recorded baseline instead.

**Cosmetic (non-blocking):**
- `ui/src/index.css` has a CSS `@import`-order warning (fonts import after
  `@import "tailwindcss"`); build is green.

---

## Honesty guarantees (enforced, not aspirational)

- **Funding hard gate** (§6.4) — a backtest over a window without real funding data is
  *rejected* with the available ranges and the fix, never zero-filled.
- **No synthetic data** (D26) — hand-authored unit inputs or real recorded fragments
  everywhere; no generated price series, no fabricated fills.
- **Deflated Sharpe always shown** (§11.1) — `n/a (N trials)` when untuned, the real
  DSR over the trial family when optimized; raw Sharpe + annualization + effective
  range beside every metric.
- **The sandbox is the boundary** (D25) — user code runs OS-isolated for the full run,
  not just a pre-flight probe; the AST lint is UX, not the guarantee.
- **Structured scarcity** (§19.1) — expected scarcity is a `rejected`/`degraded`
  payload; only faults are errors; a real bug is loud (500 + incident id).
- **No Drift** (D28) — Drift Protocol is not a supported venue anywhere in the build.
