# How to: Compare backtests

Side-by-side comparison of multiple runs — same strategy different params, different strategies, different regimes.

## In-memory (active session)

```bash
curl -s "localhost:8000/api/v1/backtest/compare?ids=$ID1,$ID2,$ID3" | jq .
```

Returns metrics + equity curves for each run. Runs expire after 1h / 200-entry cap.

## From the journal (persisted)

```bash
# List
curl -s "localhost:8000/api/v1/journal/runs?limit=20" | jq .

# Compare
curl -s "localhost:8000/api/v1/journal/compare?ids=run_a,run_b,run_c" | jq .
```

The UI's Journal page has a **Compare** button with a multi-select.

## From the CLI / MCP

**MCP:**

```
compare_runs("id1,id2,id3")
```

**CLI:** no dedicated compare command; use the API or UI.

## Python

```python
import httpx
r = httpx.get("http://localhost:8000/api/v1/journal/compare",
              params={"ids": "run_a,run_b,run_c"}).json()

for c in r["comparisons"]:
    print(f"{c['strategy']}: Sharpe {c['metrics']['sharpe_ratio']:.2f}, "
          f"PnL ${c['metrics']['total_pnl']:.0f}, DD {c['metrics']['max_drawdown']:.1%}")
```

## What to compare

- **Multiple parameter sets from the same optimization.** Top 5 trials — check that they cluster near a real optimum (not scattered).
- **Same strategy across regimes.** Consistency test. See [concepts/regimes.md](../concepts/regimes.md).
- **Different strategies on the same market.** Cheap benchmark — if your custom one doesn't beat `momentum` on the same data, why deploy it?
- **Calibrated vs uncalibrated impact.** Post-calibration runs should show lower PnL but higher paper/backtest parity. See [calibrate-slippage.md](calibrate-slippage.md).

## What to look at

| Column | Why |
|---|---|
| Sharpe | Risk-adjusted return |
| Max drawdown | Worst-case equity drop |
| Profit factor | Win quality |
| Turnover | Cost sensitivity |
| Trade count | Statistical significance |
| Monte Carlo P5 | Luck-adjusted worst case |

Don't rank by PnL alone — high Sharpe + low PnL often beats high PnL + high variance.

## Related

- [reference/metrics.md](../reference/metrics.md) — what each metric means
- [reference/rest-api.md#journal](../reference/rest-api.md#journal) — journal endpoints
