# How to: Calibrate slippage from live fills

The default `impact_coefficient` per venue is a rough fit. Calibration tightens it to your actual trading size and market conditions. Do this **after** accumulating ≥100 live or paper fills on a market.

## Check you have enough fills

```bash
curl -s "localhost:8000/api/v1/live/fills?session_id=<paper_or_live_session>" | jq '.fills | length'
# need >= 100 (configurable: calibration_min_fills)
```

## Run calibration

```bash
flint calibrate hyperliquid --market SOL-PERP --lookback 30
```

Reads fills from the last 30 days, fits power-law and sqrt-impact models via 5-fold cross-validation, and writes `venues.hyperliquid.impact_coefficient` to `flint.yaml`.

Dry-run (don't write):

```bash
flint calibrate hyperliquid --lookback 30 --dry-run
```

API: `POST /api/v1/backtest/calibrate` — read-only variant, returns the report without writing.

## Read the report

```
Calibration Report — hyperliquid / SOL-PERP
──────────────────────────────────────
Fills used:                        347
Lookback window:                   30d
Median order notional:            $3,200
Avg slippage observed:           7.2 bps
──────────────────────────────────────
Fit — sqrt impact model:
  k = 0.000421 (CV R² = 0.58)
Fit — power-law:
  k = 0.000512, α = 0.47 (CV R² = 0.62)
──────────────────────────────────────
Current impact_coefficient:     0.01
Recommended:                    0.00042
Drift from current:              95% ↓
```

- **CV R² < 0.5** — weak fit. Data is noisy. Either more fills or a different model.
- **Drift > 15%** (`calibration_drift_threshold_pct`) — always worth re-calibrating.
- **Recommended << default** — expected. Defaults are conservative.

## Verify impact

Run the same backtest with and without calibration:

```bash
# Before
curl -X POST localhost:8000/api/v1/backtest/run \
  -d '{"strategy":"momentum","market":"SOL-PERP","impact_coefficient":0.01,...}'

# After calibration (uses yaml value automatically)
curl -X POST localhost:8000/api/v1/backtest/run \
  -d '{"strategy":"momentum","market":"SOL-PERP",...}'
```

PnL delta is the size of the default-vs-reality gap on this market / size. Often 10–30%.

## When to re-calibrate

- Every 30–60 days
- After a major size change (you went from $1k to $10k orders)
- After market regime shifts (crash, bull run — liquidity profile changes)
- When paper vs live PnL divergence exceeds 20%

## Gotchas

- **Per market, per venue.** SOL-PERP and BTC-PERP have different `k`. Calibrate each market you trade.
- **Per size regime.** Calibration fits your recent fill sizes. Running a strategy at 10× the calibrated size extrapolates poorly.
- **Live + paper fills both count** — paper fills are recorded identically. Useful when live fill history is thin.

## Related

- [concepts/fill-pipeline.md](../concepts/fill-pipeline.md) — how the coefficient is used
- [reference/venue-configs.md](../reference/venue-configs.md) — per-venue defaults
