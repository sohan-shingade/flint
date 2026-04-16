# Flint -- AI Development Guide

Flint is a local-first algorithmic trading, backtesting, and MEV research platform for Solana.

## Quick Reference

```bash
pip install -e .              # install
flint init                    # download data + sample backtest
flint serve                   # API + UI at localhost:8000
flint serve --dev             # dev mode: API only, run UI separately
pytest tests/ -v              # 1545 tests (~7min, all mocked)
cd ui && npm run dev          # dev UI at localhost:5173 (proxies API)

# Rust engine (optional -- 10-50x faster backtests)
pip install maturin           # install build tool
cd rust && maturin develop    # build + install flint_core
pytest tests/test_rust_parity_benchmark.py -v -s  # verify + benchmark
```

## Context Files

Read the relevant file when working on that area of the codebase:

| Area | File | When to read |
|------|------|--------------|
| Architecture | [.claude/docs/architecture.md](.claude/docs/architecture.md) | Navigating the codebase, understanding module layout or key patterns |
| Data Providers | [.claude/docs/data-providers.md](.claude/docs/data-providers.md) | Working with any provider, adding new data sources, funding/volume venues |
| Execution Engine | [.claude/docs/execution.md](.claude/docs/execution.md) | Fills, margin, capital allocation, venue configs, DuckDB schema |
| API | [.claude/docs/api-reference.md](.claude/docs/api-reference.md) | Adding/modifying endpoints, understanding request/response shapes |
| MCP Server | [.claude/docs/mcp-server.md](.claude/docs/mcp-server.md) | MCP integration, tools, resources |

## Common Tasks

**Add a data provider**: Create `flint/providers/my_provider.py`, inherit `DataProvider`, add to `__init__.py`, add config in `flint.yaml`.

**Add an API endpoint**: Add to `flint/api/routes/`. Register router in `flint/api/main.py` if new file.

**Add a strategy template**: Create in `flint/strategy/`, add to builders dict in `flint/api/routes/backtest.py`.

**Modify the UI**: Edit `ui/src/`. Run `cd ui && npm run dev` for hot reload. Build: `npm run build` -> served from `ui/dist/`.

**Run tests**: All tests use mocks, no network/keys needed. `pytest tests/ -v` for all, `-k "keyword"` to filter.

## Rules

- Always use the shared `FlintStore` from `app.state.store` -- never create a new DuckDB connection
- Every store method must wrap `self._conn.execute()` in `with self._lock:` -- DuckDB is not thread-safe
- Never access `store._conn` or `store._lock` from API routes -- add a method to `FlintStore` instead
- Don't `git push --force` on main
- Don't commit `.env` files -- they contain API keys
- Don't put personal strategies in `strategies/user/` in git -- they're gitignored
- User strategies can only import: flint, numpy, math, statistics, collections, dataclasses, typing, enum, abc, functools, itertools, operator
