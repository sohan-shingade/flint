# Data Provider Guide

Flint ships with 15 data providers covering Solana on-chain data, CEX candles, oracle prices, DEX pools, and cross-venue funding rates from 7 venues. All providers are managed by `flint/providers/registry.py`.

---

## Provider Overview

| Provider | File | Auth | Data |
|---|---|---|---|
| Drift Data API | `providers/drift_candles.py` | None | OHLCV candles (48 perp markets) |
| Drift S3 | `providers/drift_s3.py` | None | Historical trade records, funding |
| Drift OI | `providers/open_interest.py` | None | Open interest (long/short) |
| Drift Funding | `providers/drift_api.py` | None | Funding rates, L2/L3 orderbook |
| Hyperliquid Candles | `providers/hyperliquid_candles.py` | None | OHLCV candles for Hyperliquid perps |
| Birdeye | `providers/birdeye.py` | `FLINT_BIRDEYE_API_KEY` | Any Solana SPL token OHLCV |
| Helius | `providers/helius.py` | `FLINT_HELIUS_API_KEY` | Liquidations, whale transfers |
| Pyth | `providers/pyth.py` | None | Oracle prices (20 pairs) |
| Raydium | `providers/raydium.py` | None | AMM/CLMM pool data |
| Orca | `providers/orca.py` | None | Whirlpool pool data |
| GeckoTerminal | `providers/gecko.py` | None | DEX pool OHLCV |
| Jupiter | `providers/jupiter.py` | None | Swap quotes |
| CoinGecko | `providers/coingecko.py` | None | Spot candles (BTC, ETH, etc.) |
| CCXT | `providers/ccxt_provider.py` | None (bundled) | 100+ CEX exchanges, volume data |
| Cross-venue funding | `providers/funding_rates.py` | None | 7 venues normalized to 1h |

All Drift, Pyth, Raydium, Orca, GeckoTerminal, Jupiter, CoinGecko, and CCXT providers are free and require no API keys. Only Birdeye and Helius need keys (both free tier, no credit card).

---

## Funding Venues

The cross-venue funding provider (`providers/funding_rates.py`) collects funding rates from 7 perpetual exchanges and normalizes them to a common hourly schema. All are free with no API keys.

| Venue | Source | Symbol Format |
|---|---|---|
| Drift | `DriftFundingProvider` (native) | SOL-PERP |
| Hyperliquid | Public API (`api.hyperliquid.xyz`) | SOL |
| OKX | Public API | SOL-USDT-SWAP |
| Bybit | Public API | SOLUSDT |
| dYdX | Public API | SOL-USD |
| Gate.io | Public API | SOL_USDT |
| Bitget | Public API | SOLUSDT |

All exchanges report funding at 8-hour intervals. Flint forward-fills these to hourly resolution so strategies can react to funding changes within each payment window rather than only at settlement times.

Funding data is stored in the `venue_funding_rates` DuckDB table. Access from strategy code:

```python
# All venue funding for a market (last 24 hours)
ctx.get_funding_by_venue("SOL-PERP", lookback=24)
# Returns: {"drift": [(ts, rate), ...], "hyperliquid": [(ts, rate), ...], ...}

# Aggregate funding rate for the primary venue
ctx.get_funding_rate("SOL-PERP")
```

---

## API Keys

### Birdeye (`FLINT_BIRDEYE_API_KEY`)

