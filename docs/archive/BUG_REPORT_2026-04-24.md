# Bug Report — Smoke Run 2026-04-24

Post-commit smoke test of branch `restructure` (8 commits: plan + docs
restructure + Phases 1-6). Full end-to-end run: rebuild UI, restart
server on `:8100`, curl key REST endpoints, drive UI via Playwright,
watch browser network requests.

**4 bugs surfaced. All 4 fixed via red→green loop.**
Regression tests live in `tests/test_smoke_regressions.py` (4 tests, all
green on current `restructure` branch).

---

## Setup

| Component | State |
|---|---|
| Branch | `restructure` (8 commits, local only) |
| Rust engine | rebuilt via `cd rust && maturin develop --release` |
| UI | `npx vite build` (tsc strict mode skipped — pre-existing errors in test files) |
| Server | `flint serve --port 8100`, PID varied across test runs |
| Browser | Playwright Chromium via MCP |

---

## BUG-1 — `/api/v1/live/sessions` returns DuckDB Binder Error

**Severity:** High — API surface broken, would affect any UI call to the
Live page.

**Symptom:**
```
GET /api/v1/live/sessions → 200 OK
{
  "error": "Binder Error: Referenced column \"strategy\" not found in
            FROM clause!\n
            Candidate bindings: \"strategy_name\", \"started_at\",
            \"status\", \"stopped_at\", \"market\""
}
```

**Root cause:** Introduced by Phase 2 T2.2 commit (`46c7a15`). The new
`FlintStore.list_live_sessions` method wrote `SELECT session_id, strategy,
market, venue, ...` but the actual `live_sessions` table column is
`strategy_name`. Pre-migration, the route ran its own SQL (also broken),
but the symptom only surfaced after the migration collapsed the bad SQL
into one spot where a smoke run hit it.

**Fix:** `flint/store.py:list_live_sessions` — rename column to
`strategy_name` in the SELECT list. The Python-side dict key stays
`strategy` so the UI contract is unchanged.

**Test:** `tests/test_smoke_regressions.py::TestLiveSessionsColumnNameFix`
asserts the endpoint returns `{"sessions": [...]}` not an error dict.
Red-confirmed by reverting the column rename (saw the original Binder
Error surface), then re-applied fix to go green.

---

## BUG-2 — `engine_used` + `fallback_reason` dropped from backtest API response

**Severity:** Medium — telemetry shipped in Phase 3 T3.2 was invisible
to UI consumers, making the Rust-fallback surface from Phase 4.6
capabilities useless.

**Symptom:**
```
POST /api/v1/backtest/run → run_id
GET  /api/v1/backtest/{run_id}/results → body.results has 19 keys:
  benchmark_label, buy_hold_equity, data_quality, drawdown_curve,
  equity_curve, initial_capital, instrument_exposure, market,
  markets_used, metrics, monthly_returns, period_end, period_start,
  resolution_s, rolling_sharpe, strategy_name, trade_breakdown, trades
  ...
  NO engine_used, NO fallback_reason
```

The `BacktestResult` dataclass (modified in commit `c11acad`) carries
`engine_used: str = "python"` and `fallback_reason: Optional[str] = None`,
populated correctly by `_run_internal`. But
`flint/api/routes/backtest.py:~676` builds the response via
`tearsheet.to_dict()` — which doesn't read these fields. UI / MCP clients
had no way to see which engine ran.

**Fix:** `flint/api/routes/backtest.py` — after `ts_dict =
tearsheet.to_dict()`, explicitly add:
```python
ts_dict["engine_used"] = getattr(result, "engine_used", None)
ts_dict["fallback_reason"] = getattr(result, "fallback_reason", None)
```

**Test:**
`tests/test_smoke_regressions.py::TestEngineUsedTelemetryInAPIResponse`
runs a real backtest through `TestClient`, asserts both fields present +
`engine_used ∈ {"rust", "python"}`. Red-confirmed by removing the fix
(saw "engine_used missing from results; keys=[...]"), then re-applied.

---

## BUG-3 — Four-way version mismatch across pyproject, API, UI, importlib

**Severity:** Medium — trust signal. Copy says 1.3.1; API says 0.1.0; UI
says 0.3.0. No user can tell which build they're on.

**Symptom:**
| Surface | Returned version |
|---|---|
| `pyproject.toml` | `1.3.1` |
| `importlib.metadata.version('flint-trading')` | `1.1.0` (stale egg-info from old `pip install -e .`) |
| `importlib.metadata.version('flint')` | `0.2.0` (separate phantom package) |
| `/api/v1/capabilities` → `version` | `0.1.0` |
| `/api/v1/system/status` → `version` | `0.1.0` |
| UI footer | `FLINT v0.3.0` (hardcoded in `App.tsx:132`) |

**Root cause:** `flint/api/routes/system.py:_get_version` called
`importlib.metadata.version("flint")` — wrong distribution name. Flint's
actual distribution is `flint-trading` (per pyproject `name =
"flint-trading"`). Stale `flint` egg-info from an earlier install leaked
through. UI footer was hardcoded and never linked to anything.

