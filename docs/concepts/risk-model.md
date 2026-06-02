# Risk Model

How Flint stops a strategy from blowing up. Two layers, both active in backtest, paper, and live.

## Layer 1 — Risk guards (`RiskManager`)

`flint/risk/guards.py`. Every order flows through a chain of guards before reaching `FillPipeline`. Any guard can reject the order.

| Guard | Behavior | Default |
|---|---|---|
| `MaxPositionSize` | Reject if cumulative USD notional on a market exceeds cap | per-market cap required |
| `MaxOpenPositions` | Reject new entries when too many positions open; doesn't block closes | `max_open_trades` in config |
| `MaxDrawdownCircuitBreaker` | Reject all orders once cumulative drawdown exceeds threshold; **latches** until manually reset | `max_drawdown_pct` |
| `DailyLossLimit` | Reject orders after daily loss exceeds USD threshold; auto-resets at day boundary | — |
| `MaxOrdersPerMinute` | Sliding-window rate limiter | 30/min in live |
| `PerMarketPositionLimit` | Hard USD cap per market from JSON config | `live_per_market_position_limits` |

Guards are **composable** — you pass a list to `RiskManager(guards=[...])`. The built-in factory `default_guards(config)` wires the common set.

Rejected orders do not fill, do not pay fees, do not enter margin. They log a warning into `BacktestResult.strategy_warnings`.

## Layer 2 — Equity monitor (`EquityMonitor`)

`flint/risk/monitor.py`. A continuous loop that runs alongside paper and live sessions. Checks every `live_kill_switch_check_interval_s` (default 5s):

- If equity dropped `live_drawdown_warning_pct` (default 7.5%) from peak → fire warning alert.
- If equity dropped `live_kill_switch_drawdown_pct` (default 15%) from peak → **kill switch**:
  1. Cancel all open orders across all venues.
  2. Close all positions with reduce-only market orders.
  3. Fire a critical alert (Telegram / Discord / webhook / all of the above).
  4. Halt the session. Manual restart required.

The kill switch is deliberately aggressive. Missing a crash by 2% is cheap; missing it by 20% is not. Tune `live_kill_switch_drawdown_pct` down, not up.

## Interplay

Guards stop orders from *entering*. EquityMonitor cleans up after the fact. You need both:

- A guard can't catch a position that's already open and losing. That's EquityMonitor's job.
- EquityMonitor can't stop a strategy from opening bad positions in the first place. That's the guards' job.

## Config surface

All in `flint.yaml` (or `FLINT_*` env):

```yaml
risk:
  max_drawdown_pct: 0.20
  default_stop_loss_pct: 0.05
  max_open_trades: 5

live:
  dry_run: false
  kill_switch_drawdown_pct: 0.15
  kill_switch_check_interval_s: 5.0
  max_orders_per_minute: 30
  per_market_position_limits: '{"SOL-PERP": 50000, "BTC-PERP": 100000}'
  drawdown_warning_pct: 0.075
```

Paper sessions take a per-session `risk_config` via `POST /paper/start` or `POST /paper/{session_id}/risk`:

```json
{
  "max_drawdown_pct": 0.15,
  "daily_loss_limit": 500.0,
  "max_position_pct": 0.95,
  "liquidation_enabled": true
}
```

## Notifications

Alerts fanned out to configured channels. Zero configured → stderr only.

| Channel | Config key |
|---|---|
| Telegram | `telegram_bot_token` + `telegram_chat_id` |
| Discord | `discord_webhook_url` |
| Generic | `webhook_url` |

## Dry-run mode

`live_dry_run: true` (or `FLINT_LIVE_DRY_RUN=1`) — log orders with full metadata but don't submit them. Guards + margin engine still run. Use this for the final sanity check before pulling the dry-run flag on mainnet.

## Devnet / testnet

All `Live*Context` subclasses default to devnet / testnet. Switching to mainnet requires:

- `live_network: mainnet` in `flint.yaml` **and**
- explicit CLI flag `--network mainnet`

This is a deliberate two-step gate. If you only set one, the session refuses to start.

## What the risk model doesn't cover

- **Logical bugs.** If your strategy wants to open infinite positions at the same price, guards can slow it but can't "fix" it. Write tests.
- **Venue rejections.** Leverage hit, insufficient collateral, self-trade prevention — the venue enforces these. Guards are a client-side courtesy.
- **Counterparty risk.** HL vault drain, CEX collapse, venue insolvency. Nothing here prevents you from losing everything on a venue failure.
- **Correlation risk across markets.** `MaxOpenPositions=5` doesn't protect you from 5 highly-correlated positions. Add your own correlation guard if you trade many markets.

## See also

- [validation/safety-rails.md](../validation/safety-rails.md) — longer form with failure scenarios
- [reference/config.md](../reference/config.md) — all `live_*` + `risk_*` fields
- [how-to/configure-risk-guards.md](../how-to/configure-risk-guards.md)
