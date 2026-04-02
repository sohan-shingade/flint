# Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write 6 comprehensive user-facing guides so developers can install, use, and extend Flint without reading the source code.

**Architecture:** Pure markdown documentation in `docs/guides/`. No code changes. Each guide is self-contained.

**Tech Stack:** Markdown.

---

## File Structure

| File | Action |
|------|--------|
| `docs/guides/quickstart.md` | Create |
| `docs/guides/strategy-authoring.md` | Create |
| `docs/guides/data-providers.md` | Create |
| `docs/guides/live-deployment.md` | Create |
| `docs/guides/architecture.md` | Create |
| `docs/guides/slippage-models.md` | Create |
| `ROADMAP.md` | Modify |

---

### Task 1: Quickstart Guide

**Files:**
- Create: `docs/guides/quickstart.md`

- [ ] **Step 1: Write the quickstart guide**

Write `docs/guides/quickstart.md` covering:

1. **Prerequisites** — Python 3.10+, pip
2. **Install** — `pip install -e .` or `curl | sh` install script
3. **Initialize** — `flint init` (downloads sample data + runs demo backtest)
4. **Pull data** — `flint serve` then use API or UI to download SOL-PERP data
5. **Run first backtest** — via CLI or API with the momentum strategy
6. **View results** — UI at localhost:8000, equity curve, trade list
7. **Next steps** — links to strategy authoring guide and data provider guide

Target: someone goes from zero to seeing backtest results in under 5 minutes.

Reference `CLAUDE.md` for exact commands:
- `pip install -e .`
- `flint init`
- `flint serve`
- API: `POST /api/v1/backtest/run`

- [ ] **Step 2: Commit**

```bash
git add -f docs/guides/quickstart.md
git commit -m "docs: add quickstart guide (install → backtest in <5 min)"
```

---

### Task 2: Strategy Authoring Guide

**Files:**
- Create: `docs/guides/strategy-authoring.md`

- [ ] **Step 1: Write the strategy authoring guide**

Write `docs/guides/strategy-authoring.md` covering:

1. **Strategy ABC** — extend `Strategy`, implement `name`, `on_candle()`, `reset()`, `parameters()`
2. **v1 strategies** — return `Signal.BUY/SELL/HOLD`, engine handles order placement
3. **v2 strategies** — use `ctx.market_order()`, `ctx.limit_order()`, etc., return `HOLD`
4. **ExecutionContext API reference** — all properties and methods:
   - `account`, `positions`, `pending_orders`, `current_candle`, `timestamp`
   - `market_order()`, `limit_order()`, `stop_order()`, `take_profit_order()`
   - `cancel()`, `cancel_all()`, `close_position()`, `position()`
   - `get_candles()`, `get_oracle_price()`, `get_funding_rate()`, `get_funding_by_venue()`
   - `venue_positions()`, `venue_balance()`, `estimate_cost()`
5. **Multi-market strategies** — `ctx.get_candles("BTC-PERP")`, venue parameter
6. **Cross-venue strategies** — `venue="drift"` parameter, `MultiVenueLiveContext`
7. **Optimization with Optuna** — `parameters()` dict format, `flint optimize` or API
8. **Strategy loader security** — allowed imports, AST validation
9. **Examples** — reference the 5 strategy READMEs in `docs/strategies/`

- [ ] **Step 2: Commit**

```bash
git add -f docs/guides/strategy-authoring.md
git commit -m "docs: add strategy authoring guide (v1/v2, API ref, Optuna)"
```

---

### Task 3: Data Provider Guide

**Files:**
- Create: `docs/guides/data-providers.md`

- [ ] **Step 1: Write the data provider guide**

Write `docs/guides/data-providers.md` covering:

1. **Provider overview table** — all 14 providers with: name, file, auth required, data types
   - Drift Data API, Drift S3, Drift OI, Drift Funding
   - Birdeye (key required), Helius (key required)
   - Pyth, Raydium, Orca, GeckoTerminal, Jupiter, CoinGecko, CCXT
   - Hyperliquid candles + funding
2. **Funding venues** — 10 venues (Drift, Binance, Hyperliquid, OKX, Bybit, Gate.io, Bitget, dYdX, MEXC, Phemex)
3. **API keys** — which providers need keys, how to set them (`FLINT_BIRDEYE_API_KEY`, etc.)
4. **Downloading data** — CLI and API methods, date ranges, resolutions
5. **Data freshness** — `GET /api/v1/data/freshness` endpoint
6. **Adding a custom provider** — inherit `DataProvider`, implement `is_available()` + `supported_data_types()`, register in `flint.yaml`
7. **Provider configuration** — `flint.yaml` providers section, enable/disable

- [ ] **Step 2: Commit**

```bash
git add -f docs/guides/data-providers.md
git commit -m "docs: add data provider guide (14 providers, API keys, custom)"
```

---

### Task 4: Live Deployment Guide

**Files:**
- Create: `docs/guides/live-deployment.md`

- [ ] **Step 1: Write the live deployment guide**

Write `docs/guides/live-deployment.md` covering:

1. **Venue setup**
   - **Drift**: `FLINT_PRIVATE_KEY` (base58 Solana keypair), `FLINT_RPC_URL`, devnet vs mainnet
   - **Hyperliquid**: `FLINT_HYPERLIQUID_PRIVATE_KEY` (Ethereum hex key), API wallet vs main wallet, withdrawals via web UI
