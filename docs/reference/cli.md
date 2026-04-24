# CLI Reference

Complete reference for the `flint` command-line interface. Entrypoint is `flint.cli:main`, built with Typer.

```
flint [--help]  <command>  [args...]
```

Top-level commands: [`init`](#flint-init) · [`backtest`](#flint-backtest) · [`optimize`](#flint-optimize) · [`serve`](#flint-serve) · [`new`](#flint-new) · [`live`](#flint-live) · [`parity`](#flint-parity) · [`calibrate`](#flint-calibrate) · [`data ...`](#flint-data) · [`data provider ...`](#flint-data-provider)

Common:

- All commands read `flint.yaml` + `.env` + `FLINT_*` env vars via `flint.config.load_config()`.
- Dates take `YYYY-MM-DD` (UTC) unless noted.
- Periods take `30d`, `3m`, `1y` (days / months / years).
- Exit codes: `0` success, `1` user error, `130` Ctrl-C.

---

## `flint init`

Scaffold a project — create `flint.yaml`, `data/`, `strategies/user/`, backfill candles, run a sample backtest.

```
flint init [--days 90] [--market SOL-PERP] [--skip-demo]
```

| Flag | Default | Purpose |
|---|---|---|
| `--days` | 90 | Days of data to backfill |
| `--market` | `SOL-PERP` | Primary market to backfill |
| `--skip-demo` | false | Skip the sample MA-crossover backtest at the end |

Idempotent — re-running does not clobber existing config, and skips downloads if coverage is ≥80%.

Source order for candles: **Pyth → Drift → 2024 fallback range**. If all fail, `init` exits cleanly with guidance to run `flint data download` manually.

---

## `flint backtest`

Run a backtest from a strategy file.

```
flint backtest <strategy.py>
  [--market SOL-PERP]     [-m]
  [--resolution 3600]     [-r]
  [--start YYYY-MM-DD]    [-s]
  [--end   YYYY-MM-DD]    [-e]
  [--period 90d]          [-p]
  [--capital 10000]       [-c]
  [--fee 0.0005]
```

Date handling:

- `--period` overrides `--start`/`--end`.
- `--start` alone → `--end` defaults to now.
- Neither → last 90 days.

Execution mode:

- If a Flint server is running at `localhost:8000`, the CLI submits via API and tails progress.
- Otherwise, runs in-process against `./data/flint.duckdb`. Missing data is auto-fetched from Drift S3.

Output: Rich table with PnL, Sharpe, drawdown, trades, fees, funding, plus an ASCII equity sparkline.

**Example:**

```bash
flint backtest strategies/user/my_rsi.py -m BTC-PERP -p 180d -c 25000
```

---

## `flint optimize`

Hyperparameter search via Optuna.

```
flint optimize <strategy.py>
  [--market SOL-PERP]              [-m]
  [--resolution 3600]              [-r]
  [--start YYYY-MM-DD]             [-s]
  [--end   YYYY-MM-DD]             [-e]
  [--period 90d]                   [-p]
  [--metric sharpe_ratio]
  [--trials 50]                    [-n]
  [--capital 10000]                [-c]
```

| Flag | Default | Notes |
|---|---|---|
| `--metric` | `sharpe_ratio` | `total_pnl`, `sortino`, `profit_factor`, `calmar_ratio` |
| `--trials` | 50 | 50 is a good default; 200 for thorough |

The strategy **must** define a `parameters()` classmethod — see [python-sdk.md §Strategy.parameters](python-sdk.md#strategyparameters). The CLI prints the best params and top 5 trials.

**Example:**

```bash
flint optimize strategies/user/ma_cross.py -p 1y --metric sortino -n 100
```

---

## `flint serve`

Start the FastAPI server (with UI).

```
flint serve [--host 127.0.0.1] [--port 8000] [--dev]
```

| Flag | Default | Purpose |
|---|---|---|
| `--host` | `127.0.0.1` | Bind address |
| `--port` | 8000 | Bind port |
| `--dev` | false | API only, no UI build, auto-reload on code change |

Production mode (default): builds `ui/dist/` via `npm run build` if missing/stale, serves UI + API from one process. Falls back to API-only if `npm` is not on PATH.

Dev mode: run `cd ui && npm run dev` in a second terminal for hot-reload UI at `:5173` (Vite proxies API to `:8000`).

---

## `flint new`

Scaffold a strategy file into `strategies/user/`.

```
flint new <name> [--v2]
```

| Flag | Default | Purpose |
|---|---|---|
| `--v2` | false | Use the v2 `ExecutionContext` API template (orders via `ctx.market_order()`) |

The default template uses the v1 Signal API (`return Signal.BUY/SELL/HOLD`). Both are supported by the engine. Errors if the file already exists.

---

## `flint live`

Run a strategy live against a venue.

```
flint live <strategy.py>
  [--market SOL-PERP]      [-m]
  [--paper / --real]
  [--capital 10000]        [-c]
  [--key <base58>]
  [--rpc  <url>]
```

| Flag | Default | Purpose |
|---|---|---|
| `--paper` | true | Paper trading against live feeds (no real orders) |
| `--real` | — | Real money. Requires confirmation prompt and `--key` or `FLINT_PRIVATE_KEY` |
| `--key` | env `FLINT_PRIVATE_KEY` | Base58 Solana keypair |
| `--rpc` | config `solana_rpc_url` | Override RPC URL |

In `--real` mode this connects via `driftpy` and reports open positions. For the full live deployment flow see [how-to/go-live-on-drift.md](../how-to/go-live-on-drift.md) and [concepts/risk-model.md](../concepts/risk-model.md).

`--real` requires `FLINT_LIVE_NETWORK` to match the intended network. Default is `devnet`; set `live_network: mainnet` in `flint.yaml` or pass through env.

---

## `flint parity`

Run a backtest-vs-paper parity test. Fails if divergence exceeds thresholds.

```
flint parity <strategy>
  --start YYYY-MM-DD
  --end   YYYY-MM-DD
  [--market SOL-PERP]
  [--capital 10000]
  [--fee 0.0005]
```

`<strategy>` is a **built-in** strategy name (momentum, rsi, etc.), not a file path. Uses the same candle data for both engines, then diffs PnL, fill prices, and equity curves.

---

## `flint calibrate`

Fit slippage impact coefficients from historical fills.

```
flint calibrate <venue>
  [--market SOL-PERP]
  [--lookback 30]
  [--dry-run]
```

| Flag | Default | Purpose |
|---|---|---|
| `venue` | **required** | `drift`, `hyperliquid`, etc. |
| `--market` | `SOL-PERP` | Market to calibrate |
| `--lookback` | 30 | Days of live fill data |
| `--dry-run` | false | Print report without writing to `flint.yaml` |

Writes `venues.<venue>.impact_coefficient` to `flint.yaml` unless `--dry-run`.

Requires fills from live or paper trading — minimum `calibration_min_fills` (default 100) fills in the lookback window, else exits non-zero.

---

## `flint data`

Data management commands.

### `flint data download`

Download historical candles from Drift S3.

```
flint data download
  [--market SOL-PERP]...         [-m]  # repeatable
  [--days 365]                    [-d]
  [--resolution 3600]             [-r]
  [--end-date YYYY-MM-DD]
```

`--market` is repeatable — `-m SOL-PERP -m BTC-PERP` downloads both. Shows a per-market progress bar and persists to `./data/flint.duckdb`.

### `flint data status`

Print data inventory.

```
flint data status
```

Queries the API if the server is running, else opens DuckDB read-only. Shows market / resolution / candle count / date range.

### `flint data exchanges`

List CEX exchanges available via CCXT.

```
flint data exchanges
```

### `flint data markets`

List tradable markets on a CEX.

```
flint data markets <exchange>
  [--type swap]      [-t]     # swap | spot | future | option
  [--quote USDT]     [-q]
  [--limit 50]       [-n]
```

---

## `flint data provider`

Manage data providers in `flint.yaml`.

| Command | Purpose |
|---|---|
| `flint data provider status` | Show provider table: enabled / available / requires API key / data types |
| `flint data provider list` | Alias for `status` |
| `flint data provider enable <name> [--api-key <key>]` | Enable provider; optionally append `FLINT_<NAME>_API_KEY` to `.env` |
| `flint data provider disable <name>` | Disable provider |

Provider names: `drift`, `pyth`, `hyperliquid`, `ccxt`, `birdeye`, `helius`, `raydium`, `orca`, `jupiter`, `coingecko`, `gecko`, `drift_oi`. See [reference/data-providers.md](data-providers.md).

---

## Environment variables

All `FlintConfig` fields can be overridden via `FLINT_*` env vars. Common ones:

| Variable | Default | Purpose |
|---|---|---|
| `FLINT_DB_PATH` | `./data/flint.duckdb` | DuckDB file |
| `FLINT_API_PORT` | 8000 | `flint serve` port |
| `FLINT_MAX_CONCURRENT_BACKTESTS` | 5 | Backtest concurrency limit |
| `FLINT_PRIVATE_KEY` | — | Base58 Solana keypair for `flint live --real` |
| `FLINT_LIVE_NETWORK` | `devnet` | `devnet` / `mainnet` |
| `FLINT_LIVE_DRY_RUN` | false | Skip order submission (log-only) |
| `FLINT_LIVE_KILL_SWITCH_DRAWDOWN_PCT` | 0.15 | Live kill switch |
| `FLINT_BIRDEYE_API_KEY` | — | Birdeye provider key |
| `FLINT_HELIUS_API_KEY` | — | Helius provider key |

Full schema: [reference/config.md](config.md).

---

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | User error — bad args, missing file, no data, API unreachable |
| 130 | SIGINT (Ctrl-C) |

---

## See also

- [REST API Reference](rest-api.md) — each CLI command has an API equivalent
- [Config Reference](config.md) — full `flint.yaml` + env schema
- [Python SDK Reference](python-sdk.md) — in-process API if you don't need the server
