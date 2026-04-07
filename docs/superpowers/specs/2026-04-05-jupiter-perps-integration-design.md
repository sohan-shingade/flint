# Jupiter Perps Integration — Design Spec

**Date**: 2026-04-05
**Status**: Draft
**Approach**: Full parallel build (data collection + live execution + backtest support)

## Context

Jupiter Perps is a pool-based perpetual futures protocol on Solana with 3 markets (SOL, ETH, wBTC). It differs fundamentally from Drift and Hyperliquid:

- **Pool-based execution**: The JLP pool is counterparty to every trade. Execution at oracle price with zero slippage (plus price impact fee).
- **Borrow fee model**: Continuous borrow fees (always positive, paid to JLP) instead of periodic funding rates (positive/negative).
- **2-step keeper execution**: Trader submits a PositionRequest on-chain, off-chain keepers fulfill it in a separate transaction.
- **No REST API, no Python SDK**: Purely on-chain program (`PERPHjGBqRHArX4DySjwM6UJHiR3sWAatqfdBS2qQJu`). Community TS client exists.
- **Limited data infrastructure**: No historical borrow rate API. Must collect from Dune Analytics or archival RPC.

**Program ID**: `PERPHjGBqRHArX4DySjwM6UJHiR3sWAatqfdBS2qQJu`
**Pool Account**: `5BUwFW4nRbftYTDMbgxykoFWqWHPzahFSNAaaaJtVKsq`

## 1. Data Collection Pipeline

Three sources feed into one DuckDB table.

### 1.1 DuckDB Table

```sql
CREATE TABLE IF NOT EXISTS jupiter_borrow_rates (
    market          VARCHAR NOT NULL,   -- 'SOL-PERP', 'ETH-PERP', 'BTC-PERP'
    ts              BIGINT  NOT NULL,   -- unix seconds
    rate_hourly     DOUBLE  NOT NULL,   -- hourly borrow rate (normalized from Dbps)
    utilization     DOUBLE  NOT NULL,   -- pool utilization ratio 0-1
    cumulative_rate DOUBLE  NOT NULL,   -- cumulative interest rate (for position cost calc)
    source          VARCHAR NOT NULL DEFAULT 'rpc',  -- 'dune', 'rpc', 'archival'
    PRIMARY KEY (market, ts)
);
```

Separate from `venue_funding_rates` because borrow rates are structurally different — always positive, no mark/index price spread, includes utilization and cumulative rate.

### 1.2 Data Model

```python
@dataclass(frozen=True)
class BorrowSnapshot:
    market: str          # 'SOL-PERP'
    ts: int              # unix seconds
    rate_hourly: float   # hourly borrow rate
    utilization: float   # 0-1
    cumulative_rate: float
    source: str = "rpc"  # 'dune', 'rpc', 'archival'
```

Added to `flint/models.py`.

### 1.3 Dune Backfill (Primary Historical Source)

New class `DuneBorrowBackfill` in `flint/providers/jupiter_borrow.py`.

- Queries Dune API with SQL that extracts `fundingRateState.hourlyFundingDbps`, `fundingRateState.cumulativeInterestRate`, and utilization from decoded Jupiter Perps custody account updates
- Requires `FLINT_DUNE_API_KEY` in config
- Backfill window: as far back as Dune has decoded Jupiter Perps data (mid-2024 onward)
- Converts Dune results to `BorrowSnapshot`, writes to DuckDB with `source='dune'`
- One-time backfill + periodic refresh (daily)

Reference Dune dashboards for query development:
- https://dune.com/jupiterexchange/jupiter-perps (official)
- https://dune.com/queries/3338148/5593343 (JLP pool/custody)
- https://dune.com/queries/3417634/5738454 (daily fees)

### 1.4 Archival RPC Fallback (Gap Filling)

New class `RpcBorrowBackfill` in the same file.

