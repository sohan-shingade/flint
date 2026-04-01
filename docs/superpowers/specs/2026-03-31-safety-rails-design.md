# Safety Rails + Parity Test — Design Spec

> Sub-project 3 of Phase 1 (ROADMAP.md §1.4 + §1.5)
> Date: 2026-03-31

## Overview

Add safety guarantees for live trading: real-time equity monitoring with auto-flatten kill switch, extended risk guards, dry-run mode, alert integration, and a backtest-to-live parity test. These wrap around the execution and WebSocket infrastructure from Sub-projects 1 and 2.

### Scope

**In scope:**
- Real-time equity monitor with kill switch (auto-flatten, require manual restart)
- Extended risk guards: per-market position limits, max orders per minute
- Dry-run mode: full pipeline, skip venue submission, simulate fills
- Alert integration: Telegram/Discord notifications on key events
- Backtest-to-live parity test: compare backtest vs paper on same data
- Config additions for all safety parameters
- CLI command + API endpoint for parity test

**Out of scope:**
- Advanced alerting (email, SMS, PagerDuty)
- UI dashboard for safety monitoring (Phase 5)
- Cross-venue risk aggregation (Phase 3)

---

## 1. Real-Time Equity Monitor

**New file:** `flint/risk/monitor.py`

Background async task that monitors equity independently of the strategy tick loop.

### How It Works

```
Every 5 seconds:
  1. Read oracle prices from Pyth cache (or store fallback)
  2. Recompute equity = cash + sum(unrealized PnL per position)
  3. Update peak equity
  4. If drawdown from peak >= kill_switch_drawdown_pct:
     → auto-flatten all positions
     → halt strategy
     → fire alert
     → require manual reset()
  5. If drawdown >= drawdown_warning_pct:
     → fire warning alert (once per threshold crossing)
```

### Interface

```python
class EquityMonitor:
    def __init__(
        self,
        context: LiveExecutionContext,
        kill_switch_pct: float = 0.15,
        warning_pct: float = 0.075,
        check_interval_s: float = 5.0,
        notification_manager: Optional[NotificationManager] = None,
        pyth_feed: Optional[PythWebSocketFeed] = None,
    ): ...

    async def run(self) -> None
        # Background monitoring loop

    async def stop(self) -> None

    def reset(self) -> None
        # Reset kill switch after manual review

    @property
    def tripped(self) -> bool

    @property
    def peak_equity(self) -> float

    @property
    def current_drawdown_pct(self) -> float
```

### Auto-Flatten

When kill switch triggers:
1. Call `context.cancel_all()` to cancel pending orders
2. For each open position: call `context.close_position(market, venue)` to submit market close orders
3. Call `await context.submit_pending_orders()` to execute the close orders
4. Set `context._running = False` to halt the tick loop
5. Fire `"kill_switch"` alert via NotificationManager

### Integration

Started as a background task in `LiveExecutionContext.run()` alongside the tick loop and order polling loop.

---

## 2. Extended Risk Guards

**Modify:** `flint/risk/guards.py`

### MaxOrdersPerMinute

Sliding window rate limiter at the risk guard level. Prevents runaway strategy loops.

```python
class MaxOrdersPerMinute(RiskGuard):
    def __init__(self, max_orders: int = 30): ...

    def check(self, order, account, positions) -> Optional[Order]:
        # Count orders in last 60 seconds (uses order.ts)
        # Reject if count >= max_orders
```

Tracks timestamps of passed orders in a deque. Prunes entries older than 60 seconds.

### PerMarketPositionLimit

Hard cap per market in USD notional. Different from `MaxPositionSize` which is a single global cap.

```python
class PerMarketPositionLimit(RiskGuard):
    def __init__(self, limits: Dict[str, float]): ...
        # limits = {"SOL-PERP": 10000, "BTC-PERP": 50000}

    def check(self, order, account, positions) -> Optional[Order]:
        # Check if existing notional + new order notional exceeds per-market limit
        # Markets not in limits dict are uncapped
```

---

## 3. Dry-Run Mode

**Modify:** `flint/execution/live_base.py`

### Constructor

Add `dry_run: bool = False` parameter.

### Behavior When `dry_run=True`

The full order pipeline runs normally:
- Risk guards evaluate orders
- OrderTracker manages state machine
- Orders get persisted to store

But `submit_pending_orders()` changes behavior:
- Instead of calling `await self._place_order(order)`, creates a simulated fill:
  - Price = current candle close (or last known price)
  - Fee = estimated from venue config
  - `tx_sig = "DRY_RUN"`
- Marks order as filled via OrderTracker
- Positions update normally from the simulated fill
- Equity tracks normally

This lets you validate everything (risk guards, strategy logic, position sizing) without real capital.

### Logging

All dry-run orders logged with `[DRY RUN]` prefix. Store records have `tx_sig="DRY_RUN"` for easy filtering.

---

## 4. Alert Integration

**Modify:** `flint/execution/live_base.py`

### Constructor

Add `notification_manager: Optional[NotificationManager] = None` parameter.

### Alert Events

| Event | When | Severity |
|-------|------|----------|
| `fill` | Fill received | info |
| `position_opened` | New position created | info |
| `position_closed` | Position fully closed | info |
| `risk_rejection` | Risk guard rejects an order | warning |
| `drawdown_warning` | Equity drops past warning threshold | warning |
| `kill_switch` | Kill switch triggered, positions flattened | critical |
| `order_failed` | Order failed after max retries | error |
| `strategy_error` | Strategy threw exception in on_candle | error |

