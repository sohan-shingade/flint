# Phase 4 — Product Polish

**Owner:** TBD
**Duration:** 4-6 weeks (runs parallel to Phases 1-3)
**Blocks:** nothing (never on critical path)

Honest positioning, better UX, lighter dependencies. Closes the gap between what's built and what's advertised.

---

## Items

- [4.1 README rewrite](#41-readme-rewrite)
- [4.2 UI error recovery](#42-ui-error-recovery)
- [4.3 Paper/Live WebSocket](#43-paperlive-websocket)
- [4.4 Lazy-load Monaco](#44-lazy-load-monaco)
- [4.5 Backtest/optimization cancellation](#45-backtestoptimization-cancellation)
- [4.6 /api/v1/capabilities endpoint](#46-apiv1capabilities-endpoint)
- [4.7 MCP in-process mode](#47-mcp-in-process-mode)

---

## 4.1 README rewrite

**Goal:** copy matches code; wedge is clear; counts auto-regenerate so they never go stale again.

### Corrections (from audit)

| Current | Replacement |
|---|---|
| "4-tier fill pipeline" | "3-stage fill pipeline (latency → impact → partial) with 4 impact models (vAMM, orderbook, sqrt, flat)" |
| "10-50x Rust speedup, drop-in" | "Rust engine (experimental): up to Nx on supported features; [capability matrix](docs/reference/rust-engine.md)" |
| "676 tests" | Auto-generated from `pytest --collect-only -q` |
| "20 built-in strategies" | Auto-generated from `ls flint/strategy/*.py` minus `__init__`/`base`/`loader` |
| "15 data sources" | Split into: "Candle providers: N", "Funding venues: M", "Optional (keys needed): K" |
| "61 REST endpoints" | Auto-generated from OpenAPI schema count |
| "17 MCP tools" | Auto-generated from `grep -c '@mcp.tool()' flint/mcp_server.py` |
| Blanket "Free data, nothing leaves your machine" | "Core data (Drift/HL/Pyth): free, local. Optional providers (Birdeye/Helius/CCXT): you provide keys, still local." |
| Aggressive Freqtrade/Hummingbot/TradingView comparison | Two narrower tables: "vs DeFi-perp-native tooling" and "vs general crypto platforms" |
| "Go live when ready" implying UI live | "CLI supports live Drift/HL execution today. UI live deployment lands in Phase 6.5." |

### Tasks

**T4.1.a — Counts from `scripts/build_docs.py`**
- Script emits `README.md` section `<!-- counts:auto -->` / `<!-- /counts:auto -->` with current numbers.
- Runs in CI; fails if numbers are stale.

**T4.1.b — Wedge-first copy**
- Replace hero: "Local backtester and paper-trading lab for Drift and Hyperliquid perp strategies. One command to install, free data, nothing leaves your machine."
- Drop CCXT from the hero. Move to a "Data providers" section further down.

**T4.1.c — Split comparison**
- Table 1: "vs Solana/Drift-native tooling" — shows Flint-only ground.
- Table 2: "vs general crypto platforms (Freqtrade, Hummingbot)" — shows overlap honestly; marks CEX-live as "planned".
- Remove TradingView comparison (different product category — chart lib ≠ backtester).

**T4.1.d — Trust-first "Try It" section**
- Replace `examples/canonical_backtest.py` with `jupyter lab notebooks/funding_arb.ipynb` (Phase 1.5).
- Remove the `examples/parity_test_example.py` mention; replace with "see `artifacts/parity/` for checked-in reports."

**T4.1.e — Add TRUST_ARTIFACTS status badge**
- Badge from a GitHub Action that reads `TRUST_ARTIFACTS.md` and colors based on shipped count.

### Acceptance

- CI job fails if README counts don't match code.
- No claim in README lacks a link to either code or a proof artifact.
- Audit re-run: overpromise-to-reality table has zero open items.

### Effort

~2 days for copy, ~4 hours for auto-counts.

---

## 4.2 UI error recovery

**Problem:** most UI pages use `.catch(() => {})` on fetches. If the server dies mid-session, the UI freezes silently and users make decisions on stale data.

### Tasks

**T4.2.a — Global "connection lost" banner**
- `ui/src/components/ConnectionBanner.tsx` — shows when any critical poll has failed N times.
- States: green (connected), yellow (degraded — 1-2 failures), red (disconnected — 3+ failures), amber (reconnecting).

**T4.2.b — Polling backoff**
- All hooks in `ui/src/hooks/` — 1s → 2s → 5s → 10s → 30s exponential on failure; reset to 1s on success.
- Cap at 30s.

**T4.2.c — Remove silent catches**
- Grep: `grep -rn '\.catch(() => {})' ui/src/` — should return zero hits after this task.
- All errors either displayed, logged to console with context, or rethrown.

**T4.2.d — Retry button**
- On the red banner: "Retry now" button forces an immediate poll.

### Acceptance

- Kill the API server mid-session; UI shows red banner within 10 seconds.
- Restart server; UI shows green within one poll cycle.
- No silent `.catch` remaining.

### Effort

~3 days.

---

## 4.3 Paper/Live WebSocket

**Problem:** `vite.config.ts` already proxies `/ws`, but nothing uses it. Paper and Live pages poll every 2s. 60 polls/minute × N sessions × M tabs.

### Tasks

**T4.3.a — Server: `/ws/paper/{session_id}`**
- New: `flint/api/websocket.py:paper_session_ws`.
- Streams `{equity, unrealized_pnl, last_trade}` on each engine tick.
- Handles reconnect: client sends last-seen `equity_ts`, server replays missed points.

**T4.3.b — Server: `/ws/live/{session_id}`**
- Same pattern for live sessions.

**T4.3.c — UI: `useWebSocket` hook**
- New: `ui/src/hooks/useWebSocket.ts` — generic reconnecting WS hook.
- Drop-in replacement for the poll hooks in `usePaperTrading` and `useLiveMonitor`.

**T4.3.d — Fallback to polling**
- If WS fails to connect, fall back to the existing poll path with banner notification.

### Acceptance

- Paper page with one session: 0 HTTP polls in 60s (only WS messages).
- Server restart: WS reconnects within 5s without UI freeze.
- Fallback path works (test by blocking WS in browser dev tools).

### Effort

~1 week.

---

## 4.4 Lazy-load Monaco

**Problem:** `ui/src/components/CodeEditor.tsx` imports `@monaco-editor/react` at top level. BacktestLab loads ~1MB of Monaco on first mount even if user doesn't edit code.

### Tasks

**T4.4.a — `React.lazy` wrap**
```tsx
// ui/src/components/CodeEditor.tsx
import React, { Suspense } from 'react';

const MonacoEditor = React.lazy(() => import('@monaco-editor/react'));

export function CodeEditor(props: CodeEditorProps) {
  return (
    <Suspense fallback={<div className="editor-loading">Loading editor...</div>}>
      <MonacoEditor {...props} />
    </Suspense>
  );
}
```

**T4.4.b — Measure**
- Before/after bundle size check.
- Lighthouse: FCP improvement measured on Lab page.

### Acceptance

- Initial bundle on `/` (Dashboard) drops by ~800KB.
- Navigating to `/backtest` shows fallback briefly, then renders editor.

### Effort

~1 hour.

---

## 4.5 Backtest/optimization cancellation

**Problem:** `POST /api/v1/backtest/{id}/cancel` exists but doesn't actually signal the worker. UI closing tab leaves thread running.

### Tasks

**T4.5.a — Worker-thread cancellation**
- `flint/api/routes/backtest.py` — worker thread checks `entry.status` at each bar boundary; exits cleanly if set to `"cancelled"`.
- Same for `flint/api/routes/optimization.py`.

**T4.5.b — UI: cancel on unmount**
- `ui/src/hooks/useBacktest.ts` — on hook cleanup, POST to `/cancel` with the run ID.

**T4.5.c — Server: cancel on disconnect**
- FastAPI request cancellation: on disconnect, if request is mid-flight, set the corresponding backtest/optimize entry to `cancelled`.

### Acceptance

- Start a 100-trial optimization. Close the tab. Server log shows "optimization cancelled" within one trial.
- Concurrent slot freed immediately.

### Effort

~1 day.

---

## 4.6 /api/v1/capabilities endpoint

**Goal:** UI, MCP, and external clients can feature-flag based on what the server supports.

### Tasks

**T4.6.a — New route**
```python
@router.get("/capabilities")
def get_capabilities():
    return {
        "version": VERSION,
        "api_version": "v1",
        "features": {
            "live_trading_api": False,  # true after Phase 6.5
            "mev_scanning": True,       # ArbDetector / LiquidationScanner
            "custom_strategies": True,
            "optimization": True,
            "walk_forward": True,
            "rust_engine": RustEngine.available(),
            "rust_capabilities": RustEngine.capabilities().to_dict() if RustEngine.available() else None,
            "parity_test": True,
            "reconciliation": TRUST_1_4_SHIPPED,
        },
        "limits": {
            "max_concurrent_backtests": _MAX_CONCURRENT,
            "backtest_timeout_s": _MAX_BACKTEST_SECONDS,
        },
    }
```

**T4.6.b — UI feature flags**
- `ui/src/hooks/useCapabilities.ts` — fetches once on load, caches.
- Pages hide sections when capability is false.

**T4.6.c — MCP version check**
- `flint/mcp_server.py` — `get_server_info()` tool reports same capabilities.

### Acceptance

- `curl localhost:8000/api/v1/capabilities` returns valid JSON with all feature flags.
- UI hides MevDashboard if `mev_scanning: false`.
- Capability matrix drives the README feature-completeness table.

### Effort

~2 hours.

---

## 4.7 MCP in-process mode

**Problem:** MCP paper-trading tools internally HTTP-call the REST API (`mcp_server.py` reaches `/api/v1/paper/*`). Double serialization, and MCP fails if the FastAPI server isn't running.

### Tasks

**T4.7.a — Shared service layer**
- New: `flint/services/` — one module per domain (backtest, paper, optimization, data, journal).
- Each service exposes a plain Python API. No FastAPI dependency.

**T4.7.b — Routes become thin adapters**
- `flint/api/routes/backtest.py:run_backtest` becomes: validate request → call `flint.services.backtest.run(config)` → serialize.

**T4.7.c — MCP tools call services directly**
- `flint/mcp_server.py:start_paper_trading` calls `flint.services.paper.start(config)` — no HTTP.

**T4.7.d — State sharing**
- Services share the same `FlintStore` singleton via `app.state.store` or an equivalent process-local registry.
- MCP process gets its own store when running standalone.

### Acceptance

- `python -m flint.mcp_server` works without `flint serve` running.
- `/api/v1/paper/start` and MCP `start_paper_trading` produce identical outputs on the same input.
- No HTTP calls inside MCP handlers (grep `httpx\|requests\|urllib` in `flint/mcp_server.py` returns zero).

### Effort

~2-3 days.

---

## Dependencies

```
4.1 (README)       ── independent
4.2 (errors)       ── independent
4.3 (WebSocket)    ── independent (but smoother after 4.2)
4.4 (lazy Monaco)  ── independent
4.5 (cancellation) ── independent
4.6 (capabilities) ── feeds 4.1 (README table), feeds 4.2 (flag off missing features)
4.7 (MCP)          ── feeds Phase 6 tooling
```

Start 4.1 + 4.4 + 4.5 + 4.6 on Day 1 (all under 1 day each). 4.2 + 4.3 + 4.7 are the larger items.

---

## Exit criteria (Phase 4 complete)

1. README has no unverified claims; counts auto-generated.
2. UI shows connection banner; no silent `.catch`.
3. Paper and Live pages use WebSocket; polling is fallback only.
4. Monaco lazy-loaded.
5. Backtest/optimize cancellable end-to-end.
6. `/api/v1/capabilities` feeds UI feature flags and README.
7. MCP runs standalone without FastAPI server.
