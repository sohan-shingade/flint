# Flint QuantConnect-Style Upgrade — Design Spec

**Date**: 2026-03-20
**Status**: Approved
**Goal**: Transform Flint into a QuantConnect-like platform for traditional quants entering Solana DeFi. Three major additions: documentation page, integrated strategy lab, and automated data collection.

---

## Positioning

Flint bridges TradFi/CEX quants into Solana DeFi. The platform should feel familiar to someone who has used QuantConnect or Freqtrade, while showcasing what makes Solana unique (sub-second slots, MEV, on-chain orderbooks, AMM pools, perpetual futures funding rates).

---

## 1. Docs Page

**Route**: `/docs` — new nav item between Data and MEV.

**Layout**: Sidebar navigation (left) + content panel (right). Sidebar has collapsible sections. Content panel renders formatted text with syntax-highlighted code blocks.

### Sidebar Sections (Learning Path)

#### 1.1 Getting Started
- **Installation** — pip install, npm install, prerequisites
- **Quick Start** — 5-minute first backtest walkthrough
- **Project Structure** — file tree, what each module does

#### 1.2 Solana for Quants (Bridge Content)
- **Slots vs Time** — Solana's ~400ms slots as the native clock, how Flint maps this to candles
- **Drift Protocol** — perp futures on-chain, how it compares to CEX perps (Binance Futures, etc.)
- **AMM Pools** — constant-product math, reserves, price impact, vs CEX orderbooks
- **Funding Rates** — Drift hourly funding vs CEX 8-hour funding, how to exploit the difference
- **MEV on Solana** — what it is, Jito bundles, priority fees, searcher/validator dynamics
- **On-chain Order Books** — Drift's DLOB vs traditional CLOB, JIT auctions

#### 1.3 Strategy API Reference
- **Strategy Base Class** — `on_candle(candle, history) → Signal`, `reset()`, `name` property
- **Signals** — BUY, SELL, HOLD enum and what each triggers in the engine
- **Candle Data** — fields (ts, open, high, low, close, volume, market, resolution_s)
- **Backtest Engine** — execution model (bar-close fills, fee deduction, position sizing, force-close)
- **Analytics** — metrics computed (Sharpe, Sortino, drawdown, profit factor, etc.), tearsheet structure

#### 1.4 Examples
- **MA Crossover** (beginner) — golden/death cross, already built
- **RSI Mean Reversion** (beginner) — oversold/overbought bands
- **Funding Rate Harvest** (intermediate) — Solana-specific, exploit Drift funding
- **AMM Arbitrage** (advanced) — MEV, triangular arb across pools

### Implementation
- Content stored as a structured TypeScript object (no markdown files, no build step)
- Sidebar renders sections/topics; clicking loads content in main panel
- Code blocks use syntax highlighting (inline `<pre>` with themed styles)
- Responsive: sidebar collapses to hamburger on mobile

---

## 2. Strategy Lab (Integrated Backtest Lab)

The current `/backtest` page is upgraded into a split-view strategy authoring + execution environment.

### Layout
- **Left panel (~55%)**: Monaco code editor with file tabs
- **Right panel (~45%)**: Config (market, dates, capital) + Run button + Results (metrics, equity curve, drawdown, trade log)
- **Collapsible**: User can expand editor full-width or results full-width via drag handle or toggle

### Template System
- Dropdown above editor: "Start from template..."
- Options: `Blank Strategy`, `MA Crossover`, `RSI Mean Reversion`, `Funding Rate Harvest`, `AMM Arbitrage`
- Selecting a template populates editor with working code + inline comments explaining each section
- Templates serve as learning material and starting points

### Strategy Management
- Strategies saved to `strategies/user/` directory on the server as `.py` files
- File tab bar above editor shows open strategies
- Save (Ctrl+S) persists to disk
- "Save As" for creating new strategies from templates/modifications
- New/Open/Delete controls in the tab bar

### Light Guardrails (Validation)
- Must define a class that subclasses `Strategy`
- Must implement `name` property, `on_candle()` method, `reset()` method
- Warn (not block) on imports outside approved list: `numpy`, `math`, `statistics`, `collections`, `dataclasses`
- Clear error messages shown inline in editor (red underline + message panel)

### Execution Flow
1. User writes/edits code in Monaco
2. Clicks "Run Backtest" (or Ctrl+Enter keyboard shortcut)
3. Backend validates strategy class structure via AST parsing
4. If valid: dynamically loads strategy, runs backtest, returns tearsheet
5. Results render in right panel using existing components (MetricsCard, EquityCurve, DrawdownChart, TradeTable)
6. If validation fails: error displayed inline in editor with line numbers

### API Endpoints (New)

