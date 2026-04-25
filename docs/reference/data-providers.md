# Data Providers Reference

Catalog of the 26 provider classes Flint ships with. Source: `flint/providers/*`. Registered via `flint.providers.registry.ProviderRegistry`.

Toggle providers in `flint.yaml`:

```yaml
providers:
  drift: { enabled: true }
  birdeye: { enabled: false }
  helius: { enabled: false, api_key: "" }
```

Or via CLI: `flint data provider enable birdeye --api-key $KEY`.

---

## Candle providers

| Class | Venue | Resolutions | Auth | Notes |
|---|---|---|---|---|
| `DriftCandleProvider` | drift | 1m 5m 15m 1h 4h 1d | none | Live Drift Data API; recent windows |
| `DriftS3Provider` | drift | 1m 5m 15m 1h 4h 1d | none | Drift S3 archive; best for historical |
| `DriftDataProvider` | drift | configurable | none | Drift historical API; funding + orderbook |
| `PythCandleProvider` | pyth | 1h 1d | none | Oracle-derived candles for 20+ pairs |
| `HyperliquidCandleProvider` | hyperliquid | 1m 5m 15m 1h | none | Hyperliquid REST |
| `CCXTProvider` | 100+ CEX | 1m – 1d | none (pub) | Binance, Bybit, OKX, Kraken, etc. Requires `pip install flint-trading[ccxt]` |
| `CoinGeckoProvider` | coingecko | 1h 1d | none | BTC, ETH, SOL spot; fills gaps for non-Drift assets |
| `BirdeyeProvider` | birdeye | 1m 5m 1h 1d | `FLINT_BIRDEYE_API_KEY` | Any Solana SPL token OHLCV |
| `GeckoProvider` | coingecko-terminal | varies | none | DEX pool OHLCV for any Solana pool |

### Using a candle provider directly

```python
from flint.providers.drift_s3 import DriftS3Provider
p = DriftS3Provider()
try:
    candles = p.fetch_candles("SOL-PERP", 3600, start_ts, end_ts)
finally:
    p.close()
```

All candle providers implement:

```python
def fetch_candles(
    market: str,
    resolution_s: int,
    start_ts: int,
    end_ts: int,
    on_progress: Optional[Callable[[done, total, label], None]] = None,
) -> List[Candle]: ...

def is_available() -> bool: ...
def close() -> None: ...
```

---

## Funding rate providers

All normalize to **hourly** rates by forward-filling 8-hour venue schedules. Output rows go into `venue_funding_rates` keyed by `(venue, market, ts)`.

| Class | Venue | Auth | Symbol format |
|---|---|---|---|
| `DriftFundingProvider` | drift | none | `SOL-PERP` |
| `BinanceFundingProvider` | binance | none | `SOLUSDT` |
| `BybitFundingProvider` | bybit | none | `SOLUSDT` |
| `HyperliquidFundingProvider` | hyperliquid | none | `SOL` |
| `OKXFundingProvider` | okx | none | `SOL-USDT-SWAP` |
| `DYdXFundingProvider` | dydx | none | `SOL-USD` |
| `BitgetFundingProvider` | bitget | none | `SOLUSDT` |

### `CrossVenueFunding`

Aggregator that fans out to all enabled per-venue providers and merges into a single DataFrame / list.

---

## Other data providers

| Class | Data | Auth |
|---|---|---|
| `DriftOpenInterestProvider` | Long/short OI snapshots for Drift perps | none |
| `JupiterProvider` | DEX swap quotes, pricing | none |
| `JupiterBorrowProvider` | Borrow-rate snapshots | none |
| `JupiterBorrowCollector` | Periodic borrow-rate collector | none |
| `OrcaProvider` | Orca CLMM pool state | none |
| `RaydiumProvider` | Raydium AMM/CLMM state | none |
| `PythProvider` | Real-time oracle prices | none |
| `HeliusProvider` | Liquidation events, whale transfers, indexing | `FLINT_HELIUS_API_KEY` |
| `CCXTMarketMapper` | Maps CEX symbols to Flint format | none |

### Jupiter data gotcha

`perps-api.jup.ag` exposes **current state only** — no historical borrow-rate series, no historical notional volume, no historical OHLCV. Jupiter strategies are effectively forward-only: backfill before your collector start date doesn't exist, so backtests on Jupiter-specific data older than whatever you've collected yourself are not possible.