- Uses `getAccountInfo` on custody accounts at historical slots via archival RPC (Helius, Triton)
- Binary-steps through time: pick target timestamps, find nearest slot via `getBlockTime`, read custody state
- Deserializes custody account to extract `fundingRateState` fields
- Uses existing `FLINT_HELIUS_API_KEY` if available
- Writes to DuckDB with `source='archival'`
- Slow and expensive — only used to fill gaps Dune can't cover

Custody account addresses are read dynamically from the pool account's custody list at runtime (not hardcoded) — the pool account at `5BUwFW4nRbftYTDMbgxykoFWqWHPzahFSNAaaaJtVKsq` contains an ordered list of custody PDAs for SOL, ETH, wBTC, USDC, USDT.

### 1.5 Forward Collector (Live)

New class `JupiterBorrowCollector` in the same file.

- Polls the 5 custody accounts via Solana RPC every 5 minutes
- Reads `fundingRateState.hourlyFundingDbps`, `fundingRateState.cumulativeInterestRate`, and utilization (`assets.owned` vs `assets.locked`)
- Writes `BorrowSnapshot` to DuckDB with `source='rpc'`
- Runs as background task in `flint serve`, same pattern as existing data sync
- Starts automatically when `jupiter_perps.enabled: true` in config

### 1.6 Store Methods

Added to `FlintStore`:

```python
def upsert_borrow_rates(self, snapshots: List[BorrowSnapshot]) -> int
def query_borrow_rates(self, market: str, start_ts: int, end_ts: int) -> List[BorrowSnapshot]
def query_borrow_cumulative(self, market: str, ts: int) -> Optional[float]  # nearest cumulative_rate
```

All wrapped in `with self._lock:` per existing pattern.

## 2. TS Sidecar (Live Execution)

### 2.1 Architecture

A minimal Fastify server wrapping the community `jup-perps-client` library. Flint communicates with it over localhost HTTP.

```
sidecar/jupiter-perps/
  src/
    index.ts          # Fastify server, health endpoint
    routes/
      positions.ts    # GET /positions, GET /position/:market
      orders.ts       # POST /increase, POST /decrease, POST /close
      account.ts      # GET /balances, GET /collateral
    lib/
      client.ts       # Wraps jup-perps-client, manages wallet/RPC
      keeper.ts       # Polls for request fulfillment, timeout logic
      oracle.ts       # Reads Dove/Pyth oracle prices
  package.json
  tsconfig.json
  tests/
    client.test.ts
    keeper.test.ts
    routes.test.ts
```

### 2.2 HTTP API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Liveness (RPC connected, wallet loaded) |
| `/positions` | GET | All open positions for configured wallet |
| `/position/:market` | GET | Single position (entry, size, PnL, borrow accrued) |
| `/balances` | GET | Wallet token balances |
| `/increase` | POST | Submit `CreateIncreasePositionMarketRequest` |
| `/decrease` | POST | Submit `CreateDecreasePositionMarketRequest` |
| `/close` | POST | Close full position (decrease to zero) |
| `/oracle/:market` | GET | Current oracle price + borrow rate |

Mutation endpoints return `{request_id, tx_signature, status: "pending"}`. Caller polls `/position/:market` for keeper fulfillment confirmation.

### 2.3 Keeper-Aware Execution

1. Python calls `POST /increase` → sidecar builds & sends Solana transaction → returns pending status
2. Sidecar internally polls for keeper fulfillment (PositionRequest consumed, position updated)
3. Python polls `GET /position/:market` for confirmation
4. Timeout: 60s. If unfulfilled, sidecar returns `status: "expired"` — request can be cancelled

Maps to Flint's existing `OrderTracker` pattern.

### 2.4 Lifecycle Management

New file: `flint/execution/jupiter_sidecar.py`

`JupiterSidecar` class manages the Node subprocess:
- Auto-started by `flint serve` when Jupiter venue is enabled
- Health check via `GET /health` every 10s
- Auto-restart on crash (max 3 retries, then mark venue unavailable)
- Graceful shutdown on `flint serve` exit (SIGTERM)
- Sidecar port configurable in `flint.yaml` (default: `8401`)
- Requires `node >= 18` on PATH — checked at startup with clear error if missing

