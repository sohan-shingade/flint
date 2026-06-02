# Config Reference

Complete schema for `flint.yaml`, `.env`, and `FLINT_*` environment variables.

**Loader:** `flint.config.load_config()` returns a `FlintConfig` instance (Pydantic settings).

## Precedence

Highest wins:

1. **Environment variables** (`FLINT_*` prefix)
2. **`.env` file** (same prefix)
3. **`flint.yaml`** — nested sections are flattened (`{"db": {"path": "x"}}` → `db_path=x`)
4. **Defaults** (below)

Unknown keys are ignored (Pydantic `extra="ignore"`).

## Example `flint.yaml`

```yaml
db:
  path: ./data/flint.duckdb

trading:
  default_markets: [SOL-PERP, BTC-PERP, ETH-PERP]
  default_fee_rate: 0.0005
  default_capital: 10000.0

collector:
  enabled: true
  candle_backfill_days: 90
  candle_interval_s: 3600
  funding_interval_s: 3600
  oracle_interval_s: 60
  orderbook_interval_s: 300

risk:
  max_drawdown_pct: 0.20
  default_stop_loss_pct: 0.05
  max_open_trades: 5

api:
  host: 127.0.0.1
  port: 8000
  max_concurrent_backtests: 5

providers:
  birdeye:
    enabled: false
  helius:
    enabled: false

ccxt:
  exchange: binance

live:
  network: devnet
  dry_run: false
  kill_switch_drawdown_pct: 0.15
  max_orders_per_minute: 30
```

## Schema

All fields map 1:1 to `FlintConfig` attributes. Env-var name is `FLINT_<UPPER_FIELD>`.

### Database

| Field | Type | Default | Notes |
|---|---|---|---|
| `db_path` | str | `./data/flint.duckdb` | DuckDB file. Single-writer — do not point multiple processes at the same file |

### Trading defaults

| Field | Type | Default |
|---|---|---|
| `default_markets` | list[str] | `[SOL-PERP, BTC-PERP, ETH-PERP]` |
| `default_fee_rate` | float | 0.0005 (5 bps) |
| `default_capital` | float | 10000 |

### Collector

Background service that keeps market data up to date. Disabled by default for dev.

| Field | Type | Default | Notes |
|---|---|---|---|
| `collector_enabled` | bool | true | Start collector at server boot |
| `candle_backfill_days` | int | 90 | History depth at first run |
| `candle_interval_s` | int | 3600 | Poll interval for new candles |
| `funding_interval_s` | int | 3600 | Poll interval for funding rates |
| `oracle_interval_s` | int | 60 | Pyth oracle polling |
| `orderbook_interval_s` | int | 300 | L2 snapshot interval |

### Risk defaults

| Field | Type | Default |
|---|---|---|
| `max_drawdown_pct` | float | 0.20 |
| `default_stop_loss_pct` | float | 0.05 |
| `max_open_trades` | int | 5 |

### Paper trading

| Field | Type | Default |
|---|---|---|
| `paper_trading_capital` | float | 10000 |

### API / server

| Field | Type | Default |
|---|---|---|
| `api_host` | str | `127.0.0.1` |
| `api_port` | int | 8000 |
| `max_concurrent_backtests` | int | 5 |
| `cors_origins` | list[str] | localhost:5173 + localhost:8000 + 127.0.0.1 variants |

### Provider API keys

Optional providers. Empty string = disabled.

| Field | Type | Purpose |
|---|---|---|
| `birdeye_api_key` | str | Birdeye token OHLCV (free tier available) |
| `helius_api_key` | str | Helius RPC + indexing (liquidations, whale tracking) |
| `dune_api_key` | str | Dune Analytics (borrow-rate backfill) |
| `tardis_api_key` | str | Tardis.dev (CEX orderbook data) |

### CCXT (optional extra)

Install with `pip install flint-trading[ccxt]`.

| Field | Type | Default |
|---|---|---|
| `ccxt_exchange` | str | `binance` |
| `ccxt_api_key` | str | — |
| `ccxt_secret` | str | — |

### Solana RPC

| Field | Type | Default |
|---|---|---|
| `solana_rpc_url` | str | `https://api.mainnet-beta.solana.com` |

Public RPCs rate-limit hard. For anything beyond `flint init`, use Helius / Triton / QuickNode.

### Live trading — core

| Field | Type | Default | Notes |
|---|---|---|---|
| `live_network` | str | `devnet` | `devnet` / `mainnet`. Gating: must be explicit to trade real money |
| `live_tick_interval_s` | int | 60 | Polling cadence when not in WS mode |
| `live_on_order_failure` | str | `drop` | `drop` / `retry` / `halt` |
| `live_max_retries` | int | 3 | Per-order retry cap |
| `live_position_sync_interval` | int | 5 | Seconds between on-chain position reconciliation |
| `live_limit_order_timeout_bars` | int | 10 | Cancel resting limits after N bars |
| `live_rate_limit_orders_per_sec` | int | 10 | Client-side order rate limit |
| `live_rate_limit_concurrent_tx` | int | 2 | In-flight tx cap |
| `live_wallet_mode` | str | `keypair` | `keypair` / `delegate` |

