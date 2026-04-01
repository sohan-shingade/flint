# Cross-Venue Strategies — Design Spec

> Phase 3 of ROADMAP.md (§3.1 + §3.2 + §3.3)
> Date: 2026-04-01

## Overview

Add multi-venue live execution, a cross-venue funding arb strategy, and cross-venue backtest support. Strategies hold positions on multiple venues simultaneously — Flint's differentiator for DeFi perps.

### Scope

**In scope:**
- `MultiVenueLiveContext` wrapping multiple venue-specific contexts
- Order routing by venue parameter
- Aggregated + per-venue position/equity views
- Paired leg submission with timeout and optional auto-unwind
- Configurable tick mode (primary venue or any venue)
- `FundingArbStrategy` template (delta-neutral cross-venue arb)
- Cross-venue backtest engine (`"venue:market"` composite keys)
- Per-venue fill routing and combined analytics
- Config additions for multi-venue parameters

**Out of scope:**
- Additional venue integrations beyond Drift + Hyperliquid (Phase 2 covers these)
- vAMM fill model calibration (Phase 4)
- Cross-venue risk aggregation beyond position/equity aggregation
- UI dashboard for multi-venue monitoring (Phase 5)

---

## 1. MultiVenueLiveContext

**New file:** `flint/execution/multi_venue_live.py`

Wraps multiple `LiveExecutionContext` instances (one per venue). Implements the `ExecutionContext` ABC so strategies use it identically to single-venue contexts.

### Constructor

```python
class MultiVenueLiveContext(ExecutionContext):
    def __init__(
        self,
        contexts: Dict[str, LiveExecutionContext],  # {"drift": LiveDriftContext(...), "hyperliquid": LiveHyperliquidContext(...)}
        primary_venue: str = "",                      # Default venue for order routing + account property
        tick_mode: str = "primary",                   # "primary" or "any"
        leg_timeout_s: float = 30.0,                  # Paired leg fill timeout in seconds
        auto_unwind_failed_legs: bool = False,        # If True, auto-close filled legs when other side times out
    ): ...
```

If `primary_venue` is empty, defaults to the first key in `contexts`.

### Order Routing

All order methods delegate to the correct venue's context based on the `venue` parameter:

- `market_order("SOL-PERP", LONG, 10, venue="drift")` → `self._contexts["drift"].market_order(...)`
- `market_order("SOL-PERP", LONG, 10, venue="hyperliquid")` → `self._contexts["hyperliquid"].market_order(...)`
- `market_order("SOL-PERP", LONG, 10)` or `venue="default"` → routes to `self._primary_venue`

Each venue context handles its own `OrderTracker`, risk guards, and submission independently.

### Unified Views

**Aggregated (for risk/kill switch):**
- `account` → `AccountState` with equity = sum of all venue equity, cash = sum of all venue cash, unrealized_pnl = sum of all venue unrealized
- `positions` → merged list of `PositionInfo` from all venue contexts
- `pending_orders` → merged list from all venue contexts

**Per-venue breakdown:**
- `venue_account(venue: str) -> AccountState` — single venue's equity/cash/unrealized
- `venue_positions(venue: str) -> List[PositionInfo]` — already on base class
- `total_exposure(market: str) -> float` — net size across all venues for a market
- `per_venue_pnl() -> Dict[str, float]` — unrealized PnL per venue

### Lifecycle (`run()`)

```python
async def run(self, strategy, market: str, feeds=None, fetch_candle=None) -> None:
    # 1. Connect all venue contexts in parallel
    await asyncio.gather(*[ctx.connect() for ctx in self._contexts.values()])

    # 2. Start all WebSocket feeds
    feed_tasks = []
    if feeds:
        for feed in feeds:
            feed_tasks.append(asyncio.create_task(feed.start()))

    # 3. Start order polling loops for each venue
    poll_tasks = [asyncio.create_task(ctx._poll_orders_loop()) for ctx in self._contexts.values()]

    # 4. Start equity monitor (uses aggregated account)
    monitor_task = None
    if self._equity_monitor:
        monitor_task = asyncio.create_task(self._equity_monitor.run())

    # 5. Run tick loop
    #    tick_mode="primary": only primary venue's tick_markets trigger ticks
    #    tick_mode="any": all venue feeds enqueue to shared _candle_queue
    try:
        await self._run_tick_loop(strategy, market, fetch_candle)
    finally:
        # Cleanup all tasks
        ...
```