### 2.5 Configuration

```yaml
# flint.yaml
jupiter_perps:
  enabled: true
  sidecar_port: 8401
  rpc_url: "https://api.mainnet-beta.solana.com"
  wallet_path: "~/.config/solana/id.json"
```

Reads Solana keypair from file — no private key in env vars.

## 3. Live Execution Context

### 3.1 LiveJupiterContext

New file: `flint/execution/jupiter_live.py`

Implements `LiveExecutionContext` (7 abstract methods) by calling the TS sidecar over HTTP:

| Method | Sidecar Call | Notes |
|--------|-------------|-------|
| `market_order()` | `POST /increase` or `/decrease` | Returns pending order, async fulfillment |
| `limit_order()` | `POST /increase` (via `InstantCreateLimitOrder`) | Jupiter supports limit orders |
| `cancel_order()` | Cancel unfulfilled PositionRequest | Only works pre-fulfillment |
| `get_positions()` | `GET /positions` | Returns `List[PositionInfo]` |
| `get_balances()` | `GET /balances` | Returns `AccountState` |
| `get_orderbook()` | `GET /oracle/:market` | Synthetic single-level book at oracle price |
| `get_mark_price()` | `GET /oracle/:market` | Oracle price |

Key differences from Drift/Hyperliquid:
- **No orderbook**: Returns synthetic single-level "book" at oracle price ± impact fee
- **Async fills**: `market_order` returns immediately with pending order. `OrderTracker` polls for fulfillment
- **Collateral constraints**: Longs require base asset (SOL for SOL-PERP), shorts require USDC/USDT. Validated before submission.

### 3.2 Borrow Rate in Strategy API

Added to `ExecutionContext` ABC as default methods (return `None`/`[]`):

```python
def get_borrow_rate(self, market: str = None, venue: str = None) -> Optional[float]:
    """Current hourly borrow rate. Returns None for venues using funding rates."""

def get_borrow_rates(self, market: str = None, venue: str = None, lookback: int = 24) -> List[Tuple[int, float]]:
    """Historical borrow rates as [(ts, rate), ...]. Empty for non-borrow venues."""
```

**LiveJupiterContext**: Reads latest rate from DuckDB `jupiter_borrow_rates` or sidecar.
**BacktestContext**: Replays rates from `jupiter_borrow_rates` at each bar timestamp.
**Drift/Hyperliquid**: Return `None`/`[]`.

Enables cross-venue cost comparison in strategies:

```python
def on_candle(self, ctx):
    borrow = ctx.get_borrow_rate("SOL-PERP", venue="jupiter")
    funding = ctx.get_funding_rate("SOL-PERP")
    # Route to cheapest venue
```

### 3.3 JupiterTxCostModel

New file: `flint/execution/jupiter_costs.py`

```python
@dataclass
class JupiterCostEstimate:
    open_fee: float        # 0.06% of position size
    close_fee: float       # 0.06% of position size
    price_impact: float    # f(position_size, pool_depth)
    borrow_cost: float     # cumulative rate delta × size
    total: float

class JupiterTxCostModel:
    def estimate_round_trip(market, size, hold_hours) -> JupiterCostEstimate
    def estimate_borrow(market, size, hours) -> float
```

Handles open/close fee estimation (distinct from `HoldingCostModel` which handles ongoing borrow accrual). The backtest engine uses `JupiterTxCostModel` for entry/exit costs and `BorrowCostModel` for holding costs.

### 3.4 VenueConfig Preset

Added to `venue_config.py`:

```python
JUPITER = VenueConfig(
    name="jupiter",
    taker_fee_bps=6.0,          # 0.06% flat
    maker_fee_bps=6.0,          # no maker/taker distinction
    initial_margin=0.01,        # 1% = 100x max leverage
    maintenance_margin=0.002,   # 0.2% = 500x liquidation threshold
    max_leverage=100.0,
    liquidation_penalty=0.0,    # no separate penalty
    impact_coefficient=0.03,    # higher due to pool-based model
    base_latency_s=12.0,        # 2-step keeper model
    latency_jitter_s=8.0,       # keeper timing varies
)
```