### Live trading — WebSocket feeds

| Field | Type | Default |
|---|---|---|
| `live_tick_mode` | str | `on_candle_close` |
| `live_candle_resolution_s` | int | 60 |
| `live_tick_markets` | list[str] | `[]` |

### Safety rails

| Field | Type | Default | Notes |
|---|---|---|---|
| `live_dry_run` | bool | false | Log orders; don't submit |
| `live_kill_switch_drawdown_pct` | float | 0.15 | Flatten all positions at this drawdown |
| `live_kill_switch_check_interval_s` | float | 5.0 | How often `EquityMonitor` runs |
| `live_max_orders_per_minute` | int | 30 | Sliding-window rate limit guard |
| `live_per_market_position_limits` | str | `""` | JSON map, e.g. `{"SOL-PERP": 50000}` (USD notional) |
| `live_drawdown_warning_pct` | float | 0.075 | Alert threshold |

### Hyperliquid

| Field | Type | Default |
|---|---|---|
| `live_hyperliquid_network` | str | `testnet` |
| `live_hyperliquid_market_order_slippage` | float | 0.003 (30 bps) |
| `live_hyperliquid_l2_persist_interval_s` | int | 60 |

### Multi-venue

| Field | Type | Default |
|---|---|---|
| `live_multi_venue_primary` | str | `""` |
| `live_multi_venue_tick_mode` | str | `primary` |
| `live_multi_venue_leg_timeout_s` | float | 30.0 |
| `live_multi_venue_auto_unwind` | bool | false |

### Slippage calibration

| Field | Type | Default |
|---|---|---|
| `calibration_drift_threshold_pct` | float | 15.0 |
| `calibration_min_fills` | int | 100 |

### Transaction costs (Solana)

| Field | Type | Default |
|---|---|---|
| `tx_cost_priority_fee_lamports` | int | 5000 |
| `tx_cost_jito_tip_lamports` | int | 10000 |
| `tx_cost_sol_price_usd` | float | 150.0 |

### vAMM fill model (dormant)

The vAMM (constant-product) fill path was built for Drift. Drift is dropped post-hack, so this path is dormant and disabled by default; it's retained for reference. Hyperliquid uses the CLOB fill model.

| Field | Type | Default |
|---|---|---|
| `vamm_enabled` | bool | false |
| `vamm_default_sqrt_k` | str | `""` |

### CLMM

| Field | Type | Default |
|---|---|---|
| `clmm_tick_fetch_enabled` | bool | false |
| `clmm_tick_persist_interval_s` | int | 300 |

### Jupiter Perps

| Field | Type | Default |
|---|---|---|
| `jupiter_perps_enabled` | bool | false |
| `jupiter_perps_sidecar_port` | int | 8401 |
| `jupiter_perps_rpc_url` | str | `""` |
| `jupiter_perps_wallet_path` | str | `""` |

### Notifications

| Field | Type | Default |
|---|---|---|
| `telegram_bot_token` | str | `""` |
| `telegram_chat_id` | str | `""` |
| `discord_webhook_url` | str | `""` |
| `webhook_url` | str | `""` (generic) |

### Price source

| Field | Type | Default |
|---|---|---|
| `price_source` | str | `pyth` |

---

## Per-venue config

Venue-specific fee / margin / latency presets live in `flint/execution/venue_config.py`, not in `FlintConfig`. Override in `flint.yaml` under `venues.<venue>.*`:

```yaml
venues:
  hyperliquid:
    impact_coefficient: 0.00018    # set by `flint calibrate`
  okx:
    impact_coefficient: 0.00030
```

Built-in venues + their defaults are documented in [reference/venue-configs.md](venue-configs.md).

---

## Provider config

Enable / disable + supply keys inline:

```yaml
providers:
  birdeye:
    enabled: true
    api_key: ${FLINT_BIRDEYE_API_KEY}
  helius:
    enabled: false
  ccxt:
    exchange: binance
```

Or toggle via CLI: `flint data provider enable birdeye --api-key $KEY`.

---

## Precedence examples

**Override via env at runtime:**

```bash
FLINT_API_PORT=9000 flint serve
FLINT_LIVE_DRY_RUN=1 flint live my.py --real
```

**Override via .env:**

```
FLINT_MAX_CONCURRENT_BACKTESTS=10
FLINT_BIRDEYE_API_KEY=xxx
FLINT_LIVE_KILL_SWITCH_DRAWDOWN_PCT=0.10
```

**Read config in code:**

```python
from flint.config import load_config
cfg = load_config()
print(cfg.db_path, cfg.default_markets, cfg.live_network)
```

---

## See also

- [CLI Reference](cli.md#environment-variables) — commonly used env vars
- [reference/venue-configs.md](venue-configs.md) — per-venue fees / margin / latency
- [concepts/risk-model.md](../concepts/risk-model.md) — how safety rails combine
