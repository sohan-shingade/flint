# Execution Engine Upgrade v0.3 — Spec & Sprint Plan

## Overview

Four features that transform Flint's backtest engine from single-venue candle-based simulation to a multi-venue, orderbook-aware, margin-tracked execution environment. Required for realistic backtesting of cross-venue strategies like the funding dislocation arb.

**Build order** (each depends on the previous):
1. Orderbook Depth / Impact Filtering
2. Multi-Venue Simultaneous Positions
3. Margin / Liquidation Tracking
4. Capital Fragmentation

**Note:** Spot + perp cross-market execution is already implemented (v0.2 session).

---

## Feature 1: Orderbook Depth / Impact Filtering

### What it does
Strategies can query orderbook state. Fill models can walk the book for realistic execution prices and reject orders that exceed available liquidity.

### What exists today
- `orderbook_snapshots` table: market, ts, bid_prices[], bid_sizes[], ask_prices[], ask_sizes[] (10 levels)
- `DriftDataProvider.fetch_orderbook()` collects every 5 min
- `OrderbookSnapshot` model with bids/asks as `Tuple[OrderbookLevel, ...]`
- `SlippageFill` model (flat bps slippage, not book-aware)
- No `query_orderbook()` method on FlintStore
- No `ctx.get_orderbook()` on ExecutionContext

### Data needed
- Already collected from Drift. Consider adding collection from Hyperliquid (public L2 API).
- Current 5-min interval is fine for hourly strategies. For 1m/5m strategies, would want 1-min collection.

### Schema changes
- Add `venue VARCHAR DEFAULT 'drift'` column to `orderbook_snapshots` (for multi-venue books later)
- No new tables

### Files to change

| File | Change | Lines |
|------|--------|-------|
| `flint/store.py` | Add `query_orderbook_snapshots(market, start_ts, end_ts)` | ~25 |
| `flint/execution/context.py` | Add `get_orderbook(market) -> OrderbookSnapshot`, `get_impact_price(market, side, size) -> float` | ~20 |
| `flint/execution/backtest_context.py` | Store orderbook history, implement `get_orderbook()`, `get_impact_price()` | ~60 |
| `flint/execution/fill_models.py` | New `OrderbookFillModel` class | ~80 |
| `flint/backtest/engine.py` | Load orderbook data, feed to context per-candle (cursor like funding) | ~30 |
| `flint/api/routes/backtest.py` | Query orderbook snapshots from DB, pass to engine | ~15 |
| `tests/test_orderbook_fill.py` | New test file | ~120 |

### OrderbookFillModel design

```python
class OrderbookFillModel(FillModel):
    """Walk the orderbook to compute realistic fill prices.

    For a market buy of 10 SOL:
    - Ask level 1: 100.05 x 5 SOL → fill 5 @ 100.05
    - Ask level 2: 100.10 x 8 SOL → fill 5 @ 100.10
    - Avg fill: 100.075

    If order size > total book depth: partial fill (fill what's available).
    Falls back to SlippageFill if no orderbook data at this timestamp.
    """

    def __init__(self, fallback_slippage_bps: float = 5.0):
        self._fallback = SlippageFill(fallback_slippage_bps)

    def fill_market(self, order, candle, orderbook=None):
        if orderbook is None:
            return self._fallback.fill_market(order, candle)
        return self._walk_book(order, orderbook)

    def _walk_book(self, order, book):
        levels = book.asks if order.side == Side.LONG else book.bids
        remaining = order.size
        total_cost = 0.0
        filled = 0.0
        for level in levels:
            take = min(remaining, level.size)
            total_cost += take * level.price
            filled += take
            remaining -= take
            if remaining <= 0:
                break
        if filled == 0:
            return None
        avg_price = total_cost / filled
        return Fill(market=order.market, side=order.side,
                    price=avg_price, size=filled, ...)
```

### Impact price helper

