# Hyperliquid Integration — Design Spec

> Phase 2 of ROADMAP.md (§2.1 + §2.2 + §2.3)
> Date: 2026-04-01

## Overview

Add Hyperliquid as a second live trading venue alongside Drift. Strategies deploy to either venue with zero code changes — same `ExecutionContext` interface, same safety rails, same config pattern.

### Scope

**In scope:**
- Hyperliquid REST connector with EIP-712 signing
- `LiveHyperliquidContext` implementing all 7 `LiveExecutionContext` abstract methods
- `HyperliquidWebSocketFeed` with candle, L2 orderbook, and order update channels
- Historical candle provider for backtest data
- Config additions for Hyperliquid-specific parameters
- Download pipeline integration (CLI + API)
- Testnet/mainnet toggle (testnet by default)

**Out of scope:**
- Cross-venue execution (Phase 3)
- Funding arb strategy (Phase 3)
- vAMM/fill model calibration (Phase 4)
- Withdrawals — withdrawals happen through Hyperliquid's web UI using the main wallet, not through Flint

---

## 1. Hyperliquid REST Connector

**New file:** `flint/connectors/hyperliquid.py`

Standalone async HTTP client for Hyperliquid's REST API. Handles signing, market metadata, and all read/write operations. Consumed by `LiveHyperliquidContext` and `HyperliquidCandleProvider`.

### API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /info` | Read | Positions, orders, candles, metadata, fills |
| `POST /exchange` | Write | Place orders, cancel orders (requires EIP-712 signature) |

### Network URLs

| Network | REST | WebSocket |
|---------|------|-----------|
| Testnet | `https://api.hyperliquid-testnet.xyz` | `wss://api.hyperliquid-testnet.xyz/ws` |
| Mainnet | `https://api.hyperliquid.xyz` | `wss://api.hyperliquid.xyz/ws` |

### HyperliquidClient Interface

```python
class HyperliquidClient:
    def __init__(
        self,
        private_key: str,           # Ethereum private key (hex)
        network: str = "testnet",   # "testnet" or "mainnet"
    ): ...

    # --- Exchange (write, signed) ---
    async def place_order(
        self,
        asset: int,                 # Asset index from meta
        is_buy: bool,
        size: str,                  # String for precision
        price: str,                 # String for precision
        order_type: dict,           # {"limit": {"tif": "Gtc"}} or {"trigger": {...}}
        reduce_only: bool = False,
    ) -> dict: ...

    async def cancel_order(self, asset: int, oid: int) -> dict: ...

    async def cancel_all_orders(self, asset: Optional[int] = None) -> dict: ...

    # --- Info (read, no signature) ---
    async def get_clearinghouse_state(self, address: str) -> dict: ...
    async def get_open_orders(self, address: str) -> list: ...
    async def get_user_fills(self, address: str, start_time: Optional[int] = None) -> list: ...
    async def get_candle_snapshot(self, coin: str, interval: str, start: int, end: int) -> list: ...
    async def get_meta(self) -> dict: ...
    async def get_l2_book(self, coin: str) -> dict: ...

    # --- Helpers ---
    @property
    def address(self) -> str: ...    # Derived from private key

    async def close(self) -> None: ...
```

### EIP-712 Signing

All `/exchange` requests require an EIP-712 signature. Uses `eth_account` library.

**Domain parameters:**
- Testnet: `chainId=13337`, `verifyingContract` per Hyperliquid docs
- Mainnet: `chainId=1337`, `verifyingContract` per Hyperliquid docs

Note: Exact `verifyingContract` addresses are sourced from Hyperliquid's SDK/documentation at implementation time.

**Signing flow:**
1. Build action dict (order params, cancel params, etc.)
2. Construct EIP-712 typed data with domain + action-specific types
3. Sign with `Account.sign_typed_data()` from `eth_account`
4. Attach signature + nonce to request body

### Market Mapping

Reuse existing `HYPERLIQUID_SYMBOLS` from `flint/providers/funding_rates.py`:
```python
HYPERLIQUID_SYMBOLS = {
    "SOL-PERP": "SOL",
    "BTC-PERP": "BTC",
    "ETH-PERP": "ETH",
    ...  # 17 markets
}
```

