# Live Deployment Guide

This guide covers deploying a Flint strategy to a live venue — starting with dry-run and devnet validation, then mainnet.

---

## 1. Drift Setup

Drift uses a Solana keypair for signing transactions on-chain.

**Environment variables:**

```bash
FLINT_PRIVATE_KEY=<base58-encoded-solana-keypair>
FLINT_RPC_URL=https://api.mainnet-beta.solana.com
```

`FLINT_PRIVATE_KEY` must be the base58 representation of a 64-byte Solana keypair (the format output by `solana-keygen new`). Never use a hardware wallet address here — Flint needs the raw private key to sign transactions.

**flint.yaml:**

```yaml
live_network: devnet   # devnet | mainnet
```

Use `devnet` while testing. Switch to `mainnet` only after completing the mainnet checklist in §6.

**Getting test SOL on devnet:**

```bash
solana airdrop 2 <your-wallet-address> --url devnet
```

Or use the [Solana devnet faucet](https://solfaucet.com).

---

## 2. Hyperliquid Setup

Hyperliquid uses an Ethereum-style private key (hex, without the `0x` prefix) for EIP-712 order signing.

**Environment variable:**

```bash
FLINT_HYPERLIQUID_PRIVATE_KEY=<64-char-hex-private-key>
```

**Recommended: use an API wallet.** Hyperliquid supports trade-only API wallets that cannot initiate withdrawals. Create one at [app.hyperliquid.xyz](https://app.hyperliquid.xyz) → Settings → API Wallets. Fund it by transferring from your main account via the Hyperliquid web UI.

Withdrawals must always be initiated through the Hyperliquid web UI — Flint does not implement withdrawal logic.

**flint.yaml for Hyperliquid testnet:**

```yaml
live_network: devnet   # uses Hyperliquid testnet when devnet is set
```

---

## 3. Risk Configuration

All live risk settings live under the top-level keys in `flint.yaml`. They apply across all venues unless overridden.

```yaml
# Kill switch — flatten all positions immediately if equity drops this much from peak
live_kill_switch_drawdown_pct: 0.15

# Early warning before kill switch fires
live_drawdown_warning_pct: 0.075

# Rate limiter — prevents runaway strategy loops
live_max_orders_per_minute: 30

# Per-market notional position caps (USD)
live_per_market_position_limits: '{"SOL-PERP": 10000, "BTC-PERP": 50000}'

# Dry-run mode — logs all intended orders but submits nothing
live_dry_run: true
```

**Kill switch behavior**: When `live_kill_switch_drawdown_pct` is breached, `EquityMonitor` cancels all open orders and closes all positions immediately. The live session will not restart automatically — you must restart `flint live` manually after investigating.

**Warning threshold**: At `live_drawdown_warning_pct` (half the kill threshold by default), an alert fires but trading continues.

---

## 4. Dry-Run Testing

Dry-run mode runs the full live pipeline — WebSocket feeds, strategy ticks, order construction, risk checks — but does not submit transactions. Every intended order is logged with `tx_sig="DRY_RUN"`.

Enable in `flint.yaml`:

```yaml
live_dry_run: true
```

Or pass on the CLI:

```bash
flint live --strategy momentum --market SOL-PERP --dry-run
```

Dry-run fills are simulated at the mid-price with the configured fee rate. Check the logs to confirm the strategy is generating signals at the expected frequency before switching to real orders.

---

## 5. Devnet Testing

Always test on devnet before mainnet. Devnet has the same code path — the only difference is the RPC URL and that transactions are not real.

```yaml
live_network: devnet
live_dry_run: false   # test real tx submission on devnet
```

Steps:
1. Fund a devnet wallet via `solana airdrop`.
2. Start the live session: `flint live --strategy <name> --market SOL-PERP`.
3. Monitor logs and the Flint dashboard for fills, errors, and kill switch events.
4. Run for at least a few hours to catch reconnection, funding-rate ticks, and fill confirmation edge cases.

---

## 6. Mainnet Checklist

Work through this before switching `live_network: mainnet`:

- [ ] Wallet funded with enough SOL for gas + collateral (recommend 0.1 SOL gas buffer minimum)
- [ ] `FLINT_RPC_URL` set to a reliable mainnet endpoint (Helius, QuickNode, or your own validator)
- [ ] `live_kill_switch_drawdown_pct` set to an acceptable loss threshold
- [ ] `live_per_market_position_limits` sized to your actual capital
- [ ] `live_max_orders_per_minute` reviewed for the strategy's expected signal frequency
- [ ] At least one alert channel configured (see §7)
- [ ] Dry-run on mainnet verified (`live_dry_run: true`, `live_network: mainnet`) — confirms RPC connectivity and order construction without submitting
- [ ] Devnet test session completed with no unexpected errors
- [ ] Parity test passed (see §9)

Switch to mainnet:

```yaml
live_network: mainnet
live_dry_run: false
```

---

## 7. Monitoring and Alerting

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

Create a webhook under your Discord server → channel settings → Integrations.

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

---

## 8. Multi-Venue Live Execution

To run a strategy across Drift and Hyperliquid simultaneously, use `MultiVenueLiveContext`.

**flint.yaml:**

```yaml
live_multi_venue_primary: drift   # which venue's candle closes drive strategy ticks
```

**Tick modes:**

- `primary` — strategy ticks only when the primary venue publishes a new candle. Use for latency-sensitive strategies where you want a single authoritative clock.
- `any` — strategy ticks whenever any venue publishes a candle. Use for cross-venue monitoring strategies.

Configure in `flint.yaml`:

```yaml
live_tick_mode: primary   # primary | any
```

In your strategy, route orders by venue explicitly:

```python
ctx.market_order("SOL-PERP", "buy", size, venue="drift")
ctx.market_order("SOL-PERP", "sell", size, venue="hyperliquid")
```

`MultiVenueLiveContext` maintains separate margin, position state, and WebSocket connections per venue. The aggregated `ctx.account` reflects total equity across all venues.

---

## 9. Parity Test

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

The parity report is also available via API:

```bash
curl -X POST http://localhost:8000/api/v1/backtest/parity \
  -H 'Content-Type: application/json' \
  -d '{"strategy": "momentum", "market": "SOL-PERP", "start": "2026-01-01", "end": "2026-03-01"}'
```
