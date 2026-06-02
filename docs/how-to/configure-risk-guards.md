# How to: Configure risk guards

Two places to set limits: global defaults in `flint.yaml` + per-session `risk_config`.

## Global defaults (`flint.yaml`)

```yaml
risk:
  max_drawdown_pct: 0.20
  default_stop_loss_pct: 0.05
  max_open_trades: 5

live:
  dry_run: false
  kill_switch_drawdown_pct: 0.15
  kill_switch_check_interval_s: 5.0
  drawdown_warning_pct: 0.075
  max_orders_per_minute: 30
  per_market_position_limits: '{"SOL-PERP": 50000, "BTC-PERP": 100000}'
```

Full field list: [reference/config.md](../reference/config.md).

## Per-session override (paper / live)

`POST /api/v1/paper/start`:

```json
{
  "strategy": "momentum",
  "market": "SOL-PERP",
  "initial_capital": 10000,
  "venue": "hyperliquid",
  "risk_config": {
    "max_drawdown_pct": 0.10,
    "daily_loss_limit": 300,
    "max_position_pct": 0.8,
    "liquidation_enabled": true
  }
}
```

Update a running session:

```bash
curl -X POST localhost:8000/api/v1/paper/$SESSION_ID/risk \
  -d '{"max_drawdown_pct": 0.08}'
```

## Starting values for live

First deployment on mainnet — tight:

```yaml
live:
  kill_switch_drawdown_pct: 0.08        # half the default
  max_orders_per_minute: 10
  per_market_position_limits: '{"SOL-PERP": 1000}'   # 1k notional
```

Loosen only after a week of clean live performance.

## Which guard catches what

| Scenario | Guard |
|---|---|
| Strategy tries to open 50 positions in a minute | `MaxOrdersPerMinute` |
| Strategy tries to put 90% of capital in one market | `PerMarketPositionLimit` |
| Strategy wants to open 10th simultaneous position | `MaxOpenPositions` |
| Equity dropped 20% from peak | `MaxDrawdownCircuitBreaker` (latches) |
| Equity dropped $500 today | `DailyLossLimit` (resets at UTC midnight) |
| Equity dropped 15% from peak (live) | `EquityMonitor` kill switch (not a guard) |

Guards run synchronously on every order. `EquityMonitor` runs in a background loop (interval: `live_kill_switch_check_interval_s`).

## Dry-run before mainnet

```yaml
live:
  dry_run: true
```

Orders are logged with full metadata but not submitted. Guards + margin engine still run. Use this for the final pre-launch check.

## Notifications

Set at least one channel so you know when guards fire or the kill switch trips:

```yaml
notifications:
  telegram_bot_token: "${TG_BOT}"
  telegram_chat_id: "${TG_CHAT}"
  discord_webhook_url: "${DISCORD_HOOK}"
```

## Gotchas

- **`MaxDrawdownCircuitBreaker` latches.** Once tripped, it rejects **all** orders — including closes. Manual reset required (stop + restart the session). This is intentional.
- **`DailyLossLimit` resets at UTC midnight**, not your local timezone.
- **`PerMarketPositionLimit` is USD notional**, not base asset. Track mark price when setting the cap.
- **Kill switch runs every 5s by default.** If drawdown happens inside that window, the guard won't catch it. Tighten `live_kill_switch_check_interval_s` for latency-sensitive strategies (at the cost of CPU).

## Related

- [concepts/risk-model.md](../concepts/risk-model.md) — how guards + monitor compose
- [validation/safety-rails.md](../validation/safety-rails.md) — failure scenarios walked through
- [reference/config.md](../reference/config.md) — full field schema