| Route | Method | Description |
|-------|--------|-------------|
| `POST /api/v1/strategies/user` | POST | Save a user strategy (name + code) |
| `GET /api/v1/strategies/user` | GET | List all user strategies |
| `GET /api/v1/strategies/user/{name}` | GET | Load a user strategy's code |
| `DELETE /api/v1/strategies/user/{name}` | DELETE | Delete a user strategy |
| `POST /api/v1/strategies/validate` | POST | Validate strategy code without running |

---

## 3. Data Collection Service

### Architecture
A background async service (`flint/collector/`) that runs inside the FastAPI process via lifespan events. Not a separate process or Celery — keeps it simple and local-first.

### Data Collected

| Data | Source | Markets | Interval | DuckDB Table |
|------|--------|---------|----------|-------------|
| OHLCV candles (1h) | Drift S3 | SOL-PERP, BTC-PERP, ETH-PERP | Backfill on first run, then hourly | `candles` (existing) |
| Funding rates | Drift Data API | SOL-PERP, BTC-PERP, ETH-PERP | Every hour | `funding_rates` (existing) |
| Orderbook snapshots | Drift DLOB | SOL-PERP, BTC-PERP, ETH-PERP | Every 5 minutes | `orderbook_snapshots` (new) |
| Pool states | Drift DLOB | SOL/USDC, ETH/USDC pools | Every 5 minutes | `pool_snapshots` (new) |
| Oracle prices | Drift Data API | SOL, BTC, ETH | Every minute | `oracle_prices` (new) |

### Startup Behavior
1. On first run, detect empty/stale DuckDB
2. Kick off full backfill (default: last 90 days of candle data per market)
3. Track progress via API endpoint (percentage per market)
4. After backfill, switch to scheduled refresh mode
5. Subsequent starts skip backfill if data is fresh (< 2 hours old)

### API Endpoints (New)

| Route | Method | Description |
|-------|--------|-------------|
| `GET /api/v1/collector/status` | GET | Status per market per data type (last_updated, count, state, freshness) |
| `POST /api/v1/collector/trigger` | POST | Manually trigger collection for a market/data type |
| `GET /api/v1/collector/config` | GET | Current collection config (markets, intervals, backfill range) |

### Dashboard Integration
- New "Data Collection Status" section on the Dashboard page
- Per market + data type: row count, date range, last updated, freshness indicator (green/yellow/red)
- "Refresh Now" button per market calls `POST /collector/trigger`
- Progress bar shown during backfill

---

## 4. Backend Changes

### New Files

| File | Purpose |
|------|---------|
| `flint/collector/__init__.py` | Collector package |
| `flint/collector/service.py` | Main collector service — async loop, scheduling, backfill logic |
| `flint/collector/tasks.py` | Individual collection tasks (candles, funding, orderbook, pools, oracle) |
| `flint/api/routes/collector.py` | Collector status/trigger/config endpoints |
| `flint/api/routes/user_strategies.py` | CRUD for user strategies |
| `flint/strategy/loader.py` | Dynamic strategy loading — AST validation + exec |
| `strategies/user/.gitkeep` | User strategy directory |

### Modified Files

| File | Changes |
|------|---------|
| `flint/api/main.py` | Register new routes (collector, user_strategies), start collector on app startup via lifespan event |
| `flint/api/routes/backtest.py` | Extend `_build_strategy()` to load user strategies via `loader.py` |
| `flint/store.py` | New tables (orderbook_snapshots, pool_snapshots, oracle_prices), new upsert/query methods |
| `flint/models.py` | Add `OraclePrice`, `PoolSnapshot`, `OrderbookSnapshotRecord`, `CollectorStatus` models |

### Strategy Loader (`flint/strategy/loader.py`)

```
load_user_strategy(code: str, params: dict | None) → Strategy:
    1. ast.parse(code) — catch syntax errors with line numbers
    2. Walk AST to find class subclassing Strategy
    3. Validate: has name property, on_candle method, reset method
    4. Check imports — warn on non-approved, don't block
    5. exec() code in namespace with flint imports pre-loaded
    6. Instantiate strategy class (pass params if constructor accepts them)
    7. Return instance
```

### New UI Files

| File | Purpose |
|------|---------|
| `ui/src/pages/Docs.tsx` | Documentation page with sidebar + content |
| `ui/src/data/docs-content.ts` | Structured docs content (sections, topics, text, code blocks) |
| `ui/src/components/DocsSidebar.tsx` | Collapsible sidebar navigation |
| `ui/src/components/DocsContent.tsx` | Content renderer (text, code blocks, tables) |
| `ui/src/components/CodeEditor.tsx` | Monaco editor wrapper component |
| `ui/src/components/CollectorStatus.tsx` | Data collection status panel for Dashboard |
| `ui/src/hooks/useStrategies.ts` | Hook for strategy CRUD operations |

### Modified UI Files

