# Venue Configs Reference

Per-venue fee / margin / latency presets used by the backtest engine, paper broker, and live execution contexts. Source: `flint/execution/venue_config.py`.

## Schema (`VenueConfig`)

| Field | Type | Notes |
|---|---|---|
| `name` | str | Venue key |
| `taker_fee_bps` | float | Basis points; negative = rebate |
| `maker_fee_bps` | float | " |
| `initial_margin` | float | Min margin ratio to open (e.g. 0.10 = 10× max leverage) |
| `maintenance_margin` | float | Liquidation threshold |
| `max_leverage` | float | Hard cap |
| `liquidation_penalty` | float | Fraction of notional charged on liquidation |
| `impact_coefficient` | float | Sqrt-model `k` factor; calibrated per venue |
| `base_latency_s` | float | Execution delay |
| `latency_jitter_s` | float | ± jitter |

## Defaults

| Venue | Taker | Maker | Init margin | Maint margin | Max lev | Impact k | Latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| **drift** | 10 bps | −2 bps | 10% | 5% | 10× | 0.010 | 8.0 ± 5.0 s |
| **hyperliquid** | 3.5 bps | 1 bp | 5% | 2.5% | 20× | 0.005 | 1.0 ± 0.5 s |
| **binance** | 4.5 bps | 2 bps | 2% | 1% | 50× | 0.002 | 0.2 ± 0.1 s |
| **okx** | 5 bps | 2 bps | 2% | 1% | 50× | 0.003 | 0.3 ± 0.15 s |
| **bybit** | 5.5 bps | 2 bps | 2% | 1% | 50× | 0.003 | 0.3 ± 0.15 s |
| **dydx** | 5 bps | 1 bp | 5% | 3% | 20× | 0.006 | 2.0 ± 1.0 s |
| **jupiter** | 6 bps | 6 bps | 1% | 0.2% | 100× | 0.030 | 12.0 ± 8.0 s |
| **coinbase** | 6 bps | 4 bps | 10% | 5% | 10× | 0.003 | 0.15 ± 0.05 s |
| **kraken** | 4 bps | 1.6 bps | 2% | 1% | 50× | 0.004 | 0.3 ± 0.1 s |
| **kucoin** | 6 bps | 2 bps | 2% | 1% | 50× | 0.004 | 0.3 ± 0.15 s |
| **gate** | 7.5 bps | 3.5 bps | 2% | 1% | 50× | 0.005 | 0.3 ± 0.15 s |
| **bitget** | 6 bps | 2 bps | 2% | 1% | 50× | 0.003 | 0.3 ± 0.15 s |
| **mexc** | 0 bps | 0 bps | 2% | 1% | 50× | 0.006 | 0.3 ± 0.15 s |
| **htx** | 5 bps | 2 bps | 2% | 1% | 50× | 0.005 | 0.3 ± 0.15 s |
| **default** | 5 bps | 0 bps | 10% | 5% | 10× | 0.005 | 1.0 ± 0.5 s |

Cross-venue cost example: a $10k SOL-PERP taker trade costs ~$10 on Drift, ~$3.50 on Hyperliquid, ~$4.50 on Binance. Backtests reflect this automatically when `venue=` is set on orders.

## Overriding

In `flint.yaml`:

```yaml
venues:
  drift:
    impact_coefficient: 0.00042    # post-calibration
    taker_fee_bps: 8.0             # different fee tier
  hyperliquid:
    base_latency_s: 1.5
```

Overrides merge into the defaults at load time (`load_venue_configs(yaml_config)`).

## Programmatic access

```python
from flint.execution.venue_config import get_venue_config, VENUE_DEFAULTS

cfg = get_venue_config("drift")    # falls back to "default" if unknown
print(cfg.taker_fee_rate)           # = 0.001 = 10bps

for venue, c in VENUE_DEFAULTS.items():
    print(venue, c.max_leverage)
```

## Calibration

Impact coefficients are approximations. After accumulating live fills you can fit a venue-specific `k`:

```bash
flint calibrate drift --market SOL-PERP --lookback 30
```

Writes the result to `flint.yaml` under `venues.drift.impact_coefficient`. See [how-to/calibrate-slippage.md](../how-to/calibrate-slippage.md).

## See also

- [concepts/fill-pipeline.md](../concepts/fill-pipeline.md) — how these values feed the 4-tier model
- [concepts/margin-capital.md](../concepts/margin-capital.md) — margin engine behavior
- [config.md](config.md) — `live_*` safety overrides
