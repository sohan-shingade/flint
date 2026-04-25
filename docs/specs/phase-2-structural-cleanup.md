# Phase 2 — Structural Cleanup

**Owner:** TBD
**Duration:** 4-6 weeks (runs parallel to Phase 1)
**Blocks:** Phase 3 (sub-items 2.1 and 2.2 specifically)
**Exit criteria:** see end of doc

Paying down the biggest architectural debts before Phase 3 work is piled on top.

---

## Items

- [2.1 ExecutionContext consolidation](#21-executioncontext-consolidation)
- [2.2 Wrap raw store access](#22-wrap-raw-store-access)
- [2.3 Single BacktestConfig](#23-single-backtestconfig)
- [2.4 User-strategy subprocess isolation](#24-user-strategy-subprocess-isolation)
- [2.5 Repo cleanup](#25-repo-cleanup)

---

## 2.1 ExecutionContext consolidation

**Problem:** 5 parallel hierarchies doing overlapping work:
- `BacktestContext` (973 lines, 60 methods) — positions + orders + funding + margin + borrow + OI + fills
- `LiveExecutionContext` (651 lines)
- `MultiVenueLiveContext` (370 lines)
- `LiveContext` (216 lines)
- `LiveJupiterContext`

Duplicated fill / fee / funding logic. Strategies hardcode venue branches because no polymorphism is actually used. Blocks strategy portability (backtest ↔ paper ↔ live).

### Target architecture

```
ExecutionContext (abstract, ~15 methods)
├── BacktestContext
├── PaperContext         (new — today PaperBroker is tangled with live)
└── LiveContext
    ├── DriftLiveContext
    ├── HyperliquidLiveContext
    ├── JupiterLiveContext
    └── MultiVenueLiveContext (composes the above)
```

### Interface

All contexts implement exactly:

```python
class ExecutionContext(Protocol):
    def market_order(self, market: str, side: str, size: float, venue: str | None = None) -> OrderId: ...
    def limit_order(self, market: str, side: str, size: float, price: float, venue: str | None = None) -> OrderId: ...
    def stop_order(self, market: str, side: str, size: float, trigger_price: float, venue: str | None = None) -> OrderId: ...
    def cancel(self, order_id: OrderId) -> bool: ...
    def cancel_all(self, market: str | None = None, venue: str | None = None) -> int: ...
    def close_position(self, market: str, venue: str | None = None) -> OrderId | None: ...

    @property
    def positions(self) -> list[Position]: ...
    @property
    def orders(self) -> list[Order]: ...
    @property
    def account(self) -> AccountState: ...
    @property
    def time(self) -> int: ...  # epoch seconds

    def get_candles(self, market: str, n: int | None = None) -> list[Candle]: ...
    def get_funding_by_venue(self, market: str) -> dict[str, float]: ...
    def get_open_interest(self, market: str) -> float | None: ...
    def get_orderbook(self, market: str, venue: str | None = None) -> Orderbook | None: ...
```

Strategies written against this Protocol run unchanged on backtest, paper, and live.

### Tasks

**T2.1.a — Extract abstract Protocol**
- New: `flint/execution/protocol.py` — defines `ExecutionContext` Protocol above.
- Delete duplicates in `flint/execution/context.py`.

**T2.1.b — Break up BacktestContext god class**
- Extract into sibling components:
  - `PositionManager` (from lines owning `_positions`)
  - `OrderQueue` (from lines owning `_orders`, `_pending_orders`)
  - `FundingTracker` (from `get_funding_*`, funding accrual)
  - `BorrowTracker` (from Jupiter borrow logic)
  - `MarketDataFeed` (from `get_candles`, `get_orderbook`, `get_open_interest`)
- `BacktestContext` becomes a thin facade composing these.
- Target: `BacktestContext` under 300 lines.

**T2.1.c — Merge LiveContext + LiveExecutionContext**
- Today these are siblings. `LiveExecutionContext` is richer; `LiveContext` is the older simple variant.
- Collapse into one. Migrate all callers.

**T2.1.d — Paper trading becomes its own context**
- Today paper logic lives inside live code paths with a `paper_mode` flag.
- New: `flint/execution/paper_context.py:PaperContext` — implements `ExecutionContext`, uses `PaperBroker` for fills, shares `FundingTracker`/`MarketDataFeed` with `BacktestContext`.

**T2.1.e — MultiVenueLiveContext as composition**
- Today: duplicated routing logic.
- New: `MultiVenueLiveContext` holds `dict[venue, LiveContext]` and dispatches `venue=` arg.

### Acceptance

- Every strategy under `flint/strategy/` runs on all three contexts (backtest, paper, live-dryrun) with zero source changes.
- Line count: `BacktestContext + LiveContext + MultiVenueLiveContext + PaperContext ≤ 1500` total (down from ~2200 today).
- All existing tests green.
- New test: `tests/test_context_portability.py` — takes a strategy, runs it on all three contexts, asserts interface conformance.

### Effort

~2 weeks. Highest architectural ROI in the plan.

---

## 2.2 Wrap raw store access

**Problem:** CLAUDE.md rule: "Never access `store._conn` or `store._lock` from API routes — add a method to `FlintStore` instead." Verified violations (2026-04-23):

| File | Lines | Method |
|---|---|---|
| `flint/api/routes/live.py` | 32-36, 49-53 | `/equity`, `/sessions` |
| `flint/api/routes/data.py` | 109-112, 195-206, 221, 281-285, 295-299, 309-313, 699-704 | `/volume`, `/market/{market}` DELETE, `/freshness`, `/funding`, `/orderbook`, `/open-interest` |
| `flint/mcp_server.py` | 502-506 | `list_local_markets` |

Also violated in internal helpers (lower severity, pre-rule):
- `flint/journal/storage.py` — 15+ sites
- `flint/paper/session_store.py` — 10+ sites

### Tasks

**T2.2.a — Add clean methods to `FlintStore`**
- `get_live_equity_history(session_id: str) -> list[EquityPoint]`
- `list_live_sessions(limit: int = 20) -> list[LiveSession]`
- `query_volume_by_venue(market: str, start_ts: int, end_ts: int) -> dict[str, float]`
- `delete_market_data(market: str) -> int` (returns row count deleted; manages transaction)
- `get_freshness_per_venue(market: str) -> dict[str, int]`
- `get_funding_latest(market: str, venue: str) -> FundingRate | None`
- `get_orderbook_latest(market: str, venue: str) -> Orderbook | None`
- `get_open_interest_latest(market: str) -> float | None`
- `list_local_markets() -> list[str]`

Every method uses `with self._lock:` internally.

**T2.2.b — Delete raw `_conn` access from routes**
- `flint/api/routes/live.py` — swap to new methods.
- `flint/api/routes/data.py` — swap all 8 sites.
- `flint/mcp_server.py:502-506` — swap to `store.list_local_markets()`.

**T2.2.c — Internal storage helpers (lower priority)**
- `flint/journal/storage.py` and `flint/paper/session_store.py` currently own their own SQL and pass `store` to reuse its `_conn`.
- Refactor: move journal and paper session SQL *into* `FlintStore` as `save_journal_run`, `get_journal_run`, `list_journal_runs`, `save_paper_session`, etc.
- Delete the helper-class indirection.

**T2.2.d — Lint rule**
- Add `tests/test_store_encapsulation.py`: `grep -rn 'store\._conn\|store\._lock' flint/ --include='*.py'` outside `flint/store.py` must return zero hits.
- Makes the rule enforceable going forward.

### Acceptance

- Grep-based lint test green.
- All existing tests green.
- No API-level behavior change.

### Effort

~3 days for the route and MCP fixes. ~1 additional week if also absorbing journal/paper session helpers.

---

## 2.3 Single BacktestConfig

**Problem:** Config sprawl across:
- `flint.yaml` (venue configs, data providers, live keys)
- `BacktestEngine(**kwargs)` (capital, fee_rate, fill_model, seed, ...)
- `FillPipeline(**kwargs)` (impact_coefficient, latency_enabled, tx_cost_model, ...)
- `MarginEngine(venue_configs)`
- `capital_allocator(**kwargs)`

Changing fee rate requires editing 3 places. No single type system for a "backtest run."

### Target

```python
@dataclass
class BacktestConfig:
    # Market + data
    market: str
    start_ts: int
    end_ts: int
    resolution_s: int = 3600

    # Capital + fees
    initial_capital: float = 10_000.0
    fee_rate: float = 0.0005

    # Seed
    seed: int | None = None

    # Fill model
    fill: FillConfig = field(default_factory=FillConfig)

    # Margin
    margin: MarginConfig = field(default_factory=MarginConfig)

    # Capital allocator (multi-strategy)
    allocator: AllocatorConfig | None = None

    # Venue overrides (merges flint.yaml defaults)
    venues: dict[str, VenueConfig] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path, overrides: dict | None = None) -> BacktestConfig: ...
    def to_json(self) -> dict: ...
    def checksum(self) -> str: ...  # for proof notebooks
```

### Tasks

**T2.3.a — Define `BacktestConfig` + nested configs**
- New: `flint/backtest/config.py`.
- `FillConfig`, `MarginConfig`, `AllocatorConfig`, `VenueConfig` as subtypes.

**T2.3.b — `BacktestEngine.run(config: BacktestConfig, candles, strategy)`**
- Primary path.
- Old kwargs accepted via one-release-only shim: `BacktestEngine.run(..., **legacy_kwargs)` logs a `DeprecationWarning`.

**T2.3.c — API + UI migration**
- `/api/v1/backtest/run` accepts `BacktestConfig` JSON.
- UI sends the full config.

**T2.3.d — `checksum()` for provenance**
- Used by proof notebooks (1.5) — the full config including data hashes goes into the notebook output.

### Acceptance

- All backtest entrypoints accept `BacktestConfig` as the primary API.
- Deprecation warnings emitted for old kwargs.
- Existing tests pass via shim.
- New test: round-trip `BacktestConfig → yaml → BacktestConfig` preserves equality.

### Effort

~1 week.

---

## 2.4 User-strategy subprocess isolation

**Problem:** AST-based import whitelist in `flint/strategy/loader.py` blocks bad imports but does not:
- Bound wall-clock time (a `while True: pass` in `on_candle` hangs the server)
- Bound memory (a strategy that allocates `np.zeros(1e12)` crashes the process)
- Prevent fork bombs or network at runtime (if someone finds an import escape)

Affects any multi-user or hostile-input context (only localhost today, but MCP tools can expose the surface accidentally).

### Target

All user-strategy execution routed through `concurrent.futures.ProcessPoolExecutor` with:
- 5-minute wall-clock timeout (configurable)
- 1GB RSS limit (Linux: `resource.setrlimit(RLIMIT_AS)`; macOS: best-effort `RLIMIT_RSS`)
- Whitelist applied in the subprocess too (defense in depth)
- Clean error surface: timeout → `StrategyTimeoutError`, OOM → `StrategyMemoryError`

### Tasks

**T2.4.a — `flint/strategy/sandbox.py`**
- `run_in_sandbox(strategy_path: Path, config: BacktestConfig, candles: list) -> BacktestResult`.
- Uses `spawn` start method (not `fork`) to prevent parent state leak.
- Enforces rlimit + timeout inside the child.

**T2.4.b — Route user strategies through sandbox**
- Any `BacktestEngine.run` call where the strategy comes from `strategies/user/*.py` or `flint/api/routes/user_strategies.py` uploads.
- Built-in strategies under `flint/strategy/` run in-process (trusted).

**T2.4.c — Sandbox escape tests**
- `tests/test_sandbox_escape.py`:
  - Attempt `os.system`, `subprocess.Popen`, `open("/etc/passwd")`, `eval()`, `exec()`, `__import__('os')`. All must fail before execution.
  - Submit a `while True: pass` strategy. Assert `StrategyTimeoutError` raised within 6 min.
  - Submit `np.zeros(10**10)`. Assert `StrategyMemoryError` or `MemoryError` raised.

### Acceptance

- All sandbox escape tests pass.
- Existing strategy tests still pass (built-ins run in-process at same speed).
- User-strategy uploads cannot hang the server.

### Effort

~3-5 days.

---

## 2.5 Repo cleanup

**Goal:** delete dead weight.

### Tasks

**T2.5.a — Untrack committed build artifacts**
- `git rm --cached dist/flint_trading-1.1.0-py3-none-any.whl dist/flint_trading-1.1.0.tar.gz`
- Already in `.gitignore`; they just predate it.

**T2.5.b — Delete orphan files**
- `git rm --cached research_analysis.py` (780 LOC, gitignored, not imported anywhere).
- If there's value, move contents into a proper module under `flint/` first. Verify with `grep -rn 'research_analysis' .`.

**T2.5.c — Decide `sidecar/jupiter-perps/`**
- Currently empty directory under `sidecar/`.
- Either: integrate into `flint/execution/jupiter_live.py` flow + document, or delete.

**T2.5.d — Consolidate `docs/how-to/` into `docs/guides/`**
- Today: `docs/how-to/` rendered into UI docs but MCP `flint://guide` resource only serves `docs/guides/quickstart.md`. Split source of truth.
- Fold the 8 how-to files into the 8 existing guides as "## How to ..." sections, or expose how-to's via a new MCP resource.

**T2.5.e — Audit top-level files**
- `docker-compose.yml`, `Dockerfile`, `docker-entrypoint.sh` — keep if Docker install is supported; drop if not.
- `Makefile` — verify it has targets worth keeping vs. `scripts/`.

### Acceptance

- `git ls-files | grep -E '(dist/|research_analysis.py|sidecar/jupiter-perps)'` returns zero.
- Repo root has only files that map to live code paths.

### Effort

~2-4 hours.

---

## Dependencies

```
2.1 (ExecutionContext) ──► Phase 3.1, 3.5 (fills and multi-venue both rely on unified Protocol)
2.2 (store abstraction) ──► all route-touching work
2.3 (BacktestConfig)   ──► Phase 3.6 (calibration reports need config checksums)
2.4 (sandbox)          ──► Phase 4.7 (MCP in-process mode)
2.5 (cleanup)          ──► independent
```

---

## Exit criteria (Phase 2 complete)

1. Every strategy runs on Backtest, Paper, Live with zero code changes.
2. `grep 'store\._conn\|store\._lock' flint/ --include='*.py' -rn` returns hits only in `flint/store.py`.
3. `BacktestConfig` is the canonical entry for backtest runs; old kwargs deprecated.
4. User-strategy sandbox enforces wall-clock + memory.
5. Repo root has no committed artifacts or orphan files.

Until all five are green, Phase 3 items 3.1 and 3.2 do not start.

## Deferred sibling PRs

Phase 2 ships the interface contracts + plumbing but defers the invasive
refactors to dedicated sibling PRs. Tracker with owners, prerequisites, and
success metrics: [`DEFERRED.md`](../../DEFERRED.md).

Specifically:
- **D-2.1.b** — break up `BacktestContext` god class (973 LOC → 5 components)
- **D-2.1.c** — merge `LiveContext` + `LiveExecutionContext` (needs testnet smoke)
- **D-2.1.d** — separate `PaperContext` (coupled with PaperBroker rewiring)
- **D-2.4.b** — route `/api/v1/backtest/run` uploads through sandbox (needs UI error surface)
- **D-2.2-internal** — migrate `flint/journal/storage.py` + `flint/paper/session_store.py` off raw `store._conn`

Exit criterion 1 is marked 🟡 until D-2.1.b/c/d land; the ABC + conformance
test (`tests/test_context_portability.py`) guards against silent method drops
in the interim.
