# Funding Rate Mean Reversion (`FundingMeanReversionStrategy`)

Mean-reversion strategy that applies Bollinger Bands to hourly funding rates and trades the extremes.

## How It Works

- Fetches the last `bb_lookback` hourly funding rate observations via `ctx.get_funding_rates()`.
- Computes a simple Bollinger Band: mean ± (`bb_std` × standard deviation).
- **Entry conditions:**
  - Rate below the lower band → go **long** (funding is extremely negative; expect it to revert upward).
  - Rate above the upper band → go **short** (funding is extremely positive; expect it to revert downward).
- **Exit conditions:**
  - Rate crosses back through the midline (mean) — the mean-reversion has completed.
  - Position held for longer than `max_hold_hours` — time-based stop.
- Position size is `position_size_pct` of current account equity at entry.

## Parameters

| Parameter | Type | Default | Range | Description |
|---|---|---|---|---|
| `bb_lookback` | int | 24 | 6–72 | Number of hourly funding samples for the band |
| `bb_std` | float | 2.0 | 1.0–4.0 | Band width in standard deviations |
| `max_hold_hours` | int | 12 | 1–48 | Maximum hold time before forced exit |
| `position_size_pct` | float | 0.5 | 0.1–1.0 | Fraction of equity to risk per trade |
| `candle_resolution_s` | int | 3600 | 60–3600 | Candle granularity (seconds) |

## Backtest Example

```bash
flint backtest \
  --strategy funding_mean_reversion \
  --market SOL-PERP \
  --start 2024-01-01 \
  --end 2024-09-30 \
  --capital 10000 \
  --param bb_lookback=24 \
  --param bb_std=2.0 \
  --param max_hold_hours=12
```

## Known Limitations

- Requires `ctx` to implement `get_funding_rates()` — returns HOLD in plain v1 signal-only backtests.
- Funding rate distribution is heavy-tailed; standard Bollinger Bands assume near-normality and may produce too many signals during trending funding regimes.
- Holds only one position at a time — cannot stack entries if funding moves further against the band.
- No size scaling relative to band width; entries at 3σ are the same size as entries at 2σ.

## Venues

Works on any market with hourly funding data in FlintStore. Best results on **Drift** and **Hyperliquid** perp markets where multiple months of funding history are available.