```python
def get_impact_price(self, market, side, size) -> float:
    """What average price would I get filling `size` right now?

    Strategies use this to check execution cost before placing orders:
        impact = ctx.get_impact_price("SOL-PERP", Side.LONG, 100)
        mid = ctx.current_candle.close
        slippage_bps = (impact - mid) / mid * 10000
        if slippage_bps > 10:
            ctx.log("Too much slippage, skipping")
            return Signal.HOLD
    """
```

### Sprint tasks

```
Task 1.1: Store query method                          [30 min]
  - Add query_orderbook_snapshots() to FlintStore
  - Returns List[OrderbookSnapshot] for market/time range
  - Test: query returns correct shape

Task 1.2: Context plumbing                            [45 min]
  - Add get_orderbook(), get_impact_price() to ExecutionContext ABC
  - Implement in BacktestContext with orderbook history dict
  - Add add_orderbook_snapshot() called by engine
  - Test: context stores and returns snapshots correctly

Task 1.3: Engine integration                          [30 min]
  - Load orderbook snapshots from store in backtest API route
  - Pass to BacktestEngine, cursor-advance like funding rates
  - Test: engine feeds orderbook data to context

Task 1.4: OrderbookFillModel                          [60 min]
  - Implement book-walking fill logic
  - Handle partial fills (order > book depth)
  - Handle empty book (fallback to slippage)
  - Test: fill prices match expected walk-the-book math

Task 1.5: Integration test                            [30 min]
  - End-to-end: strategy checks impact → places order → fills at book price
  - Compare: same strategy with ClosePriceFill vs OrderbookFillModel
  - Verify PnL differs realistically

Total: ~3.5 hours
```

---

## Feature 2: Multi-Venue Simultaneous Positions

### What it does
Hold positions on multiple venues simultaneously. Short SOL-PERP on Drift while long SOL-PERP on Hyperliquid. Each venue has its own fee structure.

### What exists today
- Positions keyed by `market: str` only — `Dict[str, _Position]`
- `Order`, `Fill` models have no `venue` field
- Fee models are venue-agnostic (one fee model per engine run)
- Cross-venue funding rates exist with `source` field

### Data needed
- Per-venue candle data OR use same candles with per-venue mark price adjustments
- Per-venue fee schedules (already partially defined in `drift_sim.py`)
- Decision: for Layer 1 research, using same candles + different fees per venue is acceptable. Mark prices from funding snapshots give venue-specific pricing.

### Schema changes
- No DuckDB changes (positions are in-memory during backtest)
- Config addition in `flint.yaml`:

```yaml
venues:
  drift:
    taker_fee_bps: 10
    maker_fee_bps: -2
    initial_margin: 0.10
    maintenance_margin: 0.05
    max_leverage: 10
  hyperliquid:
    taker_fee_bps: 3.5
    maker_fee_bps: 1
    initial_margin: 0.05
    maintenance_margin: 0.025
    max_leverage: 20
  binance:
    taker_fee_bps: 4.5
    maker_fee_bps: 2
    initial_margin: 0.02
    maintenance_margin: 0.01
    max_leverage: 50
```

### Files to change

| File | Change | Lines |
|------|--------|-------|
| `flint/models.py` | Add `venue: str = "default"` to `Order`, `Fill`, `PositionInfo` | ~10 |
| `flint/execution/backtest_context.py` | Change position key from `market` to `(venue, market)`. Add venue to order/fill creation. | ~80 |
| `flint/execution/context.py` | Add `venue` param to `market_order()`, `limit_order()`, etc. Add `venue_positions(venue)` | ~30 |
| `flint/execution/fee_models.py` | New `MultiVenueFeeModel` that dispatches to per-venue fee models | ~40 |
| `flint/execution/venue_config.py` | **New file.** `VenueConfig` dataclass, `load_venue_configs()` from YAML | ~60 |
| `flint/backtest/engine.py` | Pass venue configs to context. Venue-aware position closing at end. | ~20 |
| `tests/test_multi_venue.py` | New test file | ~150 |