| File | Changes |
|------|---------|
| `ui/src/App.tsx` | Add `/docs` route, add DOCS nav item |
| `ui/src/pages/BacktestLab.tsx` | Major rewrite: split-view layout, Monaco editor integration, template dropdown, strategy save/load |
| `ui/src/pages/Dashboard.tsx` | Add CollectorStatus section |
| `ui/package.json` | Add `@monaco-editor/react` dependency |

---

## 5. Testing

### New Test Files

| File | Tests |
|------|-------|
| `tests/test_loader.py` | Strategy loader: valid strategy, missing methods, syntax errors, bad imports warning, subclass check |
| `tests/test_collector.py` | Collector service: backfill detection, task scheduling, status tracking, manual trigger |
| `tests/test_user_strategies.py` | User strategy API: upload, list, load, delete, validate endpoint |
| `tests/test_api_collector.py` | Collector API endpoints: status, trigger, config |

### Modified Tests

| File | Changes |
|------|---------|
| `tests/test_api.py` | Add tests for new endpoints (user strategies, collector) |

---

## 6. Key Design Decisions

### 6.1 Route Prefix for User Strategies

The existing `strategies.py` router mounts at `/api/v1/strategies` with a `GET /{name}` catch-all. To avoid collision, user strategy endpoints mount at a **separate prefix**: `/api/v1/user-strategies`. This keeps the routers independent.

| Route | Method | Description |
|-------|--------|-------------|
| `POST /api/v1/user-strategies` | POST | Save a user strategy (name + code) |
| `GET /api/v1/user-strategies` | GET | List all user strategies |
| `GET /api/v1/user-strategies/{name}` | GET | Load a user strategy's code |
| `DELETE /api/v1/user-strategies/{name}` | DELETE | Delete a user strategy |
| `POST /api/v1/user-strategies/validate` | POST | Validate strategy code without running |

(This supersedes the `/api/v1/strategies/user` routes listed in Section 2.)

### 6.2 Security Model for `exec()`-Based Loader

The strategy loader uses `exec()` on user-submitted Python code with no sandbox. **This is intentional.** Flint is a local-first, single-user tool — the user is running their own code on their own machine with full process privileges. This is the same model as Jupyter notebooks, Freqtrade, and running `python my_strategy.py` from the terminal. No false security theater.

### 6.3 DuckDB Concurrency (Collector vs API)

DuckDB supports concurrent reads but only one writer. Since the collector writes continuously in the background:

- The app creates a **singleton `FlintStore`** instance at startup, shared by both the collector and API request handlers.
- DuckDB is configured with **WAL mode** (`PRAGMA wal_autocheckpoint`) for better read/write concurrency.
- Write operations (collector upserts) acquire an **asyncio Lock** so the collector and backtest routes don't write simultaneously.
- Read operations (data queries) proceed without locking — DuckDB WAL supports concurrent reads during writes.

### 6.4 Backtest Request Schema for User Strategies

The `BacktestRequest` model gains an optional `code` field:

```python
class BacktestRequest(BaseModel):
    strategy: str           # "ma_crossover" (built-in) or "user:my_strategy" (user file)
    code: str | None = None # If provided, run this code directly (ad-hoc from editor)
    market: str
    resolution_s: int = 3600
    start_ts: int
    end_ts: int
    initial_capital: float = 10_000
    params: dict = {}
```

Resolution order in `_build_strategy()`:
1. If `code` is provided → load via `loader.py` (ad-hoc run from editor)
2. If `strategy` starts with `"user:"` → load from `strategies/user/{name}.py`
3. Otherwise → look up in built-in strategy map

### 6.5 User Strategy Directory

Strategies are stored at `{PROJECT_ROOT}/strategies/user/`. The project root is resolved as the directory containing `pyproject.toml`, found by walking up from `flint/__init__.py`. This is deterministic regardless of CWD when uvicorn starts.

### 6.6 FastAPI Lifespan Pattern

The current `main.py` uses plain `FastAPI()`. This changes to use the async context manager lifespan pattern:

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: init shared store, start collector
    store = FlintStore("./data/flint.duckdb")
    collector = CollectorService(store)
    task = asyncio.create_task(collector.run())
    app.state.store = store
    app.state.collector = collector
    yield
    # Shutdown: stop collector, close store
    task.cancel()
    store.close()

app = FastAPI(lifespan=lifespan)
```

### 6.7 New DuckDB Table Schemas

```sql
CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    market VARCHAR NOT NULL,
    ts BIGINT NOT NULL,
    bid_prices DOUBLE[],      -- top 10 bid prices
    bid_sizes DOUBLE[],       -- top 10 bid sizes
    ask_prices DOUBLE[],      -- top 10 ask prices
    ask_sizes DOUBLE[],       -- top 10 ask sizes
    PRIMARY KEY (market, ts)
);