**Tick routing:**
- All venue feeds call `self._on_ws_candle(candle)` which enqueues to the shared `_candle_queue`
- In `"primary"` mode: `_on_ws_candle` only enqueues if `candle.venue == primary_venue` (or matches `tick_markets`)
- In `"any"` mode: all candles enqueue

**After each tick:**
- Call `submit_pending_orders()` on each venue context that has pending orders
- This happens in parallel via `asyncio.gather`

### EquityMonitor Integration

The `EquityMonitor` (from Phase 1) works with `MultiVenueLiveContext` because it reads `context.account` — which returns the aggregated equity. Kill switch triggers on total portfolio drawdown, then auto-flattens across all venues by calling `cancel_all()` and `close_position()` on each venue context.

---

## 2. Paired Leg Submission

### Data Structures

```python
@dataclass
class OrderLeg:
    order_id: str           # Flint order ID (set after submission)
    venue: str              # Target venue
    market: str
    side: Side
    size: float

@dataclass
class LegGroup:
    group_id: str           # UUID
    legs: List[OrderLeg]
    status: str = "pending" # "pending", "partial", "filled", "failed", "unwound"
    created_at: int = 0
    timeout_s: float = 30.0

@dataclass
class LegGroupResult:
    group_id: str
    status: str             # "filled", "partial", "failed", "unwound"
    filled_legs: List[str]  # order_ids that filled
    failed_legs: List[str]  # order_ids that didn't fill
    unwind_order_ids: List[str]  # orders placed to unwind (if auto_unwind=True)
```

These are added to `flint/models.py` alongside existing dataclasses.

### `submit_leg_group()` Flow

```
1. Submit all legs in parallel:
   asyncio.gather(
       ctx["drift"]._place_order(leg_a),
       ctx["hyperliquid"]._place_order(leg_b),
   )

2. Wait for fills (poll each venue's order status):
   - Poll every 1s up to leg_timeout_s
   - Track which legs have filled

3. On timeout:
   a. Cancel all unfilled legs
   b. If auto_unwind_failed_legs=True AND some legs filled:
      → For each filled leg, submit a market close order on the same venue
      → Record unwind order IDs
   c. Return LegGroupResult with status

4. If all fill within timeout:
   → Return LegGroupResult(status="filled", filled_legs=[...])
```

### When to Use Leg Groups

- `FundingArbStrategy` uses `submit_leg_group()` for entry and exit
- Strategies can also just use regular `market_order(venue=...)` calls independently if pairing isn't needed
- Leg groups are optional convenience, not required

---

## 3. FundingArbStrategy

**New file:** `flint/strategy/funding_arb.py`

Delta-neutral strategy exploiting funding rate divergence between venues.

### Signal Logic

```
Every tick:
  1. Read funding rates: ctx.get_funding_by_venue(market)
  2. For each pair of venues in self.venues:
     spread = venue_a_rate - venue_b_rate
  3. Find the pair with largest absolute spread
  4. If |spread| > min_spread_bps AND spread persisted for min_spread_duration:
     → Long on the venue with lower rate (being paid)
     → Short on the venue with higher rate (paying)
     → Equal USD notional on both legs (delta neutral)
     → Submit as leg group
  5. If has position AND |spread| < exit_spread_bps:
     → Close both legs via leg group
  6. If has position AND hold_time > max_hold_hours:
     → Force close both legs
```

### Parameters (Optuna-optimizable)

```python
def parameters(self):
    return {
        "min_spread_bps": (3.0, 20.0),
        "exit_spread_bps": (0.5, 5.0),
        "max_hold_hours": (4, 72),
        "position_size_usd": (100, 10000),
        "min_spread_duration": (1, 6),       # Hours spread must persist
        "candle_resolution_s": (60, 3600),
    }
```

### Guards

- Only enters when both venues have funding data within the last 2 hours
- Tracks cumulative funding income per venue per position
- Logs spread, funding income, and position status each tick
- Uses `ctx.total_exposure(market)` to verify delta neutrality (should be ~0)

### Backtest Compatibility

Works in both live and backtest:
- **Live:** `ctx.get_funding_by_venue()` reads from WS feeds
- **Backtest:** `ctx.get_funding_by_venue()` reads from stored `venue_funding_rates` table (already populated by funding rate providers)

In backtest mode, `submit_leg_group()` isn't available (it's a live concept), so the strategy falls back to placing two independent `market_order(venue=...)` calls. The backtest engine processes both orders on the same tick.

