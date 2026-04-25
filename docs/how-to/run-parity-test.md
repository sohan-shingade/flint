# How to: Run a parity test

A parity test runs the backtest engine and paper broker on the same candles and compares PnL, fill prices, and equity curves. It catches fill-model bugs, data-flow issues, and engine divergence. Mandatory before deploying any strategy to live.

## CLI

```bash
flint parity momentum \
  --market SOL-PERP \
  --start 2025-01-01 --end 2025-03-01 \
  --capital 10000 --fee-rate 0.0005
```

Uses a **built-in** strategy name. For custom code, use the API.

## API

```bash
curl -X POST localhost:8000/api/v1/backtest/parity \
  -H 'Content-Type: application/json' \
  -d '{
    "market": "SOL-PERP",
    "strategy": "momentum",
    "start_ts": 1704067200, "end_ts": 1709251200,
    "capital": 10000, "fee_rate": 0.0005,
    "params": {"lookback": 24}
  }'
```

## Report fields

```
Parity Report — momentum on SOL-PERP (2025-01-01 to 2025-03-01)
────────────────────────────────────────────────────
Backtest PnL:         +$ 1,240
Paper PnL:            +$ 1,215
Divergence:              2.0% ✓
────────────────────────────────────────────────────
Trades:                backtest  paper    diff
                       47        47       0
Fill price MAE:        $0.04
Equity curve corr:     0.998
```

**Pass thresholds:**

- PnL divergence < **2%**
- Trade count difference = 0
- Fill price MAE < $0.10 (market-dependent — Drift OK up to $0.50 due to vAMM)
- Equity correlation > 0.99

## What to do when it fails

| Failure | First look |
|---|---|
| PnL divergence >5% | Fill model setup — `impact_coefficient`, `slippage_bps`, `fill_model` consistent across engines |
| Trade count differs | Timing bug — strategy signal generated on different bars. Check `history` slicing |
| Fill price MAE high | Orderbook snapshots missing in paper but present in backtest, or vice versa |
| Equity corr < 0.95 | Data source differs — paper may be using live candles that don't match stored data |
| Backtest makes money, paper doesn't | Forward-filled funding gap, or latency model disabled in one engine |

## Enforce in CI

```bash
flint parity momentum --start 2025-01-01 --end 2025-03-01 \
  | grep -E 'Divergence: .*\.[0-9]+%' \
  | awk '{if ($2+0 > 2.0) exit 1}'
```

Or use the API response directly:

```python
import httpx
r = httpx.post("http://localhost:8000/api/v1/backtest/parity", json={...}).json()
assert r["divergence_pct"] < 2.0, r
```

## Gotchas

- **Paper engine needs running server** — API / CLI must have `flint serve` up somewhere.
- **Same candle resolution matters** — the parity test enforces this, but if you're comparing ad-hoc results, mismatched resolutions are the #1 source of false divergence.
- **Funding timing** — backtest applies funding hourly from stored rates; paper pulls live rates. Small (<1%) divergence here is expected on cross-venue strategies.

## Related

- [concepts/execution-contexts.md](../concepts/execution-contexts.md) — why parity works at all
- [tutorials/04-paper-to-live.md](../tutorials/04-paper-to-live.md) — where parity fits in the workflow