CREATE TABLE IF NOT EXISTS pool_snapshots (
    pool_address VARCHAR NOT NULL,
    dex VARCHAR NOT NULL,
    token_a_mint VARCHAR NOT NULL,
    token_b_mint VARCHAR NOT NULL,
    reserve_a DOUBLE NOT NULL,
    reserve_b DOUBLE NOT NULL,
    fee_rate DOUBLE NOT NULL,
    ts BIGINT NOT NULL,
    PRIMARY KEY (pool_address, ts)
);

CREATE TABLE IF NOT EXISTS oracle_prices (
    market VARCHAR NOT NULL,
    ts BIGINT NOT NULL,
    price DOUBLE NOT NULL,
    slot BIGINT,
    PRIMARY KEY (market, ts)
);
```

`orderbook_snapshots` stores the top 10 levels as DuckDB arrays (flattened from the existing `OrderbookSnapshot` model). `pool_snapshots` is a timestamped version of the existing `PoolState` model. These are DuckDB-storage representations — the existing rich models (`OrderbookSnapshot`, `PoolState`) remain unchanged for in-memory use.

### 6.8 New Models

```python
@dataclass(frozen=True)
class OraclePrice:
    market: str
    ts: int
    price: float
    slot: int | None = None

@dataclass
class CollectorStatus:
    market: str
    data_type: str          # "candles", "funding", "orderbook", "pools", "oracle"
    state: str              # "idle", "collecting", "backfilling", "error"
    last_updated: int | None
    row_count: int
    date_range_start: int | None
    date_range_end: int | None
    error_message: str | None = None
    progress_pct: float | None = None   # 0-100 during backfill
```

### 6.9 Collector Error Handling

Collection tasks use **exponential backoff with jitter** on failure:
- First retry after 5s, then 10s, 20s, 40s, max 5 minutes
- After 5 consecutive failures, set status to `"error"` with the error message
- Error status is visible via the `/collector/status` endpoint and Dashboard UI
- Next scheduled run resets the retry counter and tries again
- Collector never crashes the API server — all errors are caught and logged

### 6.10 Collector Data Sources

| Data | Endpoint | Notes |
|------|----------|-------|
| OHLCV candles | Drift S3: `drift-historical-data-v2.s3.eu-west-1.amazonaws.com/.../tradeRecords/` | Use existing `DriftS3Provider`, aggregate trades to 1h candles |
| Funding rates | Drift Data API: `GET https://data.api.drift.trade/fundingRates?marketIndex={idx}` | Use existing `DriftDataProvider.fetch_funding_rates()` |
| Orderbook | Drift DLOB: `GET https://dlob.drift.trade/l2?marketIndex={idx}&depth=10` | Use existing `DriftDataProvider.fetch_orderbook()`, store top 10 levels |
| Pool states | Drift DLOB: `GET https://dlob.drift.trade/l2?marketIndex={idx}` | Derive implied pool state from DLOB spread for perp markets; for AMM pools, read on-chain reserves via RPC (future: Helius free tier) |
| Oracle prices | Drift Data API: `GET https://data.api.drift.trade/fundingRates?marketIndex={idx}` | Extract `oraclePrice` field from funding rate response (already returned by Drift), or use `DriftDataProvider.fetch_mid_price()` as fallback |

### 6.11 Template Strategies

The `Funding Rate Harvest` and `AMM Arbitrage` templates are **new implementations** written as part of this work. They are template code with extensive comments — not production strategies.

- **Funding Rate Harvest**: Tracks funding rate sign. Goes long when funding is deeply negative (shorts are paying longs), goes short when funding is deeply positive. Configurable threshold.
- **AMM Arbitrage**: Uses the existing `ArbDetector` from `flint/mev/arb.py` to detect mispriced pools. Generates BUY signal when arb profit exceeds threshold. This is a simplified demonstration — real arb would need atomic execution.

### 6.12 Docs Content TypeScript Interface

```typescript
interface DocCodeBlock {
  language: string;   // "python", "bash", "typescript"
  code: string;
}

interface DocTopic {
  id: string;         // URL-friendly slug
  title: string;
  content: string;    // HTML string with formatted text
  codeBlocks?: DocCodeBlock[];
}

interface DocSection {
  id: string;
  title: string;
  topics: DocTopic[];
}

type DocsContent = DocSection[];
```

### 6.13 Nav Label

The nav item for the upgraded Backtest Lab changes from `BACKTEST` to `LAB`. This reflects the broader scope (authoring + execution) while staying concise in the cyberpunk nav style.

---

## Non-Goals (Explicitly Out of Scope)

- Cloud deployment / multi-user auth
- Sandboxed Python execution
- Strategy versioning / git integration
- WebSocket streaming (future phase)
- Hyperparameter optimization (future phase)
- Live trading execution (future phase)
