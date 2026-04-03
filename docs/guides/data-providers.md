# Data Provider Guide

Flint ships with 15 data providers covering Solana on-chain data, CEX candles, Hyperliquid perps, oracle prices, DEX pools, and cross-venue funding rates from 10 venues. All providers are managed by `flint/providers/registry.py`.

---

## Provider Table

| Provider | File | Auth Required | Data |
|---|---|---|---|
| Drift Data API | `providers/drift_candles.py` | None | OHLCV candles (48 markets) |
| Drift S3 | `providers/drift_s3.py` | None | Historical trade records, funding |
| Drift OI | `providers/open_interest.py` | None | Open interest |
| Drift Funding | `providers/drift_api.py` | None | Funding rates, L2/L3 orderbook |
| Hyperliquid Candles | `providers/hyperliquid_candles.py` | None | OHLCV candles for Hyperliquid perps |
| Birdeye | `providers/birdeye.py` | `FLINT_BIRDEYE_API_KEY` | Any Solana token OHLCV |
| Helius | `providers/helius.py` | `FLINT_HELIUS_API_KEY` | Liquidations, whale tracking |
| Pyth | `providers/pyth.py` | None | Oracle prices (20 pairs) |
| Raydium | `providers/raydium.py` | None | AMM/CLMM pool data |
| Orca | `providers/orca.py` | None | Whirlpool pool data |
| GeckoTerminal | `providers/gecko.py` | None | DEX pool OHLCV |
| Jupiter | `providers/jupiter.py` | None | Swap quotes |
| CoinGecko | `providers/coingecko.py` | None | Spot candles (BTC, ETH, etc.) |
| CCXT | `providers/ccxt_provider.py` | Optional (per-exchange) | 100+ CEX exchanges |
| Cross-venue funding | `providers/funding_rates.py` | None | 10 venues normalized to 1h |

---

## Multi-Venue Candle Storage

Candle data is stored per-venue in DuckDB. The `candles` table has a `venue` column as part of its primary key:

```sql
PRIMARY KEY (venue, market, resolution_s, ts)
```

This means you can have SOL-PERP candles from both Drift and Hyperliquid stored simultaneously, each with their own OHLCV data reflecting the price action on that specific venue.

When you download data, specify which venues to fetch from. The Data Explorer UI includes a venue selector that controls which venue's candles are displayed and downloaded.

### Per-venue data comparison

In the Data Explorer, you can overlay candle data from multiple venues on the same chart (overlay mode) or view them in separate panels (split mode). This is useful for spotting price dislocations and basis between venues.

---

## Hyperliquid Candle Provider

`providers/hyperliquid_candles.py` -- `HyperliquidCandleProvider`

Fetches OHLCV candles from Hyperliquid's public API. No API key required. Supports the same market symbols as Drift (e.g., `SOL-PERP`, `BTC-PERP`). Data is stored in the `candles` table with `venue='hyperliquid'`.

This provider was added in Phase 2 (multi-venue support) to enable cross-venue backtesting and funding rate arbitrage strategies.

---

## Funding Venues

The cross-venue funding provider (`providers/funding_rates.py`) collects hourly funding rates from 10 venues and normalizes them to a common schema. All are free with no API keys required:

| Venue | Source |
|---|---|
| Drift | Native provider (`DriftFundingProvider`) |
| Binance | Public API (`BinanceFundingProvider`) |
| Hyperliquid | Public API (`HyperliquidFundingProvider`) |
| OKX | Public API (`OKXFundingProvider`) |
| Bybit | Public API (`BybitFundingProvider`) |
| Gate.io | Public API (`GateioFundingProvider`) |
| Bitget | Public API (`BitgetFundingProvider`) |
| dYdX | Public API (`DydxFundingProvider`) |
| MEXC | Via CCXT (`CCXTFundingProvider`) |
| Phemex | Via CCXT (`CCXTFundingProvider`) |
| BitMEX | Via CCXT (`CCXTFundingProvider`) |

Funding data is stored in the `venue_funding_rates` DuckDB table and is accessible inside strategies via:

```python
# All venue funding for a market
ctx.get_funding_by_venue("SOL-PERP", lookback=24)
# Returns: {"drift": [(ts, rate), ...], "hyperliquid": [(ts, rate), ...], ...}

# Aggregate funding rate (primary venue)
ctx.get_funding_rate("SOL-PERP")
```

---

## API Keys

Most providers work without any credentials. Two providers require free API keys, and CCXT supports optional per-exchange credentials.

### Birdeye (`FLINT_BIRDEYE_API_KEY`)