### Key design decisions

**Position key change:**
```python
# Before:
self._positions: Dict[str, _Position]  # "SOL-PERP" -> Position

# After:
self._positions: Dict[Tuple[str, str], _Position]  # ("drift", "SOL-PERP") -> Position
```

**Backward compatibility:** When `venue` is not specified, default to `"default"`. All existing strategies and tests continue to work — they just use `venue="default"` implicitly.

**Strategy API:**
```python
# New: venue-aware orders
ctx.market_order("SOL-PERP", Side.SHORT, 10, venue="drift")
ctx.market_order("SOL-PERP", Side.LONG, 10, venue="hyperliquid")

# Old: still works (venue="default")
ctx.market_order("SOL-PERP", Side.LONG, 10)

# Query positions per venue
drift_pos = ctx.position("SOL-PERP", venue="drift")
all_positions = ctx.positions  # includes all venues
```

### Sprint tasks

```
Task 2.1: VenueConfig                                 [30 min]
  - Create flint/execution/venue_config.py
  - VenueConfig dataclass (fees, margin, leverage)
  - load_venue_configs() from flint.yaml
  - Hardcode defaults for Drift, Hyperliquid, Binance, OKX, Bybit
  - Test: configs load correctly

Task 2.2: Model changes                               [30 min]
  - Add venue field to Order, Fill, PositionInfo (default="default")
  - Add venue to _Position
  - Test: existing tests still pass with default venue

Task 2.3: Context refactor                             [90 min]
  - Change position dict key to (venue, market)
  - Add venue param to all order methods (default="default")
  - Add venue_positions(venue), position(market, venue) helpers
  - _apply_fill uses venue from fill
  - close_all_positions iterates all venue+market combos
  - Test: multi-venue positions tracked independently

Task 2.4: MultiVenueFeeModel                          [30 min]
  - Dispatches compute_fee() to venue-specific fee model
  - Falls back to FlatFeeModel if venue not configured
  - Test: different fees for same fill on different venues

Task 2.5: Integration test                             [45 min]
  - Strategy shorts SOL-PERP on "drift", longs on "hyperliquid"
  - Verify independent P&L tracking per venue
  - Verify venue-specific fees applied correctly
  - Verify close_all closes both venues

Total: ~4 hours
```

---

## Feature 3: Margin / Liquidation Tracking

### What it does
Enforce margin requirements per position. Track leverage. Compute liquidation prices. Force-liquidate positions when mark price crosses liquidation threshold.

### What exists today
- `compute_liquidation_price()` in `flint/mev/liquidation.py` — formula exists
- `DriftMarketConfig` with `maintenance_margin=0.05`, `initial_margin=0.10`
- `AccountState.margin_used` field (always 0)
- `LiquidationScanner` class (MEV analysis, not backtest)
- No margin checks on order placement
- No liquidation events during backtest

### Data needed
- Per-venue margin rules (from VenueConfig, Feature 2)
- No new external data — margin is computed from position state + venue config

### Schema changes
- None for DuckDB
- Add fields to `_Position`:

```python
class _Position:
    # existing fields...
    leverage: float = 1.0
    margin_allocated: float = 0.0   # collateral backing this position
    liquidation_price: float = 0.0  # computed on entry, updated on DCA
```

- Extend `AccountState`:

```python
@dataclass
class AccountState:
    equity: float
    cash: float
    unrealized_pnl: float = 0.0
    margin_used: float = 0.0
    free_margin: float = 0.0        # NEW
    margin_ratio: float = 1.0       # NEW: equity / margin_used
    leverage: float = 0.0           # NEW: total notional / equity
```

- New result tracking:

```python
@dataclass
class LiquidationEvent:
    market: str
    venue: str
    ts: int
    side: str        # "long" or "short"
    size: float
    entry_price: float
    liq_price: float
    loss: float      # realized loss including liq penalty
```

### Files to change