On startup, call `get_meta()` to build:
- `_coin_to_asset_index: Dict[str, int]` — maps coin name to asset index
- `_asset_info: Dict[str, dict]` — tick sizes, lot sizes, max leverage per asset
- Reverse map: `_asset_index_to_coin: Dict[int, str]`

### Authentication

Private key from `FLINT_HYPERLIQUID_PRIVATE_KEY` env var. Two options for users:

1. **API wallet key (recommended)** — generated from Hyperliquid's web UI. Can trade but cannot withdraw. Safer for bots — if compromised, attacker can't drain funds.
2. **Main wallet key** — full permissions. Not recommended for automated trading.

Flint accepts either — both are Ethereum private keys. We document the recommendation but don't restrict.

**Important:** Withdrawals are not supported through Flint. Users should deposit and withdraw funds through Hyperliquid's web UI using their main wallet. The API wallet (recommended for Flint) is trade-only by design.

---

## 2. LiveHyperliquidContext

**New file:** `flint/execution/hyperliquid_live.py`

Extends `LiveExecutionContext` — identical pattern to `LiveDriftContext`. Implements the 7 abstract methods using `HyperliquidClient`.

### Constructor

```python
class LiveHyperliquidContext(LiveExecutionContext):
    def __init__(
        self,
        strategy,
        markets: List[str],
        private_key: Optional[str] = None,  # Falls back to env var
        network: str = "testnet",
        market_order_slippage: float = 0.003,
        **kwargs,  # Passed to LiveExecutionContext
    ): ...
```

### Abstract Method Implementations

| Method | Implementation |
|--------|---------------|
| `_connect()` | Create `HyperliquidClient`, call `get_meta()` to cache market metadata (asset indices, tick/lot sizes), start WS feed |
| `_disconnect()` | Close HTTP client via `client.close()`, stop WS feed |
| `_place_order(order)` | Map Flint `Order` to Hyperliquid params, call `client.place_order()`, return `(str(oid), oid)` |
| `_cancel_order(venue_order_id)` | Call `client.cancel_order(asset, oid)` |
| `_fetch_positions()` | Call `client.get_clearinghouse_state()`, parse `assetPositions` into `List[PositionInfo]` |
| `_fetch_balance()` | Parse `marginSummary.accountValue` from clearinghouse state |
| `_poll_order_status(venue_order_id)` | Check `get_open_orders()` — if order not present, check `get_user_fills()` to distinguish filled vs cancelled |

### Order Type Mapping

| Flint Order | Hyperliquid Params |
|-------------|-------------------|
| `market_order()` | IOC limit at `mark_price * (1 ± slippage)`. Slippage configurable via `live_hyperliquid_market_order_slippage` (default 0.3%). Unfilled remainder auto-cancels. |
| `limit_order()` | Limit with `{"limit": {"tif": "Gtc"}}` |
| `stop_order()` | Trigger market: `{"trigger": {"triggerPx": price, "isMarket": true, "tpsl": "sl"}}` |

### Market Order Implementation

Hyperliquid has no native market order type. We simulate it with an aggressive IOC limit:

```
For a BUY:  price = mark_price * (1 + slippage)
For a SELL: price = mark_price * (1 - slippage)
Order type: {"limit": {"tif": "Ioc"}}
```

Mark price is fetched from the WS feed's L2 book (mid price) or via `get_clearinghouse_state()`.

### Precision Handling

Hyperliquid requires prices and sizes as strings with specific decimal places per asset. On `_connect()`, we cache `szDecimals` and price tick sizes from `get_meta()` and apply rounding before submission.

### Key Differences from LiveDriftContext

| Aspect | Drift | Hyperliquid |
|--------|-------|-------------|
| Transport | Solana on-chain tx | HTTP POST |
| Signing | Solana keypair (ed25519) | Ethereum key (secp256k1, EIP-712) |
| Order confirmation | Poll on-chain state | Instant HTTP response with oid |
| Fill notification | Poll user account | WS `orderUpdates` push (+ polling fallback) |
| Market orders | Native market order type | IOC limit with slippage |
| Retry scenarios | Tx dropped, stale oracle, RPC failure | HTTP timeout, rate limit |
| Latency | ~8s (Solana block times) | ~1s |

