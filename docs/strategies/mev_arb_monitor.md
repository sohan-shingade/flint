# MEV Arb Monitor (`MevArbMonitor`)

Monitoring-only strategy that scans for cross-DEX arbitrage opportunities on Solana and logs them.

## How It Works

- On every candle tick, calls `ctx.get_arb_routes(market, max_hops=N)` if the method is available on the context.
- Filters routes where `profit_bps` meets or exceeds `min_profit_bps`.
- Logs qualifying opportunities at DEBUG level (and optionally WARNING when `alert_enabled=1`).
- **Never places any orders** — always returns `Signal.HOLD`.
- Tracks a running count of opportunities found (`strategy._opportunities_found`) for inspection.

## Parameters

| Parameter | Type | Default | Range | Description |
|---|---|---|---|---|
| `min_profit_bps` | float | 10.0 | 1–100 | Minimum profit threshold to log an opportunity |
| `max_hops` | int | 3 | 1–5 | Maximum swap hops to consider in a route |
| `alert_enabled` | int | 0 | 0–1 | 1 = emit WARNING log for qualifying routes |
| `candle_resolution_s` | int | 60 | 1–3600 | Candle granularity (seconds) |

## Backtest Example

```bash
flint backtest \
  --strategy mev_arb_monitor \
  --market SOL-USDC \
  --start 2024-01-01 \
  --end 2024-03-31 \
  --capital 10000 \
  --param min_profit_bps=5.0 \
  --param max_hops=3 \
  --param alert_enabled=1
```

## Known Limitations

- No trades are placed — PnL in backtest results will always be zero. Use this strategy to measure opportunity frequency, not returns.
- Route data depends on `ctx.get_arb_routes()` being implemented; standard `BacktestContext` does not provide this — integrate with `MevArbScanner` or a custom context.
- Profit estimates do not account for Solana priority fees, Jito tips, or slippage on execution; actual captured profit will be lower.
- High `max_hops` values significantly increase route enumeration time in live mode.

## Venues

Designed for **Raydium** and **Orca** pools on Solana. Pool price data must be present in FlintStore (`pool_snapshots` table) for accurate offline scanning. For live scanning, run against a context wired to the MEV arb scanner (`flint/mev/`).