| File | Change | Lines |
|------|--------|-------|
| `flint/execution/margin.py` | **New file.** `MarginEngine` class | ~120 |
| `flint/execution/backtest_context.py` | Integrate margin checks into order flow. Add liquidation checking per candle. | ~80 |
| `flint/models.py` | Extend AccountState, add LiquidationEvent | ~20 |
| `flint/backtest/engine.py` | Call margin.check_liquidations() each bar. Track liquidation events in result. | ~30 |
| `tests/test_margin.py` | New test file | ~180 |

### MarginEngine design

```python
class MarginEngine:
    def __init__(self, venue_configs: Dict[str, VenueConfig], enabled: bool = True):
        self.configs = venue_configs
        self.enabled = enabled  # opt-in: False preserves v0.2 behavior

    def check_can_open(self, order, cash, positions) -> Tuple[bool, str]:
        """Can this order be opened given current margin?

        Returns (allowed, reason).
        Checks: sufficient free margin, max leverage not exceeded.
        """
        venue_config = self.configs.get(order.venue, DEFAULT_CONFIG)
        notional = order.size * current_price
        required_margin = notional * venue_config.initial_margin
        if required_margin > free_cash:
            return False, f"Insufficient margin: need ${required_margin:.0f}, have ${free_cash:.0f}"
        return True, ""

    def compute_liquidation_price(self, position, venue_config) -> float:
        """Use existing formula from mev/liquidation.py"""
        return compute_liquidation_price(
            position.entry_price, position.size,
            position.margin_allocated, venue_config.maintenance_margin,
            is_long=(position.side == Side.LONG)
        )

    def check_liquidations(self, positions, current_prices) -> List[LiquidationEvent]:
        """Check all positions against current prices.

        Called BEFORE strategy runs each bar.
        If mark price crosses liquidation price:
          - Force close the position
          - Apply liquidation penalty fee
          - Record event
        """
        events = []
        for (venue, market), pos in positions.items():
            price = current_prices.get(market)
            if price is None:
                continue
            if pos.side == Side.LONG and price <= pos.liquidation_price:
                events.append(LiquidationEvent(...))
            elif pos.side == Side.SHORT and price >= pos.liquidation_price:
                events.append(LiquidationEvent(...))
        return events
```

### Opt-in design

```python
# Default: no margin tracking (backward compatible)
engine = BacktestEngine(strategy, capital, fee_rate)

# Opt-in: enable margin
engine = BacktestEngine(strategy, capital, fee_rate,
                        margin_tracking=True,
                        venue_configs=load_venue_configs())
```

### Sprint tasks

```
Task 3.1: MarginEngine class                          [60 min]
  - Create flint/execution/margin.py
  - check_can_open(), compute_liquidation_price(), check_liquidations()
  - Reuse formula from mev/liquidation.py
  - Test: margin math is correct for long/short at various leverages

Task 3.2: Model extensions                            [20 min]
  - Add free_margin, margin_ratio, leverage to AccountState
  - Add LiquidationEvent dataclass
  - Add strategy_warnings for margin rejection messages
  - Test: models serialize correctly

Task 3.3: Context integration                         [60 min]
  - BacktestContext accepts optional MarginEngine
  - market_order() checks margin before placing
  - Rejected orders log warning via ctx.log()
  - _apply_fill computes and stores liquidation_price on new positions
  - Test: orders rejected when margin insufficient

Task 3.4: Engine liquidation loop                     [45 min]
  - Before each candle: check_liquidations() against current prices
  - Force-close liquidated positions at liquidation price + penalty
  - Track LiquidationEvent list in result
  - Opt-in via margin_tracking=True flag
  - Test: position liquidated at correct price, penalty applied

Task 3.5: Integration test                            [45 min]
  - Strategy opens 5x leverage long, price drops 25% → liquidated
  - Verify: liquidation fires at correct price, cash impact correct
  - Verify: non-margin backtest produces identical results to before
  - Verify: margin rejection warning appears in results

Total: ~4 hours
```