### 3.5 MultiVenueLiveContext

No refactoring needed. Register `LiveJupiterContext` as handler for `venue="jupiter"`. Capital allocation via existing `VenueAllocator`.

## 4. Backtest Support

### 4.1 Continuous Borrow Accrual

Jupiter borrow fees are continuous, not periodic. They accrue every second via `cumulativeInterestRate`. The backtest engine handles this differently from funding rates:

```
For each open Jupiter position:
  At position open:
    record cumulative_rate_at_entry (from jupiter_borrow_rates table)

  At each bar (for PnL/liquidation):
    cumulative_rate_now = lookup from jupiter_borrow_rates
    accrued_borrow = (cumulative_rate_now - cumulative_rate_at_entry) × position_size_usd
    unrealized_pnl -= accrued_borrow  (affects margin/liquidation checks)

  At position close:
    final_borrow = (cumulative_rate_at_close - cumulative_rate_at_entry) × position_size_usd
    cash -= final_borrow  (realized cost, deducted once)
```

No per-bar cash deduction. Borrow cost is realized on close, matching the real protocol. Position modifications (partial close, increase) snapshot a new `cumulative_rate_at_entry` for the modified portion.

### 4.2 HoldingCostModel Abstraction

New file: `flint/execution/holding_cost.py` (~40 lines)

```python
class HoldingCostModel(ABC):
    def cost_at_bar(self, position, bar_ts) -> float:
        """Unrealized holding cost at this bar for margin checks."""
    def cost_at_close(self, position, close_ts) -> float:
        """Realized holding cost on position close."""

class FundingCostModel(HoldingCostModel):
    """Drift/Hyperliquid: periodic funding rate, can be +/-."""

class BorrowCostModel(HoldingCostModel):
    """Jupiter: continuous borrow fee, always positive, based on cumulative rate."""
```

Backtest engine calls `cost_model.cost_at_bar()` uniformly per venue. Venue config maps to the right cost model. This is the only structural refactor.

### 4.3 BacktestResult Extension

New fields:
```python
jupiter_borrow_paid: float = 0.0       # total borrow fees across all Jupiter positions
borrow_payments: List[dict] = []        # [{ts, market, rate, cost, position_size}, ...]
```

Appear in API response and UI alongside `funding_paid`.

### 4.4 Price Data

No new candle provider needed. Jupiter executes at oracle prices — Pyth oracle data already in Flint is the correct proxy (within 1-5 bps). `JupiterTxCostModel` adds price impact fee on top.

### 4.5 Backtest Request

Existing `POST /api/v1/backtest/run` handles Jupiter via `capital_allocation`:

```json
{
  "markets": ["SOL-PERP"],
  "capital_allocation": {"drift": 5000, "jupiter": 5000},
  "margin_tracking": true
}
```

No new API endpoints for backtesting.

## 5. Refactoring Summary

Almost entirely extensions to existing interfaces, not refactors.

### Modified Files

| File | Change | Type |
|------|--------|------|
| `flint/execution/context.py` | Add `get_borrow_rate()`, `get_borrow_rates()` to ABC (default `None`/`[]`) | Extension |
| `flint/execution/backtest_context.py` | Add `_borrow_snapshots`, cumulative rate tracking per Jupiter position | Extension |
| `flint/execution/venue_config.py` | Add `JUPITER` preset | Addition |
| `flint/backtest/engine.py` | Add borrow accrual branch alongside funding rate branch | Extension |
| `flint/store.py` | Add `jupiter_borrow_rates` table, upsert/query methods | Extension |
| `flint/models.py` | Add `BorrowSnapshot` dataclass | Addition |
| `flint/config.py` | Add `jupiter_perps` section | Extension |
| `flint/api/routes/data.py` | Extend `/funding` for Jupiter, add `/borrow-rates` endpoint | Extension |
| `flint/providers/__init__.py` | Register `JupiterBorrowProvider` | Addition |

