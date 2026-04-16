# Live Deployment Guide

This guide covers the full path from paper trading to mainnet execution on Drift, Hyperliquid, and centralized exchanges via CCXT. It includes venue setup, risk configuration, parity testing, and monitoring.

---

## 1. Paper Trading

Paper trading runs your strategy on real-time price data with simulated execution. No keys, no real orders, no risk. Use it to validate behavior before committing capital.

### Start via the UI

In BacktestLab, click **Deploy to Paper** on any backtest result. The paper session starts immediately with:

- **Venue selection** -- choose Drift, Hyperliquid, CEX, or multiple venues
- **Replay-forward execution** -- replays up to 30 days of history, then transitions to live candle processing via WebSocket feeds
- **Realistic fills** -- 5bps slippage, venue-specific fee schedule, optional order latency
- **Funding rate payments** -- applied hourly from real multi-venue data
- **Live PnL updates** -- mid-price polling every 5 seconds
- **Equity curve with buy-and-hold baseline** -- strategy performance vs holding the asset
- **Trade markers** -- entry/exit dots on the equity chart
- **Session persistence** -- survives server restarts and resumes automatically
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

### Session status

A paper session is either **running** (actively processing candles and generating orders) or **stopped** (closed out, no longer ticking). Monitor running sessions in the Paper Trading page of the UI or via the API:

```bash
# List all sessions
curl http://localhost:8000/api/v1/paper/sessions

# Get detailed status of a session (equity, positions, trades)
curl http://localhost:8000/api/v1/paper/{session_id}/status
```

The Paper Trading page shows live equity curves, open positions, recent trades, and cumulative PnL for each running session.

---

## 2. Venue Setup

Flint supports live trading on three venue types: **Drift** (Solana-native), **Hyperliquid** (Arbitrum L2), and **CEX** (Binance, OKX, Bybit, and any CCXT-compatible exchange). You can trade on one or more simultaneously.

### Drift

Drift uses a Solana keypair for signing transactions on-chain via the driftpy SDK.

**Environment variables:**

```bash
FLINT_PRIVATE_KEY=<base58-encoded-solana-keypair>
FLINT_RPC_URL=https://api.mainnet-beta.solana.com
```

`FLINT_PRIVATE_KEY` must be the base58 representation of a 64-byte Solana keypair (the format output by `solana-keygen new`). Never use a hardware wallet address -- Flint needs the raw private key to sign transactions.

**flint.yaml:**

```yaml
live_network: devnet   # devnet | mainnet
```

Use `devnet` while testing. When `devnet` is set, Flint connects to Solana devnet via driftpy. Switch to `mainnet` only after completing the mainnet checklist below.

**Getting test SOL on devnet:**

```bash
solana airdrop 2 <your-wallet-address> --url devnet
```

### Hyperliquid

Hyperliquid uses an Ethereum-style private key (hex, without the `0x` prefix) for EIP-712 order signing.

**Environment variable:**

```bash
FLINT_HYPERLIQUID_PRIVATE_KEY=<64-char-hex-private-key>
```