### Implementation

Add `_notify(event_type, message, data=None)` helper method that creates a `TradingEvent` and calls `notification_manager.notify()`. Called from existing callback methods:

- `_handle_fill()` → fires `"fill"` event
- `_handle_fail()` → fires `"order_failed"` event
- `_submit_order()` when rejected → fires `"risk_rejection"` event
- `_tick()` on strategy exception → fires `"strategy_error"` event
- `EquityMonitor` → fires `"kill_switch"` and `"drawdown_warning"` events

### Config

Uses existing `telegram_bot_token`, `telegram_chat_id`, `discord_webhook_url` from FlintConfig. The `LiveExecutionContext` (or its caller) wires up the NotificationManager from config.

---

## 5. Config Additions

**Modify:** `flint/config.py`

```python
# --- Safety rails ---
live_dry_run: bool = False
live_kill_switch_drawdown_pct: float = 0.15
live_kill_switch_check_interval_s: float = 5.0
live_max_orders_per_minute: int = 30
live_per_market_position_limits: str = ""  # JSON string: '{"SOL-PERP": 10000}'
live_drawdown_warning_pct: float = 0.075
```

Note: `live_per_market_position_limits` is a JSON string because Pydantic env vars can't easily parse nested dicts. Parsed at runtime.

---

## 6. Parity Test

**New file:** `flint/backtest/parity.py`

### ParityTest Class

```python
class ParityTest:
    def __init__(
        self,
        strategy,
        market: str,
        candles: List[Candle],
        initial_capital: float = 10_000.0,
        fee_rate: float = 0.0005,
        funding_rates: Optional[List[FundingRate]] = None,
    ): ...

    def run(self) -> ParityReport:
        # 1. Run BacktestEngine on candles → BacktestResult
        # 2. Run PaperBroker + iterate candles manually → collect fills, equity
        # 3. Compare results
        # 4. Return ParityReport
```

### ParityReport

```python
@dataclass
class ParityReport:
    # Backtest results
    backtest_pnl: float
    backtest_trades: int
    backtest_equity_curve: List[float]

    # Paper results
    paper_pnl: float
    paper_trades: int
    paper_equity_curve: List[float]

    # Divergence metrics
    pnl_divergence_pct: float          # abs(bt_pnl - paper_pnl) / max(abs(bt_pnl), 1)
    fill_price_mae: float               # mean absolute error of fill prices
    equity_correlation: float            # correlation of equity curves
    trade_count_match: bool              # same number of trades?
    signal_timing_match_pct: float       # % of trades at same candle timestamp

    # Verdict
    passed: bool                         # pnl_divergence_pct < 2%
    threshold_pct: float = 2.0

    def to_dict(self) -> dict: ...
    def summary(self) -> str: ...        # human-readable summary
```

### Comparison Logic

1. **Fill price MAE**: For each trade pair (matched by timestamp), compute `abs(bt_fill_price - paper_fill_price)`. Average across all trades.
2. **PnL divergence**: `abs(bt_pnl - paper_pnl) / max(abs(bt_pnl), 1) * 100`
3. **Equity correlation**: Pearson correlation of the two equity curves (should be > 0.99).
4. **Signal timing**: Match trades by timestamp. % of backtest trades that have a paper trade at the same candle.
5. **Pass/fail**: PnL divergence < 2%.

### CLI

Add to `flint/cli.py`:

```
flint parity --strategy momentum --market SOL-PERP --start 2026-01-01 --end 2026-03-01
```

Outputs:
```
Parity Test: momentum on SOL-PERP (2026-01-01 to 2026-03-01)
─────────────────────────────────────────────────────
Backtest PnL:  $1,234.56  (23 trades)
Paper PnL:     $1,210.32  (23 trades)
PnL Divergence: 1.96%
Fill Price MAE:  $0.03
Equity Corr:    0.998
Signal Match:   100%
─────────────────────────────────────────────────────
Result: PASS ✓ (< 2.0% divergence)
```

### API Endpoint

Add to `flint/api/routes/backtest.py`:

```
POST /api/v1/backtest/parity
Body: { strategy, market, start_date, end_date, capital, fee_rate }
Response: ParityReport.to_dict()
```

---

## Testing Strategy

All tests mocked — no real connections or trading.

- **EquityMonitor**: Mock oracle prices, test kill switch trigger, auto-flatten flow, warning threshold, reset()
- **MaxOrdersPerMinute**: Test sliding window, boundary conditions, rejection logging
- **PerMarketPositionLimit**: Test per-market caps, uncapped markets, multiple positions
- **Dry-run mode**: Test full pipeline with simulated fills, verify tx_sig="DRY_RUN", positions update
- **Alert integration**: Mock NotificationManager, verify events fire on fill, rejection, kill switch
- **ParityTest**: Run on known candle data, verify divergence metrics, pass/fail threshold
- **CLI parity command**: Test CLI invocation with mock data

---

## Dependencies

No new dependencies. Uses existing:
- `httpx` for Telegram/Discord HTTP calls (already installed)
- `numpy` for correlation computation in parity test (already available in strategies)
