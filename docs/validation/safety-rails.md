# Safety Rails

Read this before deploying any strategy beyond backtesting.

---

## 1. Kill Switch (EquityMonitor)

The `EquityMonitor` (`flint/risk/monitor.py`) runs as a background async task
alongside the strategy tick loop, checking equity every `check_interval_s`
seconds (default **5s**) against two thresholds.

| Parameter | Config key | Default |
|---|---|---|
| Kill switch threshold | `live_kill_switch_drawdown_pct` | **0.15** (15%) |
| Warning threshold | `live_drawdown_warning_pct` | **0.075** (7.5%) |
| Check interval | `live_kill_switch_check_interval_s` | **5.0s** |

**What happens when it triggers:**

1. A `CRITICAL` log is emitted: `KILL SWITCH: drawdown X% >= Y%. Flattening all positions.`
2. `cancel_all()` is called -- every open and pending order across all venues is cancelled.
3. `close_position()` is called for every open position, per-market and per-venue.
4. The strategy tick loop is halted (`_running = False`).
5. If a `NotificationManager` is configured (Telegram, Discord, webhook), an alert is sent with equity details.
6. **Manual restart is required.** The monitor sets `_tripped = True` and will not resume automatically. You must restart the `flint live` process after investigating.

The warning threshold fires a `WARNING` log and notification at 7.5% drawdown
(default), giving you a chance to intervene before the kill switch trips.

---

## 2. Risk Guards

Risk guards are pre-trade checks chained in a `RiskManager` -- an order must
pass **all** guards to be submitted. If any guard rejects, the order is dropped
and logged. See `flint/risk/guards.py`.

### MaxPositionSize

Rejects orders whose cumulative notional (existing + new) on a market exceeds
a USD cap. Configured via `RiskManager` construction. On violation: order
rejected with `INFO` log. The order never reaches the venue.

### PerMarketPositionLimit

Hard USD notional cap per individual market, configured as `{market: limit}`.

- **Config key**: `live_per_market_position_limits` (comma-separated `MARKET:LIMIT` pairs).
- **On violation**: Order rejected with `INFO` log.

### MaxOpenPositions

Rejects new-position orders when the cap is reached. Orders that reduce an
existing position pass through. Config: `max_open_trades` (default **5**).

### MaxDrawdownCircuitBreaker

Stops all trading once cumulative drawdown from peak exceeds the threshold.
Unlike the kill switch, this does **not** flatten positions -- it only rejects
new orders. Once tripped, stays tripped until explicitly reset.
Config: `max_drawdown_pct` (default **0.20** / 20%).

### DailyLossLimit

Stops trading for the rest of the calendar day after daily PnL drops below a
USD threshold. Resets automatically at the start of a new day.
Config: `DailyLossLimit(max_daily_loss=500, initial_capital=10000)`.

### MaxOrdersPerMinute

Rejects orders if too many have been placed in the last 60 seconds.
Config: `live_max_orders_per_minute` (default **30**).

---

## 3. Dry-Run Mode

Dry-run mode executes the full live pipeline -- strategy ticks, risk guard
evaluation, order tracking, fill logging -- without submitting real
transactions to any venue.

**How to enable:**

```bash
flint live --strategy my_strategy.py --venue drift --dry-run
```

Or in `flint.yaml`:

```yaml
live_dry_run: true
```

**What you see:** Orders flow through the entire pipeline. Fills are recorded
with `tx_sig="DRY_RUN"` and priced at the current candle close. Fees are
simulated at 5bps. All logging, notifications, and risk guard checks work
identically to real execution.

**Why to use it:** Validate strategy behavior under mainnet market conditions
(real prices, real funding rates) without financial risk. Run dry-run for at
least a full trading session before switching to live.

See `flint/execution/live_base.py`, `submit_pending_orders()` method.

---

## 4. Order Lifecycle Safety

The `OrderTracker` (`flint/execution/order_tracker.py`) manages every order
from creation to terminal state with built-in safety mechanisms.

### Rate Limiting

| Parameter | Config key | Default |
|---|---|---|
| Max orders/sec | `live_rate_limit_orders_per_sec` | **10** |
| Max concurrent tx | `live_rate_limit_concurrent_tx` | **2** |

If either limit is hit, remaining orders are deferred to the next tick.

### Retry Logic

| Parameter | Config key | Default |
|---|---|---|
| Max retries | `live_max_retries` | **3** |
| On failure after retries | `live_on_order_failure` | **"drop"** |

