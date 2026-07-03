# Funding Rate Arbitrage (`FundingArbStrategy`)

Exploits funding rate divergence between two or more perpetual venues in a delta-neutral manner.

## How It Works

- Polls the latest funding rate for each configured venue via `ctx.get_funding_by_venue()`.
- When the spread between the highest and lowest rate exceeds `min_spread_bps`, opens a delta-neutral pair:
  - **Long** on the venue with the lower (or negative) funding rate — you receive funding.
  - **Short** on the venue with the higher (or positive) funding rate — you pay less than you receive.
- A `min_spread_duration` guard prevents entering on transient spikes — the spread must persist for at least that many hours.
- Exits when the spread converges below `exit_spread_bps` or when `max_hold_hours` elapses.

## Parameters

| Parameter | Type | Default | Range | Description |
|---|---|---|---|---|
| `min_spread_bps` | float | 5.0 | 3–20 | Minimum funding spread (bps/hr) to open a position |
| `exit_spread_bps` | float | 1.0 | 0.5–5 | Spread below which the position is closed |
| `max_hold_hours` | int | 24 | 4–72 | Maximum hold time before forced exit |
| `position_size_usd` | float | 1000.0 | 100–10000 | Notional USD per leg |
| `min_spread_duration` | int | 1 | 1–6 | Hours spread must persist before entry |
| `candle_resolution_s` | int | 60 | 60–3600 | Candle granularity (seconds) |

## Backtest Example

```bash
flint backtest \
  --strategy funding_arb \
  --market SOL-PERP \
  --start 2024-01-01 \
  --end 2024-06-30 \
  --capital 10000 \
  --param min_spread_bps=5.0 \
  --param max_hold_hours=24
```

## Known Limitations

- Requires funding data from at least 2 venues — returns HOLD if only one venue has data.
- Stale data guard (2-hour window) can cause missed entries if a venue's feed lags.
- Does not model the transaction cost of opening/closing two legs simultaneously.
- Assumes instantaneous fills at market price on both legs; in practice one leg may move before the other fills.

## Venues

Built around **Hyperliquid** (the live venue) paired with any second venue that has funding-rate data in FlintStore — a CEX reference today, Phoenix / Jupiter spot as those connectors land. Substitute via the `venues` constructor parameter.