---

## 3. HyperliquidWebSocketFeed

**New file:** `flint/providers/hyperliquid_ws.py`

Extends `WebSocketFeed` base class. Subscribes to three channel types.

### Constructor

```python
class HyperliquidWebSocketFeed(WebSocketFeed):
    def __init__(
        self,
        markets: List[str],             # ["SOL-PERP", "BTC-PERP"]
        network: str = "testnet",
        candle_interval: str = "1m",     # Native interval for candle channel
        on_candle_close: Optional[Callable[[Candle], None]] = None,
        on_order_update: Optional[Callable[[dict], None]] = None,
        user_address: Optional[str] = None,  # For orderUpdates subscription
        store=None,
        l2_persist_interval_s: int = 60,
    ): ...
```

### Channel Subscriptions

**1. `candle` — strategy ticks**

Subscribe per market:
```json
{"method": "subscribe", "subscription": {"type": "candle", "coin": "SOL", "interval": "1m"}}
```

On candle message:
1. Parse into `Candle` dataclass with `venue="hyperliquid"`
2. Detect candle close (new candle timestamp != previous)
3. Fire `on_candle_close` callback with completed candle
4. Callback enqueues to `LiveHyperliquidContext._candle_queue` for event-driven ticking

**2. `l2Book` — orderbook state**

Subscribe per market:
```json
{"method": "subscribe", "subscription": {"type": "l2Book", "coin": "SOL"}}
```

On message:
1. Update `_orderbooks[market]` with latest bid/ask levels
2. Every `l2_persist_interval_s` seconds, persist snapshot to FlintStore's `orderbook_snapshots` table

Exposed via `get_orderbook(market) -> Optional[dict]` for pre-trade impact estimation.

**3. `orderUpdates` — fill/cancel notifications**

Subscribe for connected user:
```json
{"method": "subscribe", "subscription": {"type": "orderUpdates", "user": "0x..."}}
```

On message:
1. Parse fill/cancel/trigger events
2. Fire `on_order_update` callback
3. `LiveHyperliquidContext` uses this to update `OrderTracker` state in real-time (faster than polling)

### Abstract Method Implementations

| Method | Implementation |
|--------|---------------|
| `_connect_ws()` | `websockets.connect(url)` |
| `_subscribe(ws)` | Send subscription messages for all channels/markets |
| `_handle_message(raw)` | Route by `channel` field to candle/l2Book/orderUpdates handler |
| `_fallback_poll()` | Call `HyperliquidClient.get_candle_snapshot()` for latest candle |
| `_backfill_gap(disconnect_ts, reconnect_ts)` | Fetch missed candles via REST `get_candle_snapshot()` |

### Key Difference from DriftWebSocketFeed

No `CandleAggregator` needed. Hyperliquid sends pre-built candles via the native `candle` channel. The feed just parses and forwards them.

---

## 4. Historical Candle Provider

**New file:** `flint/providers/hyperliquid_candles.py`

Data provider for backtest candles. Follows the existing `DataProvider` pattern. Uses sync HTTP (like `HyperliquidFundingProvider`) since this is for batch downloads, not live trading.

### Interface

```python
class HyperliquidCandleProvider:
    BASE_URL = "https://api.hyperliquid.xyz/info"  # Always mainnet for historical data

    def __init__(self):
        self._client = httpx.Client(timeout=15)

    def fetch_candles(
        self,
        market: str,          # "SOL-PERP"
        start_ts: int,        # Unix epoch seconds
        end_ts: int,
        resolution: str = "1m",  # "1m", "5m", "15m", "1h", "4h", "1d"
    ) -> List[Candle]: ...

    def close(self) -> None:
        self._client.close()
```

### Implementation