On submission failure, the order returns to PENDING and retries next tick.
After `max_retries`: **"drop"** (default) marks FAILED and continues;
**"halt"** marks FAILED and stops the strategy loop (manual restart required).

### Order Timeout

- **Submission timeout** (30s): SUBMITTED orders with no on-chain confirmation trigger a retry.
- **Limit order timeout** (`live_limit_order_timeout_bars`, default **10 bars**): Unfilled limit orders are marked EXPIRED and cancelled.

---

## 5. Network Failure Handling

### RPC Failures

Order submission failures trigger the retry logic described above (exponential
back-off via tick-based retry, up to `max_retries`). Position sync failures are
logged but do not halt the strategy -- the next sync interval retries
automatically.

### WebSocket Disconnects

When the event-driven tick loop (`on_candle_close` mode) does not receive a
WebSocket candle within `tick_interval_s * 2`, it falls back to REST candle
fetch. WebSocket feeds reconnect automatically on the next cycle.

### Transaction Dropped

If a submitted transaction is not confirmed within the 30-second submission
timeout, the `OrderTracker` retries submission (up to `max_retries`). Orders
are not duplicated -- each retry reuses the same Flint order ID.

---

## 6. "What Happens When Things Go Wrong"

### Scenario: Your strategy enters a runaway loop

The `MaxOrdersPerMinute` guard (default 30/min) rejects excess orders before
they reach the venue. The `OrderTracker` rate limiter (default 10/sec, 2
concurrent) provides a second layer. If the strategy somehow floods beyond
both, the kill switch monitors equity and will flatten everything if drawdown
hits the threshold.

### Scenario: Market crashes 20% in 5 minutes

The `EquityMonitor` checks equity every 5 seconds. When drawdown from peak
exceeds 15% (default), it cancels all orders, closes all positions across all
venues, halts the strategy, and sends a notification. The
`MaxDrawdownCircuitBreaker` (20% default) provides a secondary guard that
blocks any new orders even if the monitor is delayed.

### Scenario: Solana RPC goes down

Order submissions fail and enter the retry queue (up to 3 retries). The
WebSocket tick loop falls back to REST polling. Position sync logs errors but
continues retrying each interval. No orders are submitted to a dead RPC -- they
queue locally until connectivity is restored or retries are exhausted.

### Scenario: You deploy to mainnet by accident

The default `live_network` is `"devnet"` and `live_hyperliquid_network` is
`"testnet"`. Mainnet requires explicitly setting `--network mainnet` on the CLI
or `live_network: mainnet` in `flint.yaml`. There is no path to mainnet
without deliberate configuration.

### Scenario: A strategy bug opens a huge position

`PerMarketPositionLimit` rejects the order before it reaches the venue if the
resulting notional would exceed the configured cap. `MaxPositionSize` provides
a global cap. If no position limits are configured and the order goes through,
the `EquityMonitor` will catch the resulting equity impact.

---

## 7. Recommended Safety Configuration

A conservative `flint.yaml` for a first-time live deployment:

```yaml
# Start on devnet -- switch to mainnet only after validation
live_network: devnet
live_hyperliquid_network: testnet

# Enable dry-run for mainnet testing (disable when ready for real orders)
live_dry_run: true

# Kill switch: flatten everything at 10% drawdown, warn at 5%
live_kill_switch_drawdown_pct: 0.10
live_drawdown_warning_pct: 0.05
live_kill_switch_check_interval_s: 5.0

# Rate limiting
live_max_orders_per_minute: 20
live_rate_limit_orders_per_sec: 5
live_rate_limit_concurrent_tx: 1

# Order failure: halt the strategy (don't silently continue)
live_on_order_failure: halt
live_max_retries: 3
live_limit_order_timeout_bars: 5

# Position limits (adjust to your capital)
max_open_trades: 3
max_drawdown_pct: 0.15
live_per_market_position_limits: "SOL-PERP:5000,BTC-PERP:10000,ETH-PERP:7500"

# Notifications (at least one -- you want to know when the kill switch fires)
# telegram_bot_token: "your-bot-token"
# telegram_chat_id: "your-chat-id"
# discord_webhook_url: "https://discord.com/api/webhooks/..."
```

**Recommended progression:**

1. Backtest your strategy with realistic fill models and funding.
2. Paper trade for at least 1 week with live data.
3. Deploy on devnet/testnet with real venue connectivity but fake funds.
4. Run `--dry-run` on mainnet to verify order flow against real conditions.
5. Go live on mainnet with conservative position limits and the kill switch enabled.