2. **Risk configuration** — `flint.yaml` safety rails:
   - `live_kill_switch_drawdown_pct`, `live_max_orders_per_minute`
   - `live_per_market_position_limits`, `live_dry_run`
   - `live_drawdown_warning_pct`
3. **Dry-run testing** — `live_dry_run: true`, verify strategy behavior before real capital
4. **Devnet testing** — `live_network: devnet`, get devnet SOL, run on testnet
5. **Mainnet checklist** — switch network, fund wallet, verify RPC, enable kill switch, set position limits
6. **Monitoring and alerting** — Telegram/Discord notifications, `telegram_bot_token`, `telegram_chat_id`, `discord_webhook_url`
7. **Multi-venue deployment** — `MultiVenueLiveContext`, `live_multi_venue_primary`, tick modes
8. **Parity test** — `flint parity` to verify backtest matches paper trading before going live

- [ ] **Step 2: Commit**

```bash
git add -f docs/guides/live-deployment.md
git commit -m "docs: add live deployment guide (wallet, risk, devnet, mainnet)"
```

---

### Task 5: Architecture Overview

**Files:**
- Create: `docs/guides/architecture.md`

- [ ] **Step 1: Write the architecture overview**

Write `docs/guides/architecture.md` covering:

1. **High-level architecture** — text diagram showing: Providers → FlintStore → BacktestEngine → Strategy → Results
2. **Key components**:
   - `ExecutionContext` ABC — strategy interface, venue-agnostic
   - `BacktestContext` — simulated execution with fill/fee models
   - `LiveExecutionContext` → `LiveDriftContext`, `LiveHyperliquidContext`
   - `MultiVenueLiveContext` — cross-venue wrapper
3. **Fill pipeline** — `FillPipeline` with 4-tier `ImpactStage` (vAMM → orderbook → sqrt → flat), `LatencyStage`, `PartialFillStage`. Brief mention, link to slippage-models.md for details.
4. **Margin engine** — per-venue configs, liquidation detection
5. **Capital allocator** — `VenueAllocator`, per-venue balances, transfer delays
6. **Data flow** — providers fetch → store persists → engine feeds to strategy
7. **WebSocket feeds** — `WebSocketFeed` base, Drift/Hyperliquid/Pyth implementations
8. **Risk guards** — `RiskManager` chain, `EquityMonitor` kill switch
9. **Extension points** — where to add new venues, providers, fill models, strategies

- [ ] **Step 2: Commit**

```bash
git add -f docs/guides/architecture.md
git commit -m "docs: add architecture overview (components, data flow, extension points)"
```

---

### Task 6: Slippage Models Guide

**Files:**
- Create: `docs/guides/slippage-models.md`

- [ ] **Step 1: Write the slippage models guide**

Write `docs/guides/slippage-models.md` covering:

1. **4-tier impact model** — how `ImpactStage` selects the best available model:
   - Tier 0: vAMM constant-product curve (Drift markets with configured K)
   - Tier 1: Orderbook walk (when L2 snapshot available)
   - Tier 2: Sqrt participation model (`impact = k * sqrt(size/volume)`)
   - Tier 3: Flat bps fallback (last resort)
2. **vAMM curve math** — constant product equation, peg multiplier, how K factor determines depth, `VammCurve.fill_price()` formula
3. **Sqrt participation model** — the equation, what k means, how it's calibrated
4. **Calibration** — power-law model with volatility normalization:
   - `impact_bps = a * sigma * (Q/ADV)^b`
   - How to run: `flint calibrate --venue drift --market SOL-PERP`
   - Model selection (power-law vs sqrt via cross-validation)
   - Drift detection at 15% threshold
5. **Transaction costs** — priority fees, Jito tips, per-venue models
6. **Choosing the right model** — decision guide: when to use vAMM vs orderbook vs sqrt
7. **Configuration** — `vamm_enabled`, `vamm_default_sqrt_k`, `impact_coefficient` in VenueConfig

- [ ] **Step 2: Commit**

```bash
git add -f docs/guides/slippage-models.md
git commit -m "docs: add slippage models guide (4-tier impact, vAMM, calibration)"
```

---

### Task 7: ROADMAP Update

**Files:**
- Modify: `ROADMAP.md`

- [ ] **Step 1: Update §5.2**

Find §5.2 Documentation and add after existing checklist:

```markdown
**Implemented:**
- [x] Quickstart guide: install → first backtest in <5 min (`docs/guides/quickstart.md`)
- [x] Strategy authoring guide: v1/v2, ExecutionContext API, Optuna (`docs/guides/strategy-authoring.md`)
- [x] Data provider guide: 14 providers, API keys, custom providers (`docs/guides/data-providers.md`)
- [x] Live deployment guide: wallet setup, risk config, devnet → mainnet (`docs/guides/live-deployment.md`)
- [x] Architecture overview: components, data flow, extension points (`docs/guides/architecture.md`)
- [x] Slippage models guide: 4-tier impact, vAMM math, calibration equations (`docs/guides/slippage-models.md`)
```

- [ ] **Step 2: Commit**

```bash
git add ROADMAP.md
git commit -m "docs: update ROADMAP §5.2 with documentation implementation notes"
```

---

## Task Dependencies

All tasks are independent — guides can be written in any order. Task 7 (ROADMAP) is last.