**Fix:**
- `flint/api/routes/system.py:_get_version` — new preference order:
  1. Read version directly from `pyproject.toml` (authoritative).
  2. `importlib.metadata.version("flint-trading")`.
  3. `importlib.metadata.version("flint")` (legacy fallback).
  4. `"0.0.0"`.
- `ui/src/App.tsx` — footer now `FLINT v{version}`, state initialized
  from `/api/v1/capabilities` on mount. Falls back to `?.?.?` if probe
  fails (ConnectionBanner handles the error surface).

**Test:** `tests/test_smoke_regressions.py::TestVersionConsistency`
parses `pyproject.toml` directly, asserts both `/api/v1/capabilities`
and `/api/v1/system/status` return that exact version. Red-confirmed
with pre-fix code (got `0.2.0` vs expected `1.3.1`).

Post-fix browser verification via Playwright:
```
footerText: "FLINT v1.3.1\nStrike alpha on Solana\nDRIFT · JUPITER · DUCKDB"
```

---

## BUG-4 — Monaco editor loaded from `cdn.jsdelivr.net` (local-first violation)

**Severity:** High — violates the product's most load-bearing promise.

**Symptom:** Opening `/backtest` triggered 14 GET requests to
`cdn.jsdelivr.net`:
```
GET cdn.jsdelivr.net/npm/monaco-editor@0.55.1/min/vs/loader.js
GET cdn.jsdelivr.net/.../editor/editor.main.js
GET cdn.jsdelivr.net/.../monaco.contribution-{DO3azKX8,qLAYrEOP,EcChJV6a,D2OdxNBt}.js
GET cdn.jsdelivr.net/.../basic-languages/monaco.contribution.js
GET cdn.jsdelivr.net/.../editor.api-CalNCsUg.js
GET cdn.jsdelivr.net/.../workers-DcJshg-q.js
GET cdn.jsdelivr.net/.../editor/editor.main.css
GET cdn.jsdelivr.net/.../python-Cr0UkIbn.js
GET cdn.jsdelivr.net/.../assets/editor.worker-Be8ye1pW.js
```

All 14 requests successful. Flint's README top line and home page hero
both claim **"local-first, nothing leaves your machine"**. Every
non-MEV page load was quietly pinging jsdelivr.

**Root cause:** `@monaco-editor/react`'s default loader fetches Monaco
assets from the jsdelivr CDN. Without calling `loader.config({ monaco })`
early in the app, the library never knows to use the local bundle.

**Fix:** `ui/src/components/CodeEditor.tsx` — import monaco-editor as a
module + invoke `loader.config({ monaco })` at module load time:
```tsx
import { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor'
loader.config({ monaco })
```

Vite now bundles Monaco + all language chunks into the local JS output.
Bundle size grew (+~4MB of new `vs/*` chunks in `ui/dist/assets/`), but
Monaco ships entirely offline.

**Verification:** Playwright network trace after the fix:
```
cdnCount: 0   (was ~14)
monacoMounted: true
```

All 14 jsdelivr requests disappeared; editor still works.

**Test:**
`tests/test_smoke_regressions.py::TestMonacoLoadsLocallyNotFromCDN` is a
source-level assertion — checks that `CodeEditor.tsx` imports
`monaco-editor` and calls `loader.config`. Browser-level network testing
is out of scope for pytest; Playwright verified the runtime behavior
directly on 2026-04-24.

---

## Non-fatal observations (NOT filed as bugs)

| # | Finding | Status |
|---|---|---|
| O-1 | UI `tsc -b` build command fails on pre-existing TS errors in `ui/src/test/*` files (unused imports, missing `beforeEach`, etc). `npx vite build` skips tsc and works. Pre-existing WIP. | Defer to D-TS-fixes (not new with this restructure) |
| O-2 | Backtest API returns JSON with NaN/Inf for very small candle windows (10 bars) → `ValueError: Out of range float values are not JSON compliant`. Python's stdlib `json.dumps` requires `allow_nan=False` handling. Not triggered by normal UI flows. | Defer to D-NaN-sanitize |
| O-3 | Vite build warning: main bundle still > 500KB after Monaco split. Could be further code-split but functional. | Defer to D-bundle-split |
| O-4 | `ineffective_dynamic_import` warning from Vite — `CodeEditor.tsx` statically imports `@monaco-editor/react` (for loader) + dynamically imports it (for lazy). Works; warning noisy. | Low priority |
| O-5 | Footer hardcoded version was `v0.3.0` — doesn't match ANY other surface. Fixed as part of BUG-3. | — |

---

## Regression test location

All 4 bug tests are in a single file: `tests/test_smoke_regressions.py`.
They run under the default `pytest tests/` invocation + in the CI
matrix (Phase 5). Each test class has a docstring explaining pre-fix
symptom + root cause + fix.

## Next steps

- Commit the BUG-1/2/3/4 fixes as a single follow-up commit on
  `restructure`.
- Add the observations (O-1 to O-5) to `DEFERRED.md` with ETAs if they
  warrant tracking beyond this report.
- Re-run full `pytest tests/` sweep after commit to confirm no new
  regressions from the fixes.
