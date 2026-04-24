# REST API Reference

Complete reference for Flint's HTTP API. All endpoints live under `/api/v1/*` and are served by FastAPI from `flint.api.main:app`.

- **Base URL** (default): `http://127.0.0.1:8000`
- **Auth**: none — API is bound to loopback by default. Do not expose to the internet without a reverse proxy + auth layer.
- **Content type**: `application/json` for all POST/PUT bodies
- **OpenAPI**: auto-generated interactive docs at `/docs` (Swagger UI) and `/redoc` when the server is running
- **Timestamps**: all `ts`, `start_ts`, `end_ts` fields are **unix seconds** unless explicitly labeled otherwise
- **Async jobs**: `/backtest/run`, `/optimize/run`, `/data/download-async` return a run/dl ID; poll `<endpoint>/{id}/status` or `<endpoint>/{id}/results` until `status == "complete"`

**Routers (12):** [backtest](#backtest) · [strategies](#strategies) · [user-strategies](#user-strategies) · [paper](#paper-trading) · [live](#live-trading) · [data](#data) · [collector](#collector) · [optimize](#optimization) · [journal](#journal) · [mev](#mev) · [system](#system) · [websocket](#websocket)

---

## Global conventions

### Async job pattern

Submit-and-poll is used for any endpoint that may run >1s:

```bash
# 1. Submit
RUN_ID=$(curl -s -X POST localhost:8000/api/v1/backtest/run \
  -H 'Content-Type: application/json' -d @req.json | jq -r .id)

# 2. Poll
while true; do
  R=$(curl -s localhost:8000/api/v1/backtest/$RUN_ID/results)
  STATUS=$(echo $R | jq -r .status)
  [ "$STATUS" = "complete" ] && break
  [ "$STATUS" = "failed" ] && { echo $R | jq .; exit 1; }
  sleep 1
done
echo $R | jq .results
```

Response envelope while running:

```json
{
  "id": "abc123",
  "status": "running",
  "progress": { "phase": "simulating", "pct": 47.0, "detail": "bar 1234/2630", "elapsed_s": 3.2 }
}
```

Terminal states: `complete`, `failed`, `cancelled`.

### Error format

4xx/5xx responses follow FastAPI's default:

```json
{ "detail": "Start date must be before end date" }
```

Some endpoints return `{"error": "..."}` inline in a 200 response for soft failures (e.g. data queries with no data).

### Limits

| Limit | Value | Where |
|---|---|---|
| Max concurrent backtests | 5 (configurable via `FLINT_MAX_CONCURRENT_BACKTESTS`) | `flint/api/routes/backtest.py` |
| Backtest wall-clock | 300s | hard-coded in engine |
| `limit` on `/data/ohlcv` | 10000 | Query validator |
| In-memory run registry | 200 entries / 3600s TTL | per-router |

---

## Backtest

**Prefix:** `/api/v1/backtest` · 10 endpoints

### `POST /run`

Submit a backtest job. Returns an ID to poll.

**Request** (`BacktestRequest`):

| Field | Type | Default | Notes |
|---|---|---|---|
| `strategy` | str | `"ma_crossover"` | Built-in name, `user:<filename>`, or ignored if `code` is set |
| `code` | str? | — | Inline Python — overrides `strategy` |
| `market` | str | `"SOL-PERP"` | Primary market |
| `markets` | list[str]? | — | Extra markets for multi-market strategies (`ctx.get_candles(...)`) |
| `resolution_s` | int | 3600 | Candle width in seconds |
| `start_ts` | int | **required** | Unix seconds |
| `end_ts` | int | **required** | Unix seconds; must be `> start_ts + 10*resolution_s` |
| `initial_capital` | float | 10000 | `(0, 1e12]` |
| `fee_rate` | float | 0.0005 | `[0, 0.5]` |
| `params` | dict? | — | Strategy params (override defaults) |
| `margin_tracking` | bool | false | Enable per-venue margin engine |
| `capital_allocation` | dict[str, float]? | — | e.g. `{"drift": 5000, "hyperliquid": 5000}` |
| `fill_model` | str | `"pipeline"` | `pipeline` / `slippage` / `close` / `next_bar_open` |
| `slippage_bps` | float | 10.0 | Used when `fill_model="slippage"` |
| `latency_enabled` | bool | true | Add venue-specific latency in pipeline |
| `latency_seed` | int? | — | Deterministic latency for tests |
| `impact_coefficient` | float? | — | Override sqrt-impact coefficient; else use venue default |
| `aggregate` | bool | false | Run independently per market and return per-market + summary |

**Response:**

```json
{ "id": "uuid", "status": "running", "data": { "available": 2630, "expected": 2160, "source": "local" } }
```

**Errors:** `400` on invalid dates, missing data source, or `code` compile errors.

### `GET /list`

List all tracked backtest runs (up to 200, TTL 1h).

```json
{ "runs": [ { "id": "...", "status": "complete", "phase": "done", "detail": "" } ] }
```

### `GET /{run_id}/status`

Lightweight poll — status only.

```json
{ "id": "...", "status": "running" }
```

### `GET /{run_id}/results`

Full results. While running, includes `progress`. On `complete`, includes `results` with metrics, trades, equity curve, and tearsheet.

```json
{
  "id": "...",
  "status": "complete",
  "results": {
    "strategy_name": "momentum",
    "metrics": { "total_pnl": 1234.5, "sharpe_ratio": 1.8, "max_drawdown": 0.12, "win_rate": 0.56, "profit_factor": 1.45, "total_fees": 87.0, "funding_paid": -3.4 },
    "equity_curve": [[ts, equity], ...],
    "trades": [ { "ts": 1700000000, "side": "long", "price": 142.3, "size": 10.0, "pnl": 55.2 }, ... ],
    "tearsheet": { ... },
    "monte_carlo": { "mean_pnl": 1220, "p05": 340, "p95": 2180 }
  }
}
```

### `POST /{run_id}/cancel`

Mark a running backtest as `cancelled`. Does not kill in-flight work immediately; the engine checks the flag between bars.

### `GET /compare?ids=id1,id2,id3`

Side-by-side metrics + equity curves for multiple runs.

```json
{ "comparisons": [ { "id": "...", "strategy": "momentum", "metrics": {...}, "equity_curve": [...] } ] }
```

### `GET /regimes`

List 8 curated market regimes (Dec 2023 – Apr 2026).

```json
{ "regimes": [ { "id": "etf_bull_run", "name": "ETF Bull Run", "start_ts": ..., "end_ts": ..., "type": "bull", "description": "..." } ] }
```

### `POST /run-regimes`

Run a single strategy across multiple regimes in parallel.

**Request:**

```json
{ "regime_ids": ["etf_bull_run", "summer_correction"], "code": "...", "market": "SOL-PERP",
  "resolution_s": 3600, "initial_capital": 10000, "fee_rate": 0.0005, "margin_tracking": false }
```

**Response:** `{ "id": "uuid", "status": "running" }` — poll via `/{run_id}/results`.

### `POST /calibrate`

Calibrate slippage impact coefficients from live fill data. Read-only — does not mutate config.

**Request:** `{ "venue": "drift", "market": "SOL-PERP", "lookback_days": 30 }`

**Response:** Calibration report with power-law and sqrt-impact fits and a recommended coefficient. See [how-to/calibrate-slippage.md](../how-to/calibrate-slippage.md) for the workflow.

### `POST /parity`

Run backtest vs paper engines on the same time window and report divergence.

**Request:**

```json
{ "market": "SOL-PERP", "strategy": "momentum", "start_ts": ..., "end_ts": ...,
  "capital": 10000, "fee_rate": 0.0005, "params": {} }
```

**Response:** PnL divergence, fill price MAE, equity curve correlation. Pass threshold: `<2%` PnL divergence.

---

## Strategies

**Prefix:** `/api/v1/strategies` · 2 endpoints — catalog of the 20 built-in templates.

### `GET /`

```json
{ "strategies": [ { "name": "ma_crossover", "display_name": "MA Crossover", "description": "...",
  "params": { "fast_period": {...}, "slow_period": {...} }, "markets": ["SOL-PERP", ...],
  "type": "trend", "venues": ["drift"], "needs_funding": false } ] }
```

### `GET /{name}`

Single entry from the same catalog. `404` if unknown.

---

## User Strategies

**Prefix:** `/api/v1/user-strategies` · 5 endpoints — save/load user strategy code to `strategies/user/*.py`.

| Method | Path | Request | Response |
|---|---|---|---|
| `POST` | `/` | `{"name": str, "code": str}` | `{"name": str, "saved": true}` |
| `GET` | `/` | — | `{"strategies": [{"name", "params"}, ...]}` |
| `GET` | `/{name}` | — | `{"name": str, "code": str, "params": dict}` |
| `DELETE` | `/{name}` | — | `{"deleted": true}` |
| `POST` | `/validate` | `{"code": str}` | `{"valid": bool, "error": str?}` |

Names match `^[a-zA-Z][a-zA-Z0-9_-]{0,63}$`. Other names are rejected with `400`.

Strategies are AST-validated: allowed imports are `flint`, `numpy`, `math`, `statistics`, `collections`, `dataclasses`, `typing`, `enum`, `abc`, `functools`, `itertools`, `operator`. Anything else raises `StrategyLoadError`.

---

## Paper Trading

**Prefix:** `/api/v1/paper` · 12 endpoints

### `POST /start`

Start a paper trading session.

**Request** (`StartRequest`):

| Field | Type | Default |
|---|---|---|
| `strategy` | str | `"ma_crossover"` |
| `code` | str? | — |
| `market` | str | `"SOL-PERP"` |
| `resolution_s` | int | 3600 |
| `initial_capital` | float | 10000 |
| `params` | dict? | — |
| `venue` | str | `"drift"` |
| `risk_config` | dict? | — See schema below |

`risk_config` shape:

```json
{ "max_drawdown_pct": 0.15, "daily_loss_limit": 500.0, "max_position_pct": 0.95, "liquidation_enabled": true }
```

**Response:** `{ "session_id": "uuid", "status": "running" }`

### `POST /stop`

Graceful stop — closes positions, saves state.

**Request:** `{ "session_id": str }` · **Response:** `{ "session_id": str, "stopped": bool }`

### `POST /kill`

Force-kill without closing positions.

**Request:** `{ "session_id": str }` · **Response:** `{ "session_id": str, "killed": bool }`

### `GET /status/{session_id}`

Detailed session status including margin and equity curve (last 200 points).

```json
{
  "session_id": "...", "status": "running", "strategy": "momentum", "market": "SOL-PERP",
  "equity": 10234.5, "initial_capital": 10000, "realized_pnl": 120, "unrealized_pnl": 114.5,
  "positions": [ { "market": "SOL-PERP", "side": "long", "size": 10, "entry_price": 142, "unrealized_pnl": 114.5 } ],
  "margin": { "leverage": 1.5, "margin_used": 1420, "free_margin": 8814, "margin_ratio": 7.2, "liquidation_prices": { "SOL-PERP": 12.5 } },
  "funding_total": -2.1,
  "equity_curve": [[ts, equity], ...],
  "trades": [...]
}
```

`404` if the session does not exist.

### `GET /sessions`

List all sessions (active + stopped).

```json
{ "sessions": [ { "session_id": "...", "strategy": "...", "market": "...", "venue": "drift", "status": "running", "equity": 10234, "realized_pnl": 120, "unrealized_pnl": 114, "total_trades": 12, "initial_capital": 10000 } ] }
```

### `GET /portfolio`

Aggregate across all sessions.

```json
{ "total_equity": 20469, "total_pnl": 469, "total_initial_capital": 20000,
  "active_sessions": 2, "total_sessions": 5, "per_strategy": [...] }
```

### `POST /deploy`

Replay-forward deploy from BacktestLab. Replays up to 30d of history then transitions to live candle processing.

**Request:**

```json
{ "strategy_code": "...", "strategy_params": {}, "market": "SOL-PERP",
  "initial_capital": 10000, "replay_start_ts": 1700000000, "resolution_s": 3600,
  "risk_config": {}, "capital_allocation": {}, "venue": "drift" }
```

**Response:** `{ "session_id": str, "status": "deployed" }`

### `POST /redeploy`

Redeploy a session from a new start date (rewinds and replays).

**Request:** `{ "session_id": str, "replay_start_ts": int }` · **Response:** `{ "old_session_id", "new_session_id", "status": "redeployed" }`

### `POST /redeploy-all`

Redeploy every active session from the same start date.

**Request:** `{ "replay_start_ts": int }` · **Response:** `{ "redeployed": [{old, new}, ...] }`

### `GET /trades/{session_id}`

All fills recorded for a session.

```json
{ "trades": [ { "ts": ..., "side": "long", "price": 142, "size": 10, "fee": 0.07, "pnl": 55.2, "venue": "drift" } ] }
```

### `GET /{session_id}/equity-history`

Full equity curve from DuckDB (not just the in-memory tail).

```json
{ "equity_curve": [[ts, equity], ...] }
```

### `POST /{session_id}/risk`

Update risk config on a running session. Same `risk_config` schema as `/start`.

---

## Live Trading

**Prefix:** `/api/v1/live` · 3 endpoints — read-only monitoring for real-money sessions. To start a live session, use `flint live` (CLI) or `POST /paper/deploy` with a live venue config.

| Method | Path | Query | Response |
|---|---|---|---|
| `GET` | `/fills` | `session_id` (required), `venue?`, `market?` | `{"fills": [{order_id, venue, market, fill_price, size, side, ts, fee}, ...]}` |
| `GET` | `/equity` | `session_id` (required) | `{"equity": [{ts, equity, cash, unrealized_pnl}, ...]}` |
| `GET` | `/sessions` | — | `{"sessions": [{session_id, venue, market, status, connected, started_at}, ...]}` |

---

## Data

**Prefix:** `/api/v1/data` · 18 endpoints

### Query endpoints

| Method | Path | Query | Purpose |
|---|---|---|---|
| `GET` | `/ohlcv` | `market` (req), `resolution_s` (3600), `start_ts?`, `end_ts?`, `limit` (1000, max 10000), `venue?` | Candles. When `venue` omitted, dedupes by timestamp preferring Pyth for price |
| `GET` | `/volume` | `market` (`SOL-PERP`), `resolution_s` (3600), `start_ts?`, `end_ts?` | Per-venue volume (excludes Pyth, no volume) |
| `GET` | `/funding` | `market` (req), `start_ts?`, `end_ts?` | Funding rates grouped by venue. Defaults to 30d lookback |
| `GET` | `/borrow-rates` | `market` (`SOL-PERP`), `start_ts?`, `end_ts?` | Jupiter lending borrow rate history |
| `GET` | `/markets` | — | List markets with data in local store |
| `GET` | `/freshness` | — | How old each market's latest candle is |
| `GET` | `/correlation` | `markets` (csv), `resolution_s` (3600), `start_ts`, `end_ts` | Correlation matrix between markets |
| `GET` | `/open-interest/{market}` | — | OI history for a perp market |
| `GET` | `/liquidations/{market}` | — | Liquidation events from on-chain |
| `GET` | `/whale-transfers` | — | Large wallet movements (Helius) |
| `GET` | `/dex-volume/{market}` | — | DEX trading volume per venue |
| `GET` | `/check` | `market` or `markets`, `resolution_s`, `start_ts`, `end_ts` | Coverage + funding/OB/OI availability for a date range |
| `POST` | `/check-markets` | body: `{markets: [...], resolution_s, start_ts, end_ts}` | Same as `/check` for multiple markets; returns `ready` flag + `missing` list |
| `GET` | `/available-markets` | — | Catalog of downloadable markets (Drift perps + CoinGecko spot) |
| `GET` | `/providers` | — | Provider registration + availability status |

#### Sample response — `/ohlcv`

```json
{
  "market": "SOL-PERP", "resolution_s": 3600, "venue": "all", "count": 2160,
  "candles": [ { "ts": 1700000000, "open": 142.0, "high": 143.1, "low": 141.8, "close": 142.9, "volume": 320450.0, "venue": "drift" } ]
}
```

#### Sample response — `/funding`

```json
{
  "market": "SOL-PERP", "count": 720,
  "venues": {
    "drift":        [{"ts": ..., "rate": 0.00003, "rate_bps": 0.3}],
    "hyperliquid":  [{"ts": ..., "rate": 0.00012, "rate_bps": 1.2}]
  }
}
```

### Mutation endpoints

#### `DELETE /market/{market}`

Purge all data for a market across `candles`, `venue_funding_rates`, `oracle_prices`, `orderbook_snapshots`, `open_interest`, `liquidations`, and `sync_metadata`.

```json
{ "market": "SOL-PERP", "deleted": { "candles": 2160, "venue_funding_rates": 720 }, "total_records": 2880 }
```

#### `POST /download`

Synchronous download. Blocks until complete or 503 on timeout. **Prefer `/download-async` for ranges >30 days.**

**Request:** `{ "market": "SOL-PERP", "start_ts": ..., "end_ts": ..., "venues": ["drift","hyperliquid"]? }`

**Response:** `{ "market": ..., "candles_downloaded": ..., "funding_venues": [...], "source": "drift_s3" }`

#### `POST /download-async`

Fire-and-forget with a poll endpoint. Supports multi-market.

**Request:** `{ "markets": ["SOL-PERP", "BTC-PERP"], "start_ts": ..., "end_ts": ..., "venues": [...]? }`

**Response:** `{ "download_id": "uuid" }`

#### `GET /download-async/{dl_id}/status`

```json
{
  "status": "downloading",
  "progress": {
    "markets": [ { "market": "SOL-PERP", "status": "complete", "candles": 720 } ],
    "pct": 50.0,
    "elapsed_s": 12.3
  },
  "result": null
}
```

Terminal `status`: `complete`, `failed`.

---

## Collector

**Prefix:** `/api/v1/collector` · 3 endpoints — background data collection service.

| Method | Path | Request | Response |
|---|---|---|---|
| `GET` | `/status` | — | `{"running": bool, "status": [{market, data_type, last_ts}]}` |
| `POST` | `/trigger` | `{market, data_type}` | `{"triggered": bool, market, data_type}` |
| `GET` | `/config` | — | `{markets, candle_backfill_days, intervals: {candles, funding, orderbook, oracle}}` |

`data_type` ∈ `candles` · `funding` · `orderbook` · `oracle` · `open_interest`.

---

## Optimization

**Prefix:** `/api/v1/optimize` · 3 endpoints

### `POST /run`

Submit an Optuna hyperparameter optimization job.

**Request:**

| Field | Type | Default |
|---|---|---|
| `code` | str | **required** |
| `market` | str | `"SOL-PERP"` |
| `resolution_s` | int | 3600 |
| `start_ts` / `end_ts` | int | **required** |
| `initial_capital` | float | 10000 |
| `metric` | str | `"sharpe_ratio"` (also `total_pnl`, `sortino`, `profit_factor`, `calmar_ratio`) |
| `trials` | int | 50 |
| `fill_model` | str | `"pipeline"` |
| `slippage_bps` | float | 10.0 |
| `margin_tracking` | bool | false |
| `capital_allocation` | dict? | — |
| `markets` | list[str]? | — |

**Response:** `{ "id": "uuid", "status": "running" }` — poll via `/{run_id}/results`.

### `POST /walk-forward`

Rolling train/test windows. Each window optimizes on train, measures on test; reports in-sample vs out-of-sample performance and overfitting ratio.

**Request:**

```json
{ "code": "...", "market": "SOL-PERP", "start_ts": ..., "end_ts": ...,
  "n_windows": 5, "train_pct": 0.7, "trials_per_window": 30,
  "metric": "sharpe_ratio", "initial_capital": 10000 }
```

**Response:** `{ "id": "uuid", "status": "running" }`.

### `GET /{run_id}/results`

```json
{
  "id": "...", "status": "complete",
  "results": {
    "best_value": 2.3,
    "best_params": { "fast_period": 12, "slow_period": 38 },
    "trials": [ { "metric_value": 2.3, "total_pnl": 1200, "params": {...} } ],
    "param_importance": { "fast_period": 0.6, "slow_period": 0.4 },
    "convergence": [[trial_num, best_so_far], ...],
    "walk_forward": { "windows": [...], "overfitting_ratio": 0.72, "parameter_stability": 0.8 }
  }
}
```

---

## Journal

**Prefix:** `/api/v1/journal` · 4 endpoints — browse persisted backtest history.

| Method | Path | Query / Body | Response |
|---|---|---|---|
| `GET` | `/runs` | `limit` (20) | `{"runs": [{run_id, strategy_name, market, total_pnl, sharpe_ratio, max_drawdown, total_trades, win_rate, ts}]}` |
| `GET` | `/runs/{run_id}` | — | Full run record (metrics, trades, equity curve, params) |
| `DELETE` | `/runs/{run_id}` | — | `{"deleted": bool}` |
| `GET` | `/compare` | `ids` (csv) | `{"comparisons": [{id, strategy, metrics, equity_curve}]}` |

---

## MEV

**Prefix:** `/api/v1/mev` · 2 endpoints

### `POST /scan/arb`

Find profitable DEX arbitrage routes across Solana AMM pools.

**Request:**

```json
{
  "pools": [ { "pool_address": "...", "dex": "raydium", "token_a_mint": "...", "token_b_mint": "...",
                "reserve_a": 1.2e9, "reserve_b": 1.7e8, "fee_rate": 0.003 } ],
  "start_token": "...", "amount": 1000.0, "max_hops": 3, "min_profit_bps": 10.0
}
```

**Response:** `{ "routes": [ { "pools": [...], "tokens": [...], "input_amount": 1000, "output_amount": 1012.4, "profit": 12.4, "profit_bps": 124, "hops": 2 } ] }`

### `POST /scan/liquidations`

Find positions nearing liquidation on Drift/Mango given oracle prices.

**Request:**

```json
{
  "positions": [ { "user_account": "...", "market": "SOL-PERP", "size": 10, "collateral": 1000, "protocol": "drift" } ],
  "oracle_prices": { "SOL-PERP": 142.5 }
}
```

**Response:** `{ "opportunities": [ { "protocol": "drift", "user_account": "...", "market": "SOL-PERP",
  "side": "long", "liquidation_price": 130.1, "oracle_price": 142.5, "margin_ratio": 0.03, "estimated_profit": 18.2 } ] }`

---

## System

**Prefix:** `/api/v1/system` · 7 endpoints

| Method | Path | Request / Query | Response |
|---|---|---|---|
| `GET` | `/status` | — | `{"initialized": bool, "version": str, "config": {db_path, markets}}` |
| `POST` | `/config` | `{"birdeye_api_key"?, "helius_api_key"?}` | `{"saved": bool}` (appends to `.env`) |
| `GET` | `/venues` | — | Venue preset list with fees/margin/latency |
| `GET` | `/regimes` | — | Same as `/backtest/regimes` |
| `GET` | `/strategies` | — | All strategies from registry (built-in + user + custom) |
| `POST` | `/strategies` | `{name, code, params, category}` | Upsert into registry |
| `GET` | `/strategies/{name}` | — | Registry entry |
| `DELETE` | `/strategies/{name}` | — | `{"deleted": bool}` |

**Standalone:**

- `GET /api/v1/health` — `{"status": "ok", "service": "flint"}`

---

## WebSocket

### `/ws/{channel}`

Real-time updates. Channels: `all`, `backtest`, `paper`, `live`, `data`.

**Message envelope:**

```json
{ "type": "backtest.progress", "run_id": "...", "payload": { "phase": "simulating", "pct": 47.0 } }
```

Common `type` values:

- `backtest.progress`, `backtest.complete`, `backtest.failed`
- `paper.fill`, `paper.status`, `paper.equity`
- `live.fill`, `live.alert` (drawdown warning, kill switch)
- `data.download.progress`, `data.download.complete`

The server does not read client messages — any received data is treated as a keepalive.

---

## Changelog

API is at version `0.1.0`. Additive-only; no breaking changes are expected before 1.0. Deprecations land as a response field before removal.

See [reference/cli.md](cli.md) for the CLI equivalent of each endpoint, and [reference/python-sdk.md](python-sdk.md) for the in-process Python API.
