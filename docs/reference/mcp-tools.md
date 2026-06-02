# MCP Tool Reference

Flint ships an MCP (Model Context Protocol) server so AI models can drive the platform. 20 tools + 2 resources via stdio transport.

**Server module:** `flint.mcp_server` · **Framework:** `mcp.server.fastmcp.FastMCP`

## Install + register

```bash
pip install flint-trading[mcp]
claude mcp add flint -- python -m flint.mcp_server      # Claude Code
python -m flint.mcp_server                              # standalone stdio
```

Most write-oriented tools (paper trading, journal) hit the Flint HTTP API at `http://127.0.0.1:8000`. Start `flint serve` first. Read-oriented tools (backtests, candles, optimization) hit the store directly.

---

## Tools

All tools return JSON strings. Errors come back as `{"error": "..."}`.

### Backtesting

#### `run_backtest`

Run a backtest against local data. Auto-downloads from Hyperliquid (with Pyth oracle prices) if missing.

| Arg | Type | Default | Purpose |
|---|---|---|---|
| `market` | str | `SOL-PERP` | Market symbol |
| `strategy` | str | `ma_crossover` | Built-in name (ignored if `code` set) |
| `start_date` | str | `2025-01-01` | `YYYY-MM-DD` |
| `end_date` | str | `2025-06-01` | `YYYY-MM-DD` |
| `initial_capital` | float | 10000 | USD |
| `fee_rate` | float | 0.001 | 10 bps default (typical perp taker) |
| `resolution_s` | int | 3600 | Candle width |
| `fast_period` | int | 10 | For MA/EMA-like strategies |
| `slow_period` | int | 30 | For MA/EMA-like strategies |
| `code` | str | — | Custom Python strategy code |

Returns `{market, strategy, period, candles, total_pnl, total_return_pct, sharpe_ratio, max_drawdown_pct, total_trades, winning_trades, losing_trades, win_rate_pct, total_fees, params}`.

#### `list_strategies`

No args. Returns the full catalog of 20 built-in strategies with `{name, category, description, params}` plus category counts.

#### `optimize_strategy`

Optuna search.

| Arg | Type | Default |
|---|---|---|
| `market` | str | `SOL-PERP` |
| `strategy` | str | `ma_crossover` |
| `start_date` / `end_date` | str | `2025-01-01` / `2025-06-01` |
| `metric` | str | `sharpe_ratio` (`total_pnl`, `sortino`, `profit_factor`, `calmar_ratio`) |
| `trials` | int | 30 |
| `resolution_s` | int | 3600 |

Returns `{best_params, best_score, top_trials}`.

### Paper trading

All four paper tools require `flint serve` running.

#### `start_paper_trading`

| Arg | Type | Default |
|---|---|---|
| `market` | str | `SOL-PERP` |
| `strategy` | str | `rsi_macd_combo` |
| `initial_capital` | float | 10000 |
| `code` | str | — |
| `max_drawdown_pct` | float | 0.15 |

Returns `{session_id, status: "running"}`.

#### `stop_paper_trading`

Arg: `session_id` (str). Graceful close.

#### `get_paper_sessions`

No args. Returns `{sessions: [{session_id, strategy, market, status, equity, pnl, trades}], count}`.

#### `get_paper_status`

Arg: `session_id`. Full session snapshot — positions, equity, margin, trades.

### Journal

#### `list_journal_runs`

Arg: `limit` (int, default 20). Returns `{runs: [{run_id, strategy, market, pnl, return_pct, sharpe, max_dd, trades, win_rate}], count}`.

#### `compare_runs`

Arg: `run_ids` (csv str). Returns side-by-side metrics.

### Data

#### `get_candles`

| Arg | Type | Default |
|---|---|---|
| `market` | str | `SOL-PERP` |
| `resolution_s` | int | 3600 |
| `start_date` / `end_date` | str | — |
| `limit` | int | 100 |

Returns `{market, resolution, count, first, last, current_price, candles: [{ts, date, ohlcv}]}`.

#### `download_market_data`

| Arg | Type | Default |
|---|---|---|
| `market` | str | `SOL-PERP` |
| `days` | int | 90 |
| `resolution_s` | int | 3600 |
| `funding_venues` | str | `hyperliquid,okx,bybit,dydx,gateio,bitget` |

Skips the download if range is already covered (`store.is_range_synced`). Returns `{downloaded, cached, previously_existing, total, funding_fetched, funding_venues, source}`.

#### `list_available_markets`

No args. Returns `{perpetuals, perp_count, spot_all, spot_count, total}` from `flint.collector.tasks.MARKET_INDEX`.

#### `list_local_markets`

No args. Returns what's in the local DuckDB: `{markets: [{market, resolution, candle_count, from, to, type}], total_markets, total_candles}`.

#### `get_funding_rates`

| Arg | Type | Default |
|---|---|---|
| `market` | str | `SOL-PERP` |
| `venue` | str | `""` (all) |
| `limit` | int | 50 |

Returns `{market, venues, total_points, venue_data: {venue: {count, avg_rate_bps, annualized_pct, latest, recent_rates}}}`.

#### `get_open_interest`

Arg: `market` (str, default `SOL-PERP`). Returns `{latest_long_oi, latest_short_oi, net_oi, total_oi, records}`.

#### `get_correlation`

| Arg | Type | Default |
|---|---|---|
| `markets` | str (csv) | `SOL-PERP,BTC-PERP,ETH-PERP` |
| `resolution_s` | int | 3600 |

Returns `{markets, correlation_matrix: {m1: {m2: float}}}`.

#### `get_data_freshness`

No args. Returns `{freshness: [...], total_tracked}` — age of each tracked market/source.

---

## Resources

| URI | Purpose |
|---|---|
| `flint://guide` | Reads `docs/guides/quickstart.md` — usage overview for the model |
| `flint://markets` | Same output as `list_available_markets` |

---

## Typical workflows

**Evaluate a strategy idea end-to-end:**

1. `list_available_markets` → pick a market
2. `download_market_data(market, days=180)`
3. `run_backtest(market, strategy, ...)`
4. `optimize_strategy(market, strategy, ...)` if promising
5. `start_paper_trading(market, code=<best-params-strategy>)` to deploy
6. `get_paper_status(session_id)` to monitor

**Audit past work:**

1. `list_journal_runs(limit=50)`
2. `compare_runs(run_ids="a,b,c")`

---

## Notes

- All data tools are **read-only** against DuckDB — safe to call in parallel.
- Paper trading tools mutate state on the running server; treat as non-idempotent.
- `run_backtest` does **not** persist to the journal — use the HTTP API or Web UI for that.
- Custom `code` in `run_backtest`/`start_paper_trading` is AST-validated. Disallowed imports raise `StrategyLoadError`. See allowed imports in [python-sdk.md §Sandbox](python-sdk.md#sandbox).