---

## Feature 4: Capital Fragmentation

### What it does
Model capital split across venues. Each venue has its own balance. Transfers between venues take time and cost fees. Return metrics computed against total deployed capital (the fragmentation penalty).

### What exists today
- Single `_cash` float in BacktestContext
- No concept of per-venue balances
- No transfer mechanics

### Data needed
- Transfer times per route (config, not external data):
  - Solana→Drift: ~30 seconds
  - Drift→CEX: ~10-30 minutes (depends on chain + confirmation)
  - CEX→CEX: ~15-60 minutes
- Transfer costs (withdrawal fees per venue)
- Minimum deposit/withdrawal amounts

### Schema changes
- No DuckDB changes
- New config in `flint.yaml`:

```yaml
venue_capital:
  initial_allocation:
    drift: 5000
    hyperliquid: 3000
    binance: 2000
  transfers:
    default_time_s: 1800    # 30 min
    default_cost_usd: 1.0
    routes:
      drift_to_hyperliquid:
        time_s: 600         # 10 min (Solana → EVM bridge)
        cost_usd: 5.0
      drift_to_binance:
        time_s: 1800
        cost_usd: 2.0
  min_venue_balance: 100    # don't drain below this
```

### Files to change

| File | Change | Lines |
|------|--------|-------|
| `flint/execution/capital.py` | **New file.** `VenueAllocator` class | ~150 |
| `flint/execution/backtest_context.py` | Replace `_cash` with VenueAllocator. All cash operations go through allocator. | ~100 |
| `flint/execution/context.py` | Add `venue_balance(venue)`, `transfer(from, to, amount)`, `pending_transfers()` | ~20 |
| `flint/models.py` | `TransferEvent`, `CapitalState` dataclasses | ~20 |
| `flint/backtest/engine.py` | Process pending transfers each bar. Include fragmentation metrics in result. | ~30 |
| `tests/test_capital.py` | New test file | ~150 |

### VenueAllocator design

```python
@dataclass
class Transfer:
    from_venue: str
    to_venue: str
    amount: float
    initiated_ts: int
    arrival_ts: int        # initiated_ts + transfer_time
    cost: float

class VenueAllocator:
    def __init__(self, initial_balances: Dict[str, float], config):
        self._balances: Dict[str, float] = dict(initial_balances)
        self._in_transit: List[Transfer] = []
        self._completed_transfers: List[Transfer] = []
        self._config = config

    @property
    def total_cash(self) -> float:
        """Total across all venues + in-transit (for equity computation)."""
        return sum(self._balances.values()) + sum(t.amount for t in self._in_transit)

    def available(self, venue: str) -> float:
        """Cash available to trade on this venue right now."""
        return self._balances.get(venue, 0.0)

    def debit(self, venue: str, amount: float) -> bool:
        """Deduct cash for a fill. Returns False if insufficient."""
        if self._balances.get(venue, 0) < amount:
            return False
        self._balances[venue] -= amount
        return True

    def credit(self, venue: str, amount: float):
        """Add cash (from closing a position, PnL, etc.)."""
        self._balances[venue] = self._balances.get(venue, 0) + amount

    def transfer(self, from_venue, to_venue, amount, current_ts) -> Transfer:
        """Initiate a transfer. Deducts immediately, arrives later."""
        cost = self._config.get_cost(from_venue, to_venue)
        time = self._config.get_time(from_venue, to_venue)

        self._balances[from_venue] -= (amount + cost)
        t = Transfer(from_venue, to_venue, amount, current_ts, current_ts + time, cost)
        self._in_transit.append(t)
        return t

    def process_arrivals(self, current_ts):
        """Credit arrived transfers. Called each bar by engine."""
        arrived = [t for t in self._in_transit if current_ts >= t.arrival_ts]
        for t in arrived:
            self._balances[t.to_venue] = self._balances.get(t.to_venue, 0) + t.amount
            self._completed_transfers.append(t)
        self._in_transit = [t for t in self._in_transit if current_ts < t.arrival_ts]
```