1. Map `market` to Hyperliquid coin via `HYPERLIQUID_SYMBOLS`
2. Call `POST /info` with `{"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms}}`
3. Parse response into `List[Candle]` with `venue="hyperliquid"`, `source="hyperliquid"`
4. Handle pagination — Hyperliquid limits to ~5000 candles per request. Paginate by advancing `startTime` to last candle's timestamp + 1.
5. Rate limit: 0.2s sleep between pagination requests (same pattern as `HyperliquidFundingProvider`)

### Download Pipeline Integration

**Modify:** `flint/api/routes/data.py`
- `POST /api/v1/data/download` already accepts venue param — add `"hyperliquid"` handler that uses `HyperliquidCandleProvider`
- Store candles in FlintStore `candles` table via existing `upsert_candles()` method

**Modify:** `flint/providers/__init__.py` (or wherever providers are registered)
- Register `HyperliquidCandleProvider`

**CLI integration:**
- Existing `flint download` or `POST /api/v1/data/download` with `venue="hyperliquid"` should work once the provider is registered

### Market Coverage

Same 17 markets from `HYPERLIQUID_SYMBOLS`:
SOL, BTC, ETH, DOGE, AVAX, LINK, ARB, SUI, XRP, OP, INJ, TIA, SEI, WIF, JUP, RENDER, BNB

Additional markets can be added by expanding the symbol map. The `get_meta()` endpoint returns the full universe — we can offer dynamic market discovery in the future.

---

## 5. Config Additions

**Modify:** `flint/config.py`

```python
# --- Hyperliquid ---
live_hyperliquid_network: str = "testnet"
live_hyperliquid_market_order_slippage: float = 0.003   # 0.3% max slippage for IOC market orders
live_hyperliquid_l2_persist_interval_s: int = 60        # Orderbook snapshot persistence interval
```

**Env vars:**
- `FLINT_HYPERLIQUID_PRIVATE_KEY` — Ethereum private key (hex string). Recommended: use an API wallet key generated from Hyperliquid's web UI (trade-only, no withdrawal permission).

**Reused config (venue-agnostic):**
- `live_tick_mode` — `"on_candle_close"` or `"on_timer"`
- `live_candle_resolution_s` — candle interval
- `live_tick_interval_s` — timer mode polling interval
- `live_tick_markets` — which markets trigger ticks (e.g., `["hyperliquid:SOL-PERP"]`)
- `live_dry_run` — dry-run mode
- `live_kill_switch_drawdown_pct` — kill switch threshold
- `live_max_orders_per_minute` — rate limiter
- `live_per_market_position_limits` — per-market caps
- All other safety rails

---

## 6. Dependencies

**New dependency:**
- `eth_account` — EIP-712 signing for Hyperliquid exchange actions. Install via `pip install eth-account`.

**Existing dependencies (no changes):**
- `httpx` — async HTTP client (already installed)
- `websockets` — WebSocket connections (already installed)

---

## 7. ROADMAP Update

After implementation, update ROADMAP.md §2.1, §2.2, §2.3 with "Implemented" checkboxes matching the pattern used in Phase 1.

---

## 8. Testing Strategy

All tests mocked — no network calls, no API keys needed.

- **HyperliquidClient**: Mock httpx responses for each endpoint. Test EIP-712 signing produces valid structure. Test testnet vs mainnet URL routing. Test market metadata caching from `get_meta()`. Test precision string formatting.
- **LiveHyperliquidContext**: Mock HyperliquidClient. Test all 7 abstract method implementations. Test order type mapping (market→IOC limit with slippage, limit→GTC, stop→trigger). Test position parsing from clearinghouse state. Test balance extraction. Test dry-run mode works unchanged.
- **HyperliquidWebSocketFeed**: Mock websocket connection. Test candle parsing into Candle dataclass. Test L2 book state updates. Test orderUpdate routing to callback. Test reconnection and REST fallback. Test channel subscription messages.
- **HyperliquidCandleProvider**: Mock HTTP responses. Test candle parsing. Test pagination logic. Test store persistence.
- **Config**: Test new config fields load from env/yaml with defaults.
- **Integration**: End-to-end mock flow — WS candle close → tick → place order → fill via orderUpdate → position update → equity persist.