Birdeye provides OHLCV candles for any SPL token on Solana, not just the 48 markets Drift tracks. Get a free key at [birdeye.so](https://birdeye.so/developers) (no credit card required).

### Helius (`FLINT_HELIUS_API_KEY`)

Helius provides on-chain liquidation events and whale transfer tracking. Get a free key at [helius.dev](https://helius.dev) (no credit card required).

### CCXT (optional)

CCXT connects to 100+ CEX exchanges. Most support public candle data without keys. Authenticated endpoints require per-exchange credentials.

### Setting Keys

Add keys to a `.env` file in the project root (never commit this file):

```bash
FLINT_BIRDEYE_API_KEY=your_key_here
FLINT_HELIUS_API_KEY=your_key_here
```

Or export as environment variables with the `FLINT_` prefix:

```bash
export FLINT_BIRDEYE_API_KEY=your_key_here
```

Config is loaded by `flint/config.py` via Pydantic settings. It merges `flint.yaml`, `.env`, and environment variables. YAML keys flatten: `db.path` becomes `db_path`.

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

Downloads a sample set of SOL-PERP candles from Drift and runs a sanity-check backtest. This is the fastest way to verify your installation.

### Via the UI

The Data Explorer page has a download manager with:

- **Venue selector** -- choose Drift, Hyperliquid, or both
- **Market picker** -- select individual markets or use presets
- **Presets** -- "Starter Pack" (top 5 markets), "Everything" (all 48 Drift markets + Hyperliquid equivalents + funding from all venues)
- **Date range selector** -- pick start/end dates

Download warnings are surfaced in the UI if any venue encounters errors (rate limits, 503/403 responses, connectivity issues).

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

For multi-market strategies, download all referenced markets before running a backtest. The UI's bulk download with presets handles this automatically.

---

## Checking Data Coverage

Before running a backtest, verify that candle data exists for your chosen date range:

```bash
curl -s "http://localhost:8000/api/v1/data/check?market=SOL-PERP&start_ts=1709251200&end_ts=1743465600" \
  | python3 -m json.tool
```

Returns a coverage summary including the number of candles available, gaps detected, and the actual date range stored. The BacktestLab UI also checks coverage before running and prompts you to download missing data from the Data tab.

---

## Data Freshness

Check how stale the data is for each market and table:

```bash
curl -s http://localhost:8000/api/v1/data/freshness | python3 -m json.tool
```

The response shows, per market and data type, the timestamp of the most recent record and how many hours ago it was fetched.

---

## Available and Local Markets

List all markets that can be downloaded (Drift's 48 perp markets, Drift spot, Hyperliquid, CoinGecko):

```bash
curl -s http://localhost:8000/api/v1/data/available-markets | python3 -m json.tool
```

List markets already downloaded and stored locally in DuckDB:

```bash
curl -s http://localhost:8000/api/v1/data/markets | python3 -m json.tool
```

---

## Multi-Venue Candle Storage

Candle data is stored per-venue in DuckDB. The `candles` table uses a composite primary key:

```sql
PRIMARY KEY (venue, market, resolution_s, ts)
```

This means you can store SOL-PERP candles from both Drift and Hyperliquid simultaneously, each reflecting the price action on that specific venue. In the Data Explorer, you can overlay candle data from multiple venues on the same chart (overlay mode) or view them in separate panels (split mode) to spot price dislocations.

---

## Cross-Venue Funding Analysis

The Data Explorer lets you overlay funding rates from all venues on one chart. When one venue's funding diverges from the rest, there is a potential arbitrage opportunity.

Query funding rates by venue:

```bash
curl -s "http://localhost:8000/api/v1/data/funding?market=SOL-PERP&venue=drift&lookback=168" \
  | python3 -m json.tool
```

Get the cross-market correlation matrix:

```bash
curl -s http://localhost:8000/api/v1/data/correlation | python3 -m json.tool
```

---

## Jupiter Perps Limitations

Jupiter Perps has no historical borrow rate or volume API. Historical backfill is not currently available:

- **Borrow rates**: Jupiter's `perps-api.jup.ag` provides current rates only. Forward collection via `JupiterBorrowCollector` works but only accumulates going forward.
- **Volume**: Approximated from Helius Enhanced Transaction USDC transfers (collateral proxy, not notional). Limited to recent data on the free tier.
- **No historical OHLCV**: Jupiter Perps has no candle endpoint. Use Pyth oracle prices instead.

Strategies that depend on Jupiter Perps borrow rate history or volume should not be backtested beyond the data that has been forward-collected.

---

## Orca and Raydium (Spot DEXes)

Orca and Raydium are spot DEXes. They have no funding rates (that is a perpetuals concept). Available data:

- **Current pool data**: TVL, reserves, fee rates -- via native APIs
- **Historical OHLCV + volume**: Via GeckoTerminal (free, no key) for any Solana pool
- **Tick-level liquidity**: Orca Whirlpools via `OrcaTickFetcher` (on-chain RPC)

---

## Adding a Custom Provider

1. **Create the provider file**: `flint/providers/my_provider.py`

```python
from .base import CandleProvider

class MyProvider(CandleProvider):
    def fetch_candles(
        self,
        market: str,
        resolution_s: int,
        start_ts: int,
        end_ts: int,
    ) -> list:
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

The registry (`registry.py`) reads this config and checks provider availability at startup. Disabled or unavailable providers are skipped silently.

---

## DuckDB Tables

All fetched data is stored in the local DuckDB file (path configured in `flint.yaml`). The 12 tables are:

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

Access data via the `FlintStore` singleton (`app.state.store`). Never create a new DuckDB connection directly. All store methods use `threading.Lock` for thread safety.
