# Flint — AI Development Guide (greenfield rewrite)

Flint is a local-first backtesting, paper, and live lab for perp/DEX strategies.
Hyperliquid-native, venue-agnostic core. This branch (`redesign/greenfield`) is a
ground-up rewrite on a **ports-and-adapters** architecture.

**Canonical spec:** `docs/redesign/DESIGN.md`. Read only the sections you need
(it is ~1600 lines) — every subsystem cites its section (§5 models, §6 engine,
§8 strategy, §9 data, §2.7/§4/§17 architecture). The build is decomposed into
phases in §18; each phase is a board task.

## Quick reference

```bash
python3.12 -m venv .venv && source .venv/bin/activate   # Python 3.12–3.14 required
pip install -e ".[dev]"        # editable install + pytest/ruff
pytest tests/ -v               # all tests — fully mocked, no network, no keys
python scripts/codemap.py      # regenerate docs/codemap/ shards after structural changes
python scripts/codemap.py --check   # CI: fail if codemap is stale
```

The repo uses the project `.venv` (Python 3.12). Bare `python3` on this host is
3.9 and will **not** satisfy `requires-python >=3.12,<3.15` — always use `.venv/bin/python`.

## Architecture in one screen (§4, §17)

Dependency flow is strictly one-directional. Nothing lower reaches up.

```
Surfaces      api/   sdk/   mcp_srv/   agent/        (talk ONLY to services/)
                     │
Application   services/     ← every function takes a TenantContext
                     │
Domain core   engine/  research/  strategy/  core/   (pure logic, no I/O)
                     │
Venues        venues/  (hyperliquid = only executable v1 venue; jupiter v1.x)
                     │
Ports         ports/   MarketDataPort · UserDataPort · JobRunnerPort ·
                       SecretsPort · EventBusPort · Identity
                     │
Adapters      adapters/  (v1: all local — DuckDB, in-memory bus, .env, in-proc jobs)
```

Package map (`flint/`, see §17 for the full annotated tree):

| Package | Owns |
|---|---|
| `core/` | Pure models (`models/`) + time/no-lookahead rules (`time/`). No I/O; depends on nothing. |
| `engine/` | Simulator/executor shared by backtest+paper: `context/` (7 managers), `fills/`, `funding/`, `liquidation/`, `portfolio/` (event log + replay). |
| `venues/` | One adapter per exchange, keyed by market structure. `hyperliquid/`, `jupiter/`. |
| `data/` | On-demand data layer: DataManager source chain, `livefeed/`, `ingest/`, `store/`. |
| `strategy/` | User surface: base class, Signal, read-only ctx, `sandbox/` (OS isolation), `templates/`. |
| `research/` | Walk-forward, Deflated Sharpe, look-ahead linter, optimize, tearsheet, Run Library. |
| `live/` | Minimal v1 live executor (HL only, caps, kill switch); same code path as paper. |
| `ports/` | The interfaces + `TenantContext`. |
| `adapters/` | Concrete local port implementations. |
| `services/` | The front door; every function takes `TenantContext`. |
| `api/` `sdk/` `mcp_srv/` `agent/` | Surfaces. `mcp_srv` (not `mcp`) avoids shadowing the pip `mcp` package. |

## Rules (these override defaults — follow exactly)

- **The engine never touches storage directly.** All I/O goes through `ports/`.
  Surfaces (`api/`, `sdk/`, `mcp_srv/`, `agent/`, `ui/`) talk **only** to `services/` —
  never reach into the engine or a store from a surface.
- **Every `services/` function takes a `TenantContext`.** Every `UserDataPort` call
  is tenant-scoped — the scoping predicate is on every query, enforced by the
  two-tenant cross-leak contract test. There is no "default tenant" shortcut.
- **No synthetic data, anywhere, ever (D26).** Tests use hand-authored unit inputs
  or real recorded fragments — never generated "market-like" data. No random price
  series, no fabricated fills.
- **Funding is a hard gate (§6.4).** A backtest over a window without real funding
  data is *rejected* with available ranges and the fix — it is never silently
  filled, interpolated, or zero-defaulted. Data scarcity surfaces as structured
  `rejected`/`degraded` payloads, not errors and not stack traces (§19.1).
- **DRIFT IS DROPPED.** Drift Protocol is not a supported venue. Do not add Drift
  code, deps, docs, or UI/MCP narrative. Hyperliquid is the live venue today; Jupiter
  and Phoenix are planned expansion.
- **Numeric policy (§5):** `Decimal`/scaled-int for monetary accumulators, integer
  unix-ms UTC timestamps (bar START), floats for prices. Never float-accumulate money.
- **Sandbox is the security boundary (§8.3, D25):** user strategy code runs in an
  OS-isolated subprocess (env-scrub + RLIMIT floor on macOS; nsjail/seccomp on Linux).
  The AST import-allowlist is lint-grade UX, **not** the boundary.
- **Regenerate the codemap after any structural change:** `python scripts/codemap.py`.
  CI runs `--check`.

## Git / workflow

- The greenfield redesign merged to `main` at v2.0.0 — `main` is the working branch. Never `git push --force` on it.
- Explicit pathspecs only on mixed working trees; never `git add -A` blindly.
- Never commit `.env` (API keys) or user strategies under `strategies/user/`.
- The greenfield deletes old *code*, never old *data* — the legacy DuckDB is imported,
  not discarded (§19.6).
- Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Working with the code (token discipline)

- Serena MCP is available: use `get_symbols_overview` / `find_symbol` before any
  full-file Read once code exists.
- Consult `docs/codemap/` shards before grepping.
- Tests are fully mocked. The engine injects fake ports; no network or keys are needed.