**Use an API wallet.** Hyperliquid supports trade-only API wallets that cannot initiate withdrawals. This is the safest option for automated trading. Create one at [app.hyperliquid.xyz](https://app.hyperliquid.xyz) under Settings, then API Wallets. Fund it by transferring from your main account via the Hyperliquid web UI.

Withdrawals must always be initiated through the Hyperliquid web UI -- Flint does not implement withdrawal logic. This is by design for security.

When `live_network` is `devnet`, Flint connects to Hyperliquid's testnet. When `mainnet`, it connects to production.

### CEX (Binance, OKX, Bybit, and others)

Flint supports live trading on centralized exchanges via the CCXT library. The `LiveCCXTContext` provides the same interface as `LiveDriftContext` and `LiveHyperliquidContext` -- strategies deploy to any CCXT-compatible exchange with zero code changes.

**Environment variables:**

```bash
FLINT_CCXT_EXCHANGE=binance       # Exchange name: "binance", "okx", "bybit", etc.
FLINT_CCXT_API_KEY=<your-api-key>
FLINT_CCXT_SECRET=<your-api-secret>
FLINT_CCXT_PASSWORD=<passphrase>  # Required for OKX; optional for others
FLINT_CCXT_SANDBOX=true           # "true" for testnet (default), "false" for production
```

Market symbols are mapped automatically. Flint's `SOL-PERP` maps to `SOL/USDT:USDT` on CCXT exchanges, `BTC-PERP` maps to `BTC/USDT:USDT`, and so on for 18+ supported pairs. Any `-PERP` market not in the explicit mapping falls back to `{BASE}/USDT:USDT`.

**Exchange-specific notes:**

| Exchange | Password required | Sandbox | Notes |
|----------|-------------------|---------|-------|
| Binance | No | Yes (testnet.binancefuture.com) | Largest CEX, deepest liquidity |
| OKX | Yes (`FLINT_CCXT_PASSWORD`) | Yes (aws.okx.com sandbox) | Passphrase set during API key creation |
| Bybit | No | Yes (testnet.bybit.com) | Good perp liquidity |

Always start with `FLINT_CCXT_SANDBOX=true` and switch to `false` only after completing the mainnet checklist.

---

## 3. Risk Configuration

Risk settings apply in `flint.yaml` and are enforced across backtests, paper trading, and live trading.

### Kill switch

The `EquityMonitor` tracks equity continuously in live sessions. When equity drops below the configured threshold from its peak, it cancels all open orders and closes all positions immediately across all venues.

```yaml
# Kill switch -- flatten everything if equity drops this much from peak
live_kill_switch_drawdown_pct: 0.15

# Early warning before kill switch fires
live_drawdown_warning_pct: 0.075
```

The live session will not restart automatically after a kill switch event. You must restart `flint live` manually after investigating.

### Risk guard chain

`RiskManager` runs a chain of six guards before each order is submitted. An order must pass all guards to be executed.

| Guard | What it does | Config key |
|-------|-------------|------------|
| `MaxPositionSize` | Rejects orders when cumulative USD notional on a market exceeds a cap | `max_position_notional` |
| `MaxOpenPositions` | Rejects new entries when too many positions are open (does not block closes) | `max_open_positions` |
| `MaxDrawdownCircuitBreaker` | Rejects all orders once cumulative drawdown from peak exceeds threshold; must be manually reset | `max_drawdown_pct` |
| `DailyLossLimit` | Rejects orders after daily realized loss exceeds a USD threshold; resets at day boundary | `daily_loss_limit_usd` |
| `MaxOrdersPerMinute` | Sliding-window rate limiter preventing runaway strategy loops | `live_max_orders_per_minute` |
| `PerMarketPositionLimit` | Hard per-market USD notional cap, configured as a JSON map | `live_per_market_position_limits` |

**Example configuration:**

```yaml
max_open_positions: 5
max_drawdown_pct: 0.20
daily_loss_limit_usd: 500
live_max_orders_per_minute: 30
live_per_market_position_limits: '{"SOL-PERP": 10000, "BTC-PERP": 50000}'
```

### Dry-run mode

Dry-run mode runs the full live pipeline -- WebSocket feeds, strategy ticks, order construction, risk checks -- but does not submit transactions. Every intended order is logged with `tx_sig="DRY_RUN"`.

```yaml
live_dry_run: true
```

Or via the CLI:

```bash
flint live --strategy momentum --market SOL-PERP --dry-run
```

Dry-run fills are simulated at the mid-price with the configured fee rate. Both venues support dry-run. When running multi-venue, dry-run applies to all venues simultaneously.

---

## 4. Parity Testing

Before going live, verify that your strategy produces consistent results across the backtest engine and the paper/live engine on the same historical window.

### What it measures

The parity test runs both engines on the same candle data and compares:

| Metric | Description | Pass threshold |
|--------|-------------|----------------|
| PnL divergence | Percentage difference in total PnL between engines | < 2% |
| Fill price MAE | Mean absolute error of fill prices vs backtest fills | Low is better |
| Equity correlation | Pearson correlation between the two equity curves | Close to 1.0 |
| Signal timing match | Percentage of signals that occur on the same bar | High is better |

A failing parity test (> 2% PnL divergence) usually indicates a fill model mismatch, a fee rate discrepancy, or a funding rate included in one engine but not the other.

### Run via the UI

In the Paper Trading page, click the **PARITY TEST** button. Select the strategy, market, and date range. Results display inline.

### Run via the API

```bash
curl -X POST http://localhost:8000/api/v1/backtest/parity \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": "momentum",
    "market": "SOL-PERP",
    "start_ts": 1735689600,
    "end_ts": 1740873600,
    "capital": 10000,
    "fee_rate": 0.0005
  }'
```

### Run via the CLI

```bash
flint parity --strategy momentum --market SOL-PERP --start 2026-01-01 --end 2026-03-01
```

---

## 5. Recommended Progression

Follow this sequence to move from idea to live trading safely:

1. **Backtest** -- Validate the strategy on historical data in BacktestLab. Test across multiple regimes (bull, bear, crash, sideways, high_vol).

2. **Optimize** -- Run Optuna optimization (1-500 trials) to find robust parameters. Use walk-forward validation to check for overfitting.

3. **Walk-forward validation** -- Confirm out-of-sample performance holds across regime boundaries.

4. **Paper trade (1+ week)** -- Deploy to paper trading on the target venue. Monitor equity curve, trade frequency, and drawdown behavior on real market data.

5. **Parity test** -- Run a parity test comparing backtest and paper results on overlapping data. Ensure < 2% PnL divergence.

6. **Devnet / testnet** -- Deploy with real transaction submission on devnet (Drift) or testnet (Hyperliquid, CEX sandboxes). Verify fills, reconnections, and funding-rate ticks over at least a few hours.

7. **Dry-run on mainnet** -- Set `live_network: mainnet` with `live_dry_run: true`. Confirms RPC connectivity and order construction without submitting real orders.

8. **Mainnet with small capital** -- Switch `live_dry_run: false` with conservative position limits and a tight kill switch. Scale up gradually.

### Mainnet checklist

Before switching to `live_network: mainnet` with `live_dry_run: false`:

- [ ] Wallet funded with enough SOL for gas + collateral (minimum 0.1 SOL gas buffer for Drift)
- [ ] Hyperliquid API wallet funded via web UI transfer (if applicable)
- [ ] CEX API keys created with trade-only permissions, no withdrawal access (if applicable)
- [ ] `FLINT_RPC_URL` set to a reliable mainnet endpoint (Helius, QuickNode, or your own)
- [ ] `live_kill_switch_drawdown_pct` set to an acceptable loss threshold
- [ ] `live_per_market_position_limits` sized to your actual capital
- [ ] `live_max_orders_per_minute` reviewed for the strategy's expected signal frequency
- [ ] Parity test passed (< 2% PnL divergence)
- [ ] Devnet/testnet session completed with no unexpected errors
- [ ] Dry-run on mainnet verified (RPC connectivity + order construction)

---

## 6. Multi-Venue Live Execution

To run a strategy across multiple venues simultaneously, Flint uses `MultiVenueLiveContext`. This wraps `LiveDriftContext`, `LiveHyperliquidContext`, and/or `LiveCCXTContext`, maintaining separate margin, position state, and connections per venue.

### Setup

Provide keys for all target venues in your `.env`:

```bash
# Drift
FLINT_PRIVATE_KEY=<solana-keypair>
FLINT_RPC_URL=https://api.mainnet-beta.solana.com

# Hyperliquid
FLINT_HYPERLIQUID_PRIVATE_KEY=<hex-private-key>

# CEX (e.g., Binance)
FLINT_CCXT_EXCHANGE=binance
FLINT_CCXT_API_KEY=<key>
FLINT_CCXT_SECRET=<secret>
FLINT_CCXT_SANDBOX=false
```

### Tick modes

```yaml
live_multi_venue_primary: drift   # which venue's candle closes drive strategy ticks
live_tick_mode: primary           # primary | any
```

| Mode | Behavior | Use case |
|------|----------|----------|
| `primary` | Ticks only on primary venue candle close | Latency-sensitive strategies, single authoritative clock |
| `any` | Ticks on any venue candle close | Cross-venue monitoring, arb detection |

### Routing orders

In your strategy, route orders to specific venues explicitly:

```python
ctx.market_order("SOL-PERP", "long", size, venue="drift")
ctx.market_order("SOL-PERP", "short", size, venue="hyperliquid")
ctx.market_order("BTC-PERP", "long", size, venue="binance")
```

### Position tracking

Positions are keyed by `(venue, market)`. Each venue maintains independent margin and position state.

```python
ctx.venue_balance("drift")          # Cash on Drift
ctx.venue_balance("hyperliquid")    # Cash on Hyperliquid
ctx.venue_balance("binance")        # Cash on Binance
ctx.venue_positions("drift")        # Positions on Drift only
```

`ctx.account` reflects total equity across all venues.

### Paired leg submission

For cross-venue strategies (e.g., funding arb), `MultiVenueLiveContext` supports paired leg submission:

- Both legs are submitted simultaneously
- A configurable timeout waits for both fills
- If one leg fills and the other fails, auto-unwind logic closes the filled leg to prevent unhedged exposure

---

## 7. Monitoring

### Equity and trade monitoring

The LiveMonitor page in the UI shows:

- Real-time equity curves per venue and aggregate
- Open positions with unrealized PnL
- Recent trade log with fill prices and fees
- Risk guard rejection history

### Status endpoint

```bash
curl http://localhost:8000/api/v1/health
```

Returns server status, active live sessions, WebSocket connection health, and store connectivity.

### Alerts

Flint pushes notifications to Telegram and/or Discord when configured:

```yaml
# Telegram
telegram_bot_token: "123456:ABC-your-bot-token"
telegram_chat_id: "-100your-chat-id"

# Discord
discord_webhook_url: "https://discord.com/api/webhooks/..."
```

**Events that fire alerts:**

| Event | Severity |
|-------|----------|
| Fill received (open/close) | Info |
| Order rejected by risk guard | Warning |
| Drawdown warning threshold breached | Warning |
| Kill switch triggered | Critical |
| WebSocket disconnected / reconnected | Info |
| RPC error / transaction retry | Warning |
| Uncaught strategy exception | Critical |
| Paired leg failure / auto-unwind | Critical |

### WebSocket feeds

All live data arrives through venue-specific WebSocket feeds with automatic reconnection:

```
WebSocketFeed (ABC)              -- reconnection logic, health checks, REST fallback
  +-- DriftWebSocketFeed         -- trade streaming + funding rate subscription
  +-- HyperliquidWebSocketFeed   -- candle, L2 book, orderUpdates channels
  +-- PythWebSocketFeed          -- sub-second oracle prices (Hermes)
```

Reconnection uses exponential backoff (1s, 2s, 4s, up to 60s max). On reconnect, missed candles are backfilled from the REST provider. A health check forces reconnect if no message is received for 30 seconds.

Paper trading uses the same WebSocket feeds as live trading -- it ticks on real candle closes, not stored historical candles.
