# Momentum Breakout (`MomentumBreakoutStrategy`)

Enters on N-bar price channel breakouts and optionally confirms direction using the Pyth oracle price.

## How It Works

- Tracks the highest high and lowest low over the last `breakout_lookback` candles.
- **BUY signal**: current close exceeds the channel high (new N-bar high breakout).
- **SELL signal**: current close falls below the channel low (new N-bar low breakdown).
- **Oracle confirmation** (when `oracle_confirmation=1`): calls `ctx.get_oracle_price()` before signalling.
  - BUY blocked if the oracle price is below the candle close — oracle disagrees with the bullish move.
  - SELL blocked if the oracle price is above the candle close — oracle disagrees with the bearish move.
- A trailing stop (`trailing_stop_pct`) can be configured but is applied by the execution layer, not the signal logic.

## Parameters

| Parameter | Type | Default | Range | Description |
|---|---|---|---|---|
| `breakout_lookback` | int | 20 | 5–100 | Number of candles for the price channel |
| `trailing_stop_pct` | float | 0.02 | 0.005–0.10 | Trailing stop as a fraction of entry price |
| `oracle_confirmation` | int | 1 | 0–1 | 1 = require oracle agreement; 0 = disable |
| `candle_resolution_s` | int | 3600 | 60–86400 | Candle granularity (seconds) |

## Backtest Example

```bash
flint backtest \
  --strategy momentum_breakout \
  --market SOL-PERP \
  --start 2024-01-01 \
  --end 2024-09-30 \
  --capital 10000 \
  --param breakout_lookback=20 \
  --param trailing_stop_pct=0.02 \
  --param oracle_confirmation=1
```

## Known Limitations

- Oracle confirmation is a best-effort filter — if `ctx.get_oracle_price()` returns `None` (e.g. in a plain backtest without oracle data), the check is skipped and the signal fires anyway.
- Does not account for volume: a breakout on thin volume is treated identically to one on heavy volume.
- Trailing stop is a hint to the execution layer; actual stop placement depends on the context implementation.
- High `breakout_lookback` values reduce signal frequency significantly on shorter timeframes.

## Venues

Single-venue. Works on any Drift or Hyperliquid perp market. Oracle confirmation is most useful on **Drift** where Pyth oracle prices are available via `DriftWebSocketFeed` or the Pyth REST API.