### Backward compatibility

When `VenueAllocator` is not provided, BacktestContext uses a simple `_cash` float as before. The allocator is opt-in alongside `margin_tracking`.

```python
# Old (still works):
engine = BacktestEngine(strategy, capital=10000, fee_rate=0.001)

# New:
engine = BacktestEngine(strategy, capital=10000, fee_rate=0.001,
                        margin_tracking=True,
                        venue_configs=load_venue_configs(),
                        capital_allocation={"drift": 5000, "hyperliquid": 3000, "binance": 2000})
```

### Return metrics additions

```python
@dataclass
class FragmentationMetrics:
    total_deployed: float              # sum of all venue allocations
    peak_utilization: Dict[str, float] # max % of venue capital used
    idle_capital_pct: float            # % of capital that was never touched
    transfer_count: int
    transfer_costs: float
    time_in_transit_hours: float       # capital-hours locked in transit
    effective_return: float            # PnL / total_deployed (not PnL / used)
```

### Sprint tasks

```
Task 4.1: VenueAllocator class                        [60 min]
  - Create flint/execution/capital.py
  - Balance tracking, debit/credit, transfer initiation
  - Arrival processing with configurable delays
  - Test: balances track correctly through transfers

Task 4.2: Model additions                             [20 min]
  - TransferEvent dataclass
  - FragmentationMetrics dataclass
  - CapitalState for real-time venue balance snapshot

Task 4.3: Context integration                         [90 min]
  - BacktestContext optionally wraps VenueAllocator
  - _apply_fill debits venue-specific balance
  - cash property returns total across venues (backward compat)
  - Add venue_balance(), transfer(), pending_transfers() to ctx
  - Test: fills debit correct venue, insufficient balance rejects

Task 4.4: Engine integration                          [45 min]
  - Process transfer arrivals each bar
  - Include FragmentationMetrics in BacktestResult
  - Capital allocation passed via engine constructor
  - Test: transfer arrives after delay, capital becomes available

Task 4.5: Integration test                            [60 min]
  - Full cross-venue arb: short Drift, long Hyperliquid
  - Mid-trade: rebalance capital via transfer
  - Verify: transfer delay means capital isn't instant
  - Verify: effective return < gross return due to fragmentation
  - Verify: non-fragmented backtest identical to v0.2

Total: ~5 hours
```

---

## Total Effort Summary

| Feature | Effort | Depends On | Risk |
|---------|--------|-----------|------|
| 1. Orderbook depth | ~3.5 hrs | Nothing | Low — additive, no breaking changes |
| 2. Multi-venue positions | ~4 hrs | Nothing (but benefits from 1) | Medium — position key refactor touches many paths |
| 3. Margin/liquidation | ~4 hrs | Feature 2 (venue configs) | Medium — opt-in design reduces blast radius |
| 4. Capital fragmentation | ~5 hrs | Features 2 + 3 | High — replaces core cash tracking |
| **Total** | **~16.5 hrs** | | |

### Recommended sprint cadence

```
Sprint 1 (1 session):   Feature 1 — Orderbook depth
Sprint 2 (1 session):   Feature 2 — Multi-venue positions
Sprint 3 (1 session):   Feature 3 — Margin tracking
Sprint 4 (1 session):   Feature 4 — Capital fragmentation
Sprint 5 (1 session):   Integration — Update funding_dislocation strategy to use all 4
```

### After all 4 features

The funding dislocation strategy can be upgraded to:
- Short rich perp on venue A (with venue-specific fees + margin)
- Long spot or cheap perp on venue B (with venue-specific fees)
- Check orderbook impact before entering (reject if too much slippage)
- Track per-venue margin and get liquidated realistically
- Model capital split with transfer delays between venues
- Report effective return on total deployed capital

This brings Flint to **Layer 2** (execution-aware backtest) of the spec's three-layer backtest requirement.
