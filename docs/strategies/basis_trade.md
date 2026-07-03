# Basis Trade (`BasisTradeStrategy`)

Cross-venue basis arbitrage that longs the cheaper venue and shorts the more expensive one when price divergence exceeds a threshold.

## How It Works

- Retrieves venue-tagged candles via `ctx.get_candles(market)` — each `Candle` carries a `venue` attribute.
- Computes the latest close price per venue and calculates the basis spread in basis points.
- **Entry**: when the best spread across any pair of configured venues exceeds `entry_basis_bps`:
  - **Long** on the cheaper venue.
  - **Short** on the more expensive venue.
  - Equal notional size (`position_size_usd`) on each leg — delta neutral.
- **Exit** when:
  - The basis converges below `exit_basis_bps` — the trade has paid out.
  - `max_hold_hours` elapses — time-based stop.

## Parameters

| Parameter | Type | Default | Range | Description |
|---|---|---|---|---|
| `entry_basis_bps` | float | 30.0 | 5–200 | Minimum price divergence (bps) to open a position |
| `exit_basis_bps` | float | 5.0 | 0.5–50 | Basis below which the position is closed |
| `max_hold_hours` | int | 12 | 1–72 | Maximum hold time before forced exit |
| `position_size_usd` | float | 1000.0 | 100–50000 | Notional USD per leg |
| `candle_resolution_s` | int | 3600 | 60–3600 | Candle granularity (seconds) |

## Backtest Example

```bash
flint backtest \
  --strategy basis_trade \
  --markets hyperliquid:SOL-PERP,binance:SOL-PERP \
  --start 2024-01-01 \
  --end 2024-09-30 \
  --capital 20000 \
  --param entry_basis_bps=30.0 \
  --param exit_basis_bps=5.0 \
  --param position_size_usd=2000
```

## Known Limitations

- Requires at least 2 venues with candle data for the same market — returns HOLD otherwise.
- The venue-price map uses only the latest candle per venue; latency differences between venues may cause stale comparisons in live mode.
- Does not account for venue-specific fees or funding accruing on open positions; net capture is lower than gross basis.
- Assumes simultaneous fills on both legs; partial-fill risk is not modeled in backtest.

## Venues

Default venue: **Hyperliquid** (live). Any two venues whose candles are tagged with `venue` in FlintStore can be paired — e.g. a CEX reference like Binance, or Phoenix / Jupiter spot as those connectors land. Pass `venues=["hyperliquid", "binance"]` or similar to the constructor to override.
