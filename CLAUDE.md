# Flint -- AI Development Guide

Flint is a local-first algorithmic trading, backtesting, and MEV research platform for Solana.

## Quick Reference

```bash
pip install -e .              # install
flint init                    # download data + sample backtest
flint serve                   # API + UI at localhost:8000
flint serve --dev             # dev mode: API only, run UI separately
pytest tests/ -v              # all tests (~7min, all mocked)
cd ui && npm run dev          # dev UI at localhost:5173 (proxies API)

# Rust engine (optional -- 10-50x faster backtests)
pip install maturin           # install build tool
cd rust && maturin develop    # build + install flint_core
pytest tests/test_rust_parity_benchmark.py -v -s  # verify + benchmark
```

## Documentation (single source of truth: docs/)

Read the relevant guide when working on that area. These same files power the web UI docs page, MCP `flint://guide` resource, and this file.

| Area | Guide | When to read |
|------|-------|--------------|
| Getting started | [docs/guides/quickstart.md](docs/guides/quickstart.md) | Install, first backtest, optimization, paper deploy workflow |
| Architecture | [docs/guides/architecture.md](docs/guides/architecture.md) | Module layout, execution hierarchy, Rust engine, regimes |
| Strategies | [docs/guides/strategy-authoring.md](docs/guides/strategy-authoring.md) | v1/v2 APIs, 20 built-in templates, optimization params, security |
| Data | [docs/guides/data-providers.md](docs/guides/data-providers.md) | 15 providers, 7 funding venues, downloading, custom providers |
| Live/Paper | [docs/guides/live-deployment.md](docs/guides/live-deployment.md) | Paper trading, Hyperliquid/CEX setup, risk guards, parity testing |
| Web UI | [docs/guides/web-ui.md](docs/guides/web-ui.md) | All 10 UI pages, features, keyboard shortcuts |
| MCP | [docs/guides/mcp-integration.md](docs/guides/mcp-integration.md) | 17 MCP tools, setup, AI workflow |
| Fills | [docs/guides/slippage-models.md](docs/guides/slippage-models.md) | 4-tier fill pipeline, vAMM, calibration |

To update docs: edit `docs/guides/*.md`, then run `python scripts/build_docs.py` to regenerate the UI docs page.

## Architecture cheat sheet

`BacktestContext` (`flint/execution/`) and `PaperContext` (`flint/paper/context.py`)
both compose the same seven managers in `flint/execution/`. Paper trading post-D-2.1.d
unifies the old `PaperBroker` + `LiveContext` split into one class — same shape as
backtest. Positions keyed by `(venue, market)` tuples in both contexts:

| Manager | Owns |
|---|---|
| `PositionManager` | Open + closed-trade dicts |
| `CashManager` | Cash + allocator + running counters (fees / tx cost / funding) |
| `FillRecorder` | Recorded fill list + diagnostic log |
| `OrderQueue` | Pending limit/stop/TP queue + this-bar market queue |
| `FundingLedger` | Per-market + per-venue funding history |
| `BorrowLedger` | Jupiter borrow rates + paid-borrow ledger |
| `MarketDataFeed` | Cross-market candles + orderbook + OI snapshots |

Pre-trade checks flow through `flint/risk/portfolio_orchestrator.py:PortfolioMarginEngine` (composes `MarginEngine` + `VenueAllocator` + `PortfolioRiskEngine`).

Event sourcing lives in `flint/portfolio/`: `event_log.py` (append-only writer), `replay.py` (`fold(events) → BookState`), `snapshots.py` (compaction). `BacktestContext` emits via `_emit(kind, payload)` when constructed with `event_log_writer + event_session_id`. Replay surface: `/api/v1/replay/{id}/{events,state,summary}` + MCP tools (`replay_summary`, `replay_state`, `list_replay_events`) + UI page at `/replay`.

Service layer in `flint/services/`: `strategies.py` (single builder source), `backtest.py:run_backtest_sync`, `journal.py`, `data.py`, `paper.py`. MCP and routes both go through these — don't reach into route internals from MCP.

## Common Tasks

**Add a data provider**: Create `flint/providers/my_provider.py`, inherit `DataProvider`, add to `__init__.py`, add config in `flint.yaml`.

**Add an API endpoint**: Add to `flint/api/routes/`. Register router in `flint/api/main.py` if new file.

**Add a strategy template**: Create in `flint/strategy/`, add to the `_BUILTIN_BUILDERS` dict in `flint/services/strategies.py` (single source of truth — both backtest and paper routes plus MCP read from it).

**Modify the UI**: Edit `ui/src/`. Run `cd ui && npm run dev` for hot reload. Build: `npm run build` -> served from `ui/dist/`.

**Update docs**: Edit markdown in `docs/guides/`. Run `python scripts/build_docs.py` to regenerate UI docs. MCP guide resource reads from `docs/guides/quickstart.md` automatically.

**Run tests**: All tests use mocks, no network/keys needed. `pytest tests/ -v` for all, `-k "keyword"` to filter.

## Rules

- **DRIFT IS DROPPED — DO NOT USE.** Drift Protocol is offline post-hack and is no longer a supported venue. Flint is **DEX & perp native / venue-agnostic** — Hyperliquid is the live execution + data venue today (with Pyth oracle prices); **Phoenix, Jupiter spot, and batch/bulk order routing are the planned expansion** (land as new connectors). Treat every Drift surface as unavailable: `dlob.drift.trade` (DLOB), `data.api.drift.trade` (Data API), the Drift WebSocket feed, and any `flint/connectors/drift/` paths. Legacy Drift code stays dormant — do not extend it, do not surface it in docs/UI/MCP narrative, and never introduce new Drift dependencies.
- Always use the shared `FlintStore` from `app.state.store` -- never create a new DuckDB connection
- Every store method must wrap `self._conn.execute()` in `with self._lock:` -- DuckDB is not thread-safe
- Never access `store._conn` or `store._lock` from API routes -- add a method to `FlintStore` instead
- Don't `git push --force` on main
- Don't commit `.env` files -- they contain API keys
- Don't put personal strategies in `strategies/user/` in git -- they're gitignored
- User strategies can only import: flint, numpy, math, statistics, collections, dataclasses, typing, enum, abc, functools, itertools, operator
- New BacktestContext mutations should go through the seven managers (`self._pm`, `self._cm`, `self._fr`, `self._oq`, `self._fl`, `self._bl`, `self._mdf`) — legacy `self._cash` / `self._positions` etc. are read-only property aliases kept for back-compat with tests
- New strategy templates: edit `flint/services/strategies.py` only — the route layer reads from there