Birdeye provides OHLCV candles for any SPL token on Solana -- not just the 48 markets Drift tracks. Get a free key at [birdeye.so](https://birdeye.so/developers) (no credit card required).

### Helius (`FLINT_HELIUS_API_KEY`)

Helius provides on-chain liquidation events and whale transfer tracking. Get a free key at [helius.dev](https://helius.dev) (no credit card required).

### CCXT (optional)

CCXT connects to 100+ CEX exchanges. Most support public candle data without keys, but authenticated endpoints require per-exchange credentials.

```bash
pip install flint[ccxt]
```

### Setting Keys

Add keys to a `.env` file in the project root (never commit this file):

```bash
FLINT_BIRDEYE_API_KEY=your_key_here
FLINT_HELIUS_API_KEY=your_key_here
```

Or set them as environment variables with the `FLINT_` prefix:

```bash
export FLINT_BIRDEYE_API_KEY=your_key_here
```

Config is loaded by `flint/config.py` via Pydantic settings -- it merges `flint.yaml`, `.env`, and environment variables. YAML keys flatten: `db.path` -> `db_path`.

Check which providers are enabled and reachable:

```bash
curl -s http://localhost:8000/api/v1/data/providers | python3 -m json.tool
```

---

## Downloading Data

### Quick Start

```bash
flint init
```

Downloads a sample set of SOL-PERP candles from Drift and runs a sanity-check backtest.

### Via the UI

The Data Explorer page has a download manager with:

- **Venue selector** -- choose Drift, Hyperliquid, or both
- **Market picker** -- select individual markets or use presets
- **Presets** -- "Starter Pack" (top 5 markets), "Everything" (all 48 Drift markets + Hyperliquid equivalents + funding from 10 venues)
- **Date range selector** -- pick start/end dates for the download

Download warnings are surfaced in the UI if any venue encounters errors during download (rate limits, connectivity issues, etc.).

### Via the CLI

```bash
flint data download --market SOL-PERP --days 180
```

### Via the API

```bash
curl -s -X POST http://localhost:8000/api/v1/data/download \
  -H "Content-Type: application/json" \
  -d '{
    "market": "SOL-PERP",
    "resolution": "1m",
    "start_ts": 1709251200,
    "end_ts": 1743465600,
    "venues": ["drift", "hyperliquid"]
  }'
```

**Parameters:**

| Field | Type | Description |
|---|---|---|
| `market` | string | Market symbol, e.g. `"SOL-PERP"`, `"BTC-PERP"` |
| `resolution` | string | Candle resolution: `"1m"`, `"5m"`, `"1h"`, `"1d"` |
| `start_ts` | int | Start time as unix timestamp (seconds) |
| `end_ts` | int | End time as unix timestamp (seconds) |
| `venues` | list[string] | Optional. Venues to download from (default: all enabled) |

### Downloading Multiple Markets

Repeat the download call for each market, or use the UI's bulk download with presets. For multi-market strategies, download all referenced markets before running a backtest.

---

## Checking Data Coverage

Before running a backtest, verify that candle data exists for your chosen date range:

```bash
curl -s "http://localhost:8000/api/v1/data/check?market=SOL-PERP&start_ts=1709251200&end_ts=1743465600" \
  | python3 -m json.tool
```

Returns a coverage summary including the number of candles available, gaps detected, and the actual date range stored. The BacktestLab UI also checks coverage before running -- if data is missing, it tells you to download it from the Data tab first.

---

## Data Freshness

Check how stale the data is for each market and table:

```bash
curl -s http://localhost:8000/api/v1/data/freshness | python3 -m json.tool
```

The response shows, per market and data type, the timestamp of the most recent record and how many hours ago it was fetched.

---

## Available and Local Markets

List all markets that can be downloaded (from Drift's 48-market catalog, Hyperliquid, and other providers):

```bash
curl -s http://localhost:8000/api/v1/data/available-markets | python3 -m json.tool
```

List markets already downloaded and stored locally in DuckDB:

```bash
curl -s http://localhost:8000/api/v1/data/markets | python3 -m json.tool
```

---

## Cross-Venue Funding Analysis

The Data Explorer lets you overlay funding rates from all 10 venues on one chart. When one venue's funding diverges from the rest, there is a potential arbitrage opportunity.

Query funding rates by venue via the API:

```bash
curl -s "http://localhost:8000/api/v1/data/funding?market=SOL-PERP&venue=drift&lookback=168" \
  | python3 -m json.tool
```

Or get the cross-market correlation matrix:

```bash
curl -s http://localhost:8000/api/v1/data/correlation | python3 -m json.tool
```

---

## Adding a Custom Provider

1. **Create the provider file**: `flint/providers/my_provider.py`

```python
from .base import DataProvider

class MyProvider(DataProvider):
    @property
    def name(self) -> str:
        return "my_provider"

    def is_available(self) -> bool:
        # Return False if a required API key is missing, etc.
        return True

    def supported_data_types(self) -> list[str]:
        return ["candles"]

    async def fetch_candles(self, market: str, start_ts: int, end_ts: int, resolution: str) -> list:
        # Fetch and return a list of Candle objects
        ...
```

2. **Register the provider**: Add it to `flint/providers/__init__.py`.

3. **Add config** (optional): Add an enable/disable entry in `flint.yaml` under the `providers` key:

```yaml
providers:
  my_provider:
    enabled: true
```

The registry (`registry.py`) reads this config and calls `is_available()` at startup. Disabled or unavailable providers are skipped silently.

---

## DuckDB Tables

All fetched data is stored in the local DuckDB file (default path configured in `flint.yaml`). The tables are:

| Table | Data |
|---|---|
| `candles` | OHLCV bars with venue column (PK: venue, market, resolution_s, ts) |
| `venue_funding_rates` | Hourly funding rates by venue |
| `oracle_prices` | Pyth oracle price snapshots |
| `orderbook_snapshots` | L2 orderbook depth |
| `pool_snapshots` | Raydium/Orca AMM pool state |
| `open_interest` | Long/short OI by market |
| `liquidations` | On-chain liquidation events |
| `whale_transfers` | Large wallet movements |
| `dex_volume` | DEX trading volume |
| `token_unlocks` | Token vesting unlock events |
| `tick_snapshots` | CLMM tick data for concentrated liquidity pools |
| `sync_metadata` | Last-fetched timestamps per source |

Live trading sessions add additional tables: `live_sessions`, `live_orders`, `live_fills`, `live_equity_history`.

Access via the `FlintStore` singleton (`app.state.store`) -- never create a new DuckDB connection directly. All store methods use `threading.Lock` for thread safety.
