# Live Deployment Guide

This guide covers deploying a Flint strategy to live trading on Drift and/or Hyperliquid -- from dry-run validation through devnet testing to mainnet execution, including multi-venue live trading and risk management.

---

## 1. Venue Setup

Flint supports live trading on two venues: **Drift** (Solana-native) and **Hyperliquid** (Arbitrum L2). You can trade on one or both simultaneously.

### Drift

Drift uses a Solana keypair for signing transactions on-chain via the driftpy SDK.

**Environment variables:**

```bash
FLINT_PRIVATE_KEY=<base58-encoded-solana-keypair>
FLINT_RPC_URL=https://api.mainnet-beta.solana.com
```

`FLINT_PRIVATE_KEY` must be the base58 representation of a 64-byte Solana keypair (the format output by `solana-keygen new`). Never use a hardware wallet address here -- Flint needs the raw private key to sign transactions.

**flint.yaml:**

```yaml
live_network: devnet   # devnet | mainnet
```

Use `devnet` while testing. Switch to `mainnet` only after completing the mainnet checklist below.

**Getting test SOL on devnet:**

```bash
solana airdrop 2 <your-wallet-address> --url devnet
```

Or use the [Solana devnet faucet](https://solfaucet.com).

### Hyperliquid

Hyperliquid uses an Ethereum-style private key (hex, without the `0x` prefix) for EIP-712 order signing.

**Environment variable:**

```bash
FLINT_HYPERLIQUID_PRIVATE_KEY=<64-char-hex-private-key>
```

**Recommended: use an API wallet.** Hyperliquid supports trade-only API wallets that cannot initiate withdrawals. This is the safest option for automated trading. Create one at [app.hyperliquid.xyz](https://app.hyperliquid.xyz) -> Settings -> API Wallets. Fund it by transferring from your main account via the Hyperliquid web UI.

**Withdrawals must always be initiated through the Hyperliquid web UI** -- Flint does not implement withdrawal logic. This is by design for security.

**flint.yaml for Hyperliquid testnet:**

```yaml
live_network: devnet   # uses Hyperliquid testnet when devnet is set
```

When `live_network` is `devnet`, Flint connects to Hyperliquid's testnet. When `mainnet`, it connects to production.

---

## 2. Paper Trading

Before going live, validate your strategy with paper trading. Paper trading uses real-time price data from WebSocket feeds with simulated execution.

### Start via the UI

On any backtest result in BacktestLab, click **Deploy to Paper**. The paper session starts immediately with:

- **Venue selection** -- choose which venue(s) to paper trade on (Drift, Hyperliquid, or both)
- **Replay-forward execution** -- replays up to 30 days of history, then transitions to live candle processing
- **Realistic fills** -- 5bps slippage, venue-specific fee schedule, optional order latency
- **Funding rate payments** -- applied hourly from real multi-venue data
- **Live PnL updates** -- DLOB mid-price polling every 5 seconds
- **Equity curve with buy-and-hold baseline** -- see your strategy vs just holding the asset
- **Trade markers** -- entry/exit dots on the equity chart
- **Session persistence** -- survives server restarts, resumes automatically
- **Per-venue capital allocation** -- allocate capital per venue with simulated transfer delays

### Start via the API

```bash
curl -s -X POST http://localhost:8000/api/v1/paper/start \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": "momentum",
    "market": "SOL-PERP",
    "initial_capital": 10000,
    "venue": "drift"
  }'
```

### Start via the CLI

```bash
flint live --paper --strategy momentum --market SOL-PERP
```

---

## 3. Risk Configuration

All live risk settings live under top-level keys in `flint.yaml`. They apply across all venues unless overridden.

```yaml
# Kill switch -- flatten all positions immediately if equity drops this much from peak
live_kill_switch_drawdown_pct: 0.15

# Early warning before kill switch fires
live_drawdown_warning_pct: 0.075

# Rate limiter -- prevents runaway strategy loops
live_max_orders_per_minute: 30

# Per-market notional position caps (USD)
live_per_market_position_limits: '{"SOL-PERP": 10000, "BTC-PERP": 50000}'

# Dry-run mode -- logs all intended orders but submits nothing
live_dry_run: true
```

### Kill Switch

When `live_kill_switch_drawdown_pct` is breached, `EquityMonitor` cancels all open orders and closes all positions immediately across all venues. The live session will not restart automatically -- you must restart `flint live` manually after investigating.

### Risk Guard Chain

`RiskManager` runs a chain of guards before each order is submitted:

| Guard | Config key | Behavior |
|-------|-----------|---------|
| `MaxPositionSize` | `live_per_market_position_limits` | Rejects orders exceeding USD notional cap |
| `MaxOpenPositions` | `max_open_positions` | Rejects new entries when too many positions open |
| `MaxDrawdownCircuitBreaker` | `max_drawdown_pct` | Rejects all orders after cumulative drawdown |
| `DailyLossLimit` | `daily_loss_limit_usd` | Rejects orders after daily loss exceeds threshold |
| `MaxOrdersPerMinute` | `live_max_orders_per_minute` | Sliding window rate limiter |

The same risk guards apply in backtests, paper trading, and live trading.

---

## 4. Dry-Run Testing

Dry-run mode runs the full live pipeline -- WebSocket feeds, strategy ticks, order construction, risk checks -- but does not submit transactions. Every intended order is logged with `tx_sig="DRY_RUN"`.

Enable in `flint.yaml`:

```yaml
live_dry_run: true
```

Or pass on the CLI:

```bash
flint live --strategy momentum --market SOL-PERP --dry-run
```

Dry-run fills are simulated at the mid-price with the configured fee rate. Check the logs to confirm the strategy is generating signals at the expected frequency before switching to real orders.

**Both venues support dry-run.** When running multi-venue, dry-run applies to all venues simultaneously.

---

## 5. Devnet / Testnet Testing

Always test on devnet before mainnet. Devnet has the same code path -- the only difference is the RPC URL and that transactions are not real.

```yaml
live_network: devnet
live_dry_run: false   # test real tx submission on devnet
```

For Drift, `devnet` connects to Solana devnet via driftpy. For Hyperliquid, `devnet` connects to Hyperliquid testnet.

Steps:

1. Fund a devnet wallet (Solana airdrop or Hyperliquid testnet faucet).
2. Start the live session: `flint live --strategy <name> --market SOL-PERP`.
3. Monitor logs and the Flint dashboard for fills, errors, and kill switch events.
4. Run for at least a few hours to catch reconnection, funding-rate ticks, and fill confirmation edge cases.

---

## 6. Mainnet Checklist

Work through this before switching `live_network: mainnet`:

- [ ] Wallet funded with enough SOL for gas + collateral (recommend 0.1 SOL gas buffer minimum for Drift)
- [ ] Hyperliquid API wallet funded via web UI transfer (if using Hyperliquid)
- [ ] `FLINT_RPC_URL` set to a reliable mainnet endpoint (Helius, QuickNode, or your own validator)
- [ ] `live_kill_switch_drawdown_pct` set to an acceptable loss threshold
- [ ] `live_per_market_position_limits` sized to your actual capital
- [ ] `live_max_orders_per_minute` reviewed for the strategy's expected signal frequency
- [ ] At least one alert channel configured (see Monitoring below)
- [ ] Dry-run on mainnet verified (`live_dry_run: true`, `live_network: mainnet`) -- confirms RPC connectivity and order construction without submitting
- [ ] Devnet test session completed with no unexpected errors
- [ ] Parity test passed (see Parity Test below)

Switch to mainnet:

```yaml
live_network: mainnet
live_dry_run: false
```

---

## 7. Multi-Venue Live Execution

To run a strategy across Drift and Hyperliquid simultaneously, Flint uses `MultiVenueLiveContext`. This wraps both `LiveDriftContext` and `LiveHyperliquidContext`, maintaining separate margin, position state, and WebSocket connections per venue.

### Setup

Provide keys for both venues in your `.env`:

```bash
FLINT_PRIVATE_KEY=<solana-keypair>
FLINT_RPC_URL=https://api.mainnet-beta.solana.com
FLINT_HYPERLIQUID_PRIVATE_KEY=<hex-private-key>
```

### Configuration

```yaml
live_multi_venue_primary: drift   # which venue's candle closes drive strategy ticks
```

**Tick modes:**

| Mode | Behavior | Use case |
|------|----------|----------|
| `primary` | Ticks only on primary venue candle close | Latency-sensitive strategies, single authoritative clock |
| `any` | Ticks on any venue candle close | Cross-venue monitoring, arb detection |

```yaml
live_tick_mode: primary   # primary | any
```

### Routing orders

In your strategy, route orders by venue explicitly:

```python
ctx.market_order("SOL-PERP", "long", size, venue="drift")
ctx.market_order("SOL-PERP", "short", size, venue="hyperliquid")
```

### Paired leg submission

For cross-venue strategies (e.g., funding arb), `MultiVenueLiveContext` supports paired leg submission:

- Both legs are submitted simultaneously
- A configurable timeout waits for both fills
- If one leg fills and the other fails, auto-unwind logic closes the filled leg to prevent unhedged exposure

### Account state

`ctx.account` reflects total equity across all venues. Per-venue breakdowns are available via:

```python
ctx.venue_balance("drift")      # Cash on Drift
ctx.venue_balance("hyperliquid")  # Cash on Hyperliquid
ctx.venue_positions("drift")    # Positions on Drift only
```

---

## 8. WebSocket Feeds

All live data arrives through venue-specific WebSocket feeds:

```
WebSocketFeed (ABC)              -- reconnection logic, health checks, REST fallback
  +-- DriftWebSocketFeed         -- trade streaming + funding rate subscription
  +-- HyperliquidWebSocketFeed   -- candle, L2 book, orderUpdates channels
  +-- PythWebSocketFeed          -- sub-second oracle prices (Hermes)
```

**Reconnection**: exponential backoff (1s -> 2s -> 4s ... max 60s). On reconnect, missed candles are backfilled from the REST provider.

**Health check**: forces reconnect if no message received for 30 seconds.

Paper trading uses the same WebSocket feeds as live trading -- it ticks on real candle closes rather than stored historical candles.

---

## 9. Monitoring and Alerting

Flint can push notifications to Telegram and/or Discord.

**Telegram:**

```yaml
telegram_bot_token: "123456:ABC-your-bot-token"
telegram_chat_id: "-100your-chat-id"
```

Create a bot via [@BotFather](https://t.me/BotFather). Get the chat ID by sending a message and calling `https://api.telegram.org/bot<token>/getUpdates`.

**Discord:**

```yaml
discord_webhook_url: "https://discord.com/api/webhooks/..."
```

Create a webhook under your Discord server -> channel settings -> Integrations.

**Events that fire alerts:**

| Event | Severity |
|-------|----------|
| Fill received (open/close) | Info |
| Order rejected by risk guard | Warning |
| Drawdown warning threshold breached | Warning |
| Kill switch triggered | Critical |
| WebSocket disconnected / reconnected | Info |
| RPC error / tx retry | Warning |
| Uncaught strategy exception | Critical |
| Paired leg failure / auto-unwind | Critical |

---

## 10. Parity Test

Before going live, verify that your strategy's backtest behavior matches the paper/live engine on the same historical window.

```bash
flint parity --strategy momentum --market SOL-PERP --start 2026-01-01 --end 2026-03-01
```

This runs both the backtest engine and the paper engine on the specified window and compares:

- PnL divergence (pass threshold: < 2%)
- Fill price MAE (mean absolute error vs backtest fills)
- Equity curve correlation
- Signal timing match rate

A failing parity test (> 2% PnL divergence) usually indicates a fill model mismatch, a fee rate discrepancy, or a funding rate that was included in one engine but not the other. Investigate before going live.

Also available via API:

```bash
curl -X POST http://localhost:8000/api/v1/backtest/parity \
  -H 'Content-Type: application/json' \
  -d '{"strategy": "momentum", "market": "SOL-PERP", "start": "2026-01-01", "end": "2026-03-01"}'
```

---

## 11. Live Execution Architecture

The full execution hierarchy for live trading:

```
ExecutionContext (ABC)
+-- BacktestContext              -- candle-replay, fills via FillPipeline
+-- PaperBroker                  -- same fill models, no on-chain tx
+-- LiveExecutionContext (ABC)
    +-- LiveDriftContext          -- driftpy SDK, Solana RPC
    +-- LiveHyperliquidContext    -- native REST + EIP-712 signing
    +-- MultiVenueLiveContext     -- wraps multiple venue contexts, routes by venue param
```

Each `LiveExecutionContext` subclass implements:

- `place_order` -- submit to the venue's matching engine
- `cancel_order` -- cancel a resting order
- `modify_order` -- amend price/size on a resting order
- `get_positions` -- query current positions
- `get_account` -- query margin/equity
- `poll_fills` -- check for new fills since last poll
- `sync_on_startup` -- reconcile local state with venue state on restart

`OrderTracker` maintains a state machine for each order's lifecycle: `PENDING -> SUBMITTED -> FILLED / CANCELLED / REJECTED`.