Workarounds:

- **Price history** — substitute Pyth oracle candles (`PythCandleProvider`) or a spot source like Birdeye.
- **Borrow rates** — run `JupiterBorrowCollector` continuously so history accumulates going forward. Dune (`FLINT_DUNE_API_KEY`) covers some backfill on select pools but is sparse.
- **Volume** — approximated from Helius Enhanced Transaction USDC transfers (collateral proxy, not notional). Recent data only on the free tier.

---

## WebSocket feeds

Live feeds for paper and live trading. All inherit `WebSocketFeed` in `flint/providers/websocket.py`.

| Class | Stream |
|---|---|
| `DriftWebSocketFeed` | Drift trade stream → `CandleAggregator` builds OHLCV |
| `HyperliquidWebSocketFeed` | Pre-built OHLCV + L2 orderbook |
| `PythWebSocketFeed` | Oracle prices |

---

## API keys

| Provider | Env var | Free tier | Sign-up |
|---|---|---|---|
| Birdeye | `FLINT_BIRDEYE_API_KEY` | yes | [birdeye.so/developers](https://birdeye.so/developers) |
| Helius | `FLINT_HELIUS_API_KEY` | yes (no CC) | [helius.dev](https://helius.dev) |
| Dune (borrow backfill) | `FLINT_DUNE_API_KEY` | yes (limited) | [dune.com](https://dune.com) |
| Tardis (CEX orderbooks) | `FLINT_TARDIS_API_KEY` | no | [tardis.dev](https://tardis.dev) |

Set in `.env` (don't commit) or as env vars. Never put keys in `flint.yaml` if the config is checked into git.

---

## Provider status

```bash
flint data provider status                             # CLI table
curl -s localhost:8000/api/v1/data/providers           # API
```

Response:

```json
{ "providers": [ { "name": "drift", "requires_api_key": false, "available": true } ] }
```

---

## Writing a custom provider

1. Subclass the right base:

```python
# flint/providers/my_provider.py
from flint.providers.registry import DataProvider
from flint.models import Candle

class MyProvider(DataProvider):
    name = "my_provider"
    data_types = ["candles"]
    requires_api_key = False

    def fetch_candles(self, market, resolution_s, start_ts, end_ts, on_progress=None):
        # ... call your API, convert to Candle dataclass
        return [Candle(ts=..., open=..., high=..., low=..., close=..., volume=...,
                       market=market, resolution_s=resolution_s, venue=self.name)]

    def is_available(self):
        return True

    def close(self): pass
```

2. Register in `flint/providers/__init__.py`:

```python
from .my_provider import MyProvider
```

3. Enable in `flint.yaml`:

```yaml
providers:
  my_provider:
    enabled: true
```

Walkthrough: [tutorials/06-custom-data-provider.md](../tutorials/06-custom-data-provider.md).

---

## Storage

All data lands in a single DuckDB file (`db_path` in config). 12 tables used by the platform:

| Table | Key | Contents |
|---|---|---|
| `candles` | `(venue, market, resolution_s, ts)` | OHLCV per venue |
| `venue_funding_rates` | `(venue, market, ts)` | Hourly funding rates |
| `oracle_prices` | `(market, ts)` | Pyth oracle snapshots |
| `orderbook_snapshots` | `(market, ts)` | L2 depth |
| `pool_snapshots` | `(pool_address, ts)` | AMM pool state |
| `open_interest` | `(market, ts)` | Long/short OI |
| `liquidations` | `tx_sig` | On-chain liquidations |
| `whale_transfers` | `tx_sig` | Large wallet movements |
| `dex_volume` | `(market, ts)` | DEX volume |
| `token_unlocks` | `(token, ts)` | Vesting unlocks |
| `tick_snapshots` | `(pool_address, ts)` | CLMM tick data |
| `sync_metadata` | `(source, key)` | Last-fetch timestamps |

Live trading adds `live_sessions`, `live_orders`, `live_fills`, `live_equity_history`.

Always access via `FlintStore` — never open a second DuckDB connection.

## See also

- [how-to/download-data.md](../how-to/download-data.md) — recipe for bulk download
- [how-to/add-api-keys.md](../how-to/add-api-keys.md) — env var / `.env` setup
- [concepts/architecture.md](../concepts/architecture.md) — data flow end-to-end