---

## 4. Cross-Venue Backtest Engine

**Modify:** `flint/backtest/engine.py`

### Composite Key Parsing

Input candle dict uses `"venue:market"` format:
```python
candles = {
    "drift:SOL-PERP": drift_sol_candles,
    "hyperliquid:SOL-PERP": hl_sol_candles,
    "BTC-PERP": btc_candles,              # No prefix → "default" venue
}
```

On startup, the engine parses keys:
```python
def _parse_venue_market(key: str) -> Tuple[str, str]:
    if ":" in key:
        venue, market = key.split(":", 1)
        return (venue, market)
    return ("default", key)
```

### Candle Synchronization

All `(venue, market)` candle lists are aligned by timestamp:
1. Determine the primary `(venue, market)` pair (first key, or configured)
2. Iterate primary candles one by one
3. For each primary candle timestamp, advance all other pairs up to that timestamp
4. Strategy sees all venues' latest candles when `on_candle()` fires
5. Strategy accesses venue-specific candles via `ctx.get_candles("SOL-PERP")` (returns primary venue's) or uses `venue_positions` to check per-venue state

### Per-Venue Fill Routing

When processing an order with `venue="drift"`:
1. Look for candle from `"drift:SOL-PERP"` for fill price
2. Use Drift's `VenueConfig` for fees, impact, latency
3. If venue-specific candle not available, fall back to any candle for that market

When processing `venue="hyperliquid"`:
1. Use candle from `"hyperliquid:SOL-PERP"`
2. Use Hyperliquid's `VenueConfig`

### Combined Analytics

After backtest completes, `BacktestResult` includes:
```python
per_venue_pnl: Dict[str, float]              # PnL by venue
per_venue_trades: Dict[str, int]             # Trade count by venue
per_venue_funding_income: Dict[str, float]   # Funding earned/paid by venue
```

These are computed by iterating fills and grouping by `fill.venue`.

### Backward Compatibility

Existing single-venue backtests work unchanged:
- `{"SOL-PERP": candles}` → parsed as `("default", "SOL-PERP")`
- No venue prefix = default venue
- All existing tests pass without modification

---

## 5. Config Additions

**Modify:** `flint/config.py`

```python
# --- Multi-venue ---
live_multi_venue_primary: str = ""
live_multi_venue_tick_mode: str = "primary"
live_multi_venue_leg_timeout_s: float = 30.0
live_multi_venue_auto_unwind: bool = False
```

**Reused config:**
- All existing safety rails (kill switch, rate limits, per-market limits) apply to the aggregated portfolio
- Per-venue risk guards run independently within each venue context

---

## 6. Dependencies

No new dependencies. Uses existing:
- `asyncio` for parallel venue connections and leg submission
- All existing execution, backtest, and strategy infrastructure

---

## 7. ROADMAP Update

After implementation, update ROADMAP.md §3.1, §3.2, §3.3 with "Implemented" checkboxes matching the pattern used in Phases 1 and 2.

---

## 8. Testing Strategy

All tests mocked — no network calls.

- **MultiVenueLiveContext**: Mock two venue contexts (MockDrift, MockHyperliquid). Test order routing to correct venue. Test `venue="default"` routes to primary. Test aggregated `account` property (sum of venues). Test `venue_account()` per-venue. Test `positions` merges all venues. Test `total_exposure()` nets across venues. Test tick mode "primary" vs "any". Test `run()` connects all venues in parallel.
- **LegGroup lifecycle**: Test paired submission with both legs filling. Test timeout with one leg failing (cancel unfilled). Test auto-unwind (close filled leg on timeout). Test `LegGroupResult` status values.
- **FundingArbStrategy**: Test entry signal (spread above threshold). Test no entry when spread below threshold. Test exit on spread convergence. Test max hold timeout forces exit. Test delta-neutral sizing (equal notional). Test min_spread_duration guard. Test parameters() returns correct bounds.
- **Cross-venue backtest**: Test `"venue:market"` key parsing. Test backward compatibility (`"SOL-PERP"` → default venue). Test per-venue fill routing (drift order uses drift candle). Test candle sync across venues. Test combined analytics (per_venue_pnl, per_venue_trades). Test funding income attribution by venue.
- **Integration**: End-to-end backtest with FundingArbStrategy on mock two-venue funding + candle data. Verify legs placed on correct venues. Verify per-venue PnL matches expected funding income minus fees.