### New Files

| File | Purpose |
|------|---------|
| `flint/providers/jupiter_borrow.py` | Dune backfill, archival RPC fallback, forward collector |
| `flint/execution/jupiter_live.py` | `LiveJupiterContext` |
| `flint/execution/jupiter_costs.py` | `JupiterTxCostModel`, `JupiterCostEstimate` |
| `flint/execution/jupiter_sidecar.py` | Sidecar subprocess manager |
| `flint/execution/holding_cost.py` | `HoldingCostModel` abstraction |
| `sidecar/jupiter-perps/` | TS Fastify server (~6 source files + tests) |

### New API Endpoint

```
GET /api/v1/data/borrow-rates?market=SOL-PERP&start_ts=...&end_ts=...
```

### UI Changes

- Data Explorer: Add "Jupiter Borrow Rates" data type (time series + utilization overlay)
- Backtest Results: Show `jupiter_borrow_paid` in cost breakdown
- Venue selectors: Add "Jupiter" wherever Drift/Hyperliquid appear

## 6. Testing

### 6.1 Python Unit Tests (mocked)

| File | Coverage |
|------|----------|
| `tests/test_jupiter_borrow.py` | Collector parsing, Dune response handling, rate normalization |
| `tests/test_jupiter_costs.py` | Cost estimates, `BorrowCostModel` accrual |
| `tests/test_jupiter_sidecar.py` | Subprocess lifecycle, health check, restart logic |
| `tests/test_jupiter_live.py` | All 7 context methods + `get_borrow_rate`, keeper timeout |
| `tests/test_jupiter_backtest.py` | Continuous borrow accrual, cumulative rate tracking, liquidation with borrow |
| `tests/test_holding_cost.py` | `FundingCostModel` vs `BorrowCostModel`, negative funding, always-positive borrow |
| `tests/test_jupiter_venue_config.py` | Preset values, YAML override |

### 6.2 Integration Tests

| Test | Validates |
|------|-----------|
| Multi-venue backtest (Jupiter + Drift) | Funding applied to Drift, borrow accrual to Jupiter, costs tracked separately |
| Backfill → backtest pipeline | Dune mock → DuckDB → engine reads cumulative rates |
| `get_borrow_rate()` / `get_funding_rate()` | Correct per venue, `None` for wrong type |

### 6.3 TS Sidecar Tests

```
sidecar/jupiter-perps/tests/
  client.test.ts       # Transaction building from IDL
  keeper.test.ts       # Fulfillment polling, timeout
  routes.test.ts       # HTTP endpoint shapes
```

Run with `npm test`. Mocked Solana RPC.

### 6.4 Not Tested

- Actual Solana transactions (manual testnet validation)
- Dune query correctness (validated once, then mocked)
- Real keeper timing (tested via timeout logic)

## 7. Configuration

```yaml
# flint.yaml additions
jupiter_perps:
  enabled: true
  sidecar_port: 8401
  rpc_url: "https://api.mainnet-beta.solana.com"
  wallet_path: "~/.config/solana/id.json"

# Environment variables
FLINT_DUNE_API_KEY=dune_xxx        # for borrow rate backfill
FLINT_JUPITER_PERPS_ENABLED=true   # alternative to YAML
```

## 8. External Dependencies

| Dependency | Purpose | Required? |
|------------|---------|-----------|
| `node >= 18` | Run TS sidecar | Yes (for live execution) |
| `jup-perps-client` (npm) | Build Jupiter Perps transactions | Yes (sidecar) |
| Dune API key | Historical borrow rate backfill | Optional (forward collection works without) |
| Archival Solana RPC | Gap-fill historical data | Optional (Helius key already supported) |
| Solana keypair file | Sign transactions | Yes (for live execution) |
