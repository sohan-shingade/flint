# Market Regimes

Flint ships with 8 curated market regimes. A strategy that only works in one regime is overfit; regime testing surfaces that in one run.

## The 8 regimes

Source: `flint/regimes.py`. The UI keeps a synced copy in `ui/src/constants/regimes.ts`.

| Regime | Type | Period | Description |
|---|---|---|---|
| Pre-ETF Consolidation | sideways | Dec 2023 – Jan 2024 | Low volatility, awaiting ETF catalyst |
| ETF Bull Run | bull | Jan 2024 – May 2024 | BTC spot ETF approval drives +70% rally |
| Summer Correction | bear | Jun 2024 – Aug 2024 | Profit-taking, BTC −12% |
| Recovery Rally | bull | Sep 2024 – Dec 2024 | Re-acceleration to new ATHs |
| Peak & Distribution | high_vol | Jan 2025 – Mar 2025 | Topping pattern with funding spikes |
| Extended Decline | bear | Apr 2025 – Sep 2025 | Slow grind lower |
| Crash Phase 1 | crash | Oct 2025 – Dec 2025 | Accelerating sell-off, capitulation |
| Crash Phase 2 | crash | Jan 2026 – Apr 2026 | Continued decline, dead-cat bounces |

Regime types: `bull`, `bear`, `sideways`, `high_vol`, `crash`.

## How to use them

### UI

In BacktestLab, open the **Regimes** panel → select multiple → run. Flint runs the strategy on each regime in parallel and shows a matrix of metrics.

### API

```bash
curl -s localhost:8000/api/v1/backtest/regimes | jq .
```

```bash
curl -s -X POST localhost:8000/api/v1/backtest/run-regimes \
  -H 'Content-Type: application/json' \
  -d '{
    "regime_ids": ["etf_bull_run", "summer_correction", "extended_decline"],
    "code": "<strategy code>",
    "market": "SOL-PERP",
    "resolution_s": 3600,
    "initial_capital": 10000,
    "fee_rate": 0.0005
  }'
```

Response returns a run ID; poll for results.

### Python

```python
from flint.regimes import REGIMES, get_regime

r = get_regime("etf_bull_run")
candles = store.query_candles("SOL-PERP", 3600, r.start_ts, r.end_ts)
```

## Reading regime results

- **Consistent Sharpe across regimes** (say, 0.8–1.4 everywhere) — robust. Boring is good.
- **Huge Sharpe in bulls, negative in crashes** — the strategy is long-biased. Fine if you can detect the regime live; fragile if you can't.
- **Positive total PnL but one regime drags 40% drawdown** — single-scenario risk. Don't deploy without additional filters.
- **All-positive on a short list of regimes and the strategy uses indicators tuned within those windows** — overfit. Expand the regime set or add walk-forward.

## Regime != prediction

Regimes label *past* windows. They don't tell you what regime you're in now. Strategies that depend on regime detection (e.g. "trend-follow in bulls, mean-revert in sideways") need a live classifier, which is another modeling problem. The safer approach is to write strategies that survive across regimes.

## Adding a regime

Edit `flint/regimes.py` + `ui/src/constants/regimes.ts`. Flint reads both from disk at startup; no DB migration needed.

```python
Regime(
    id="my_regime",
    name="Custom Period",
    start_ts=..., end_ts=...,
    type="high_vol",
    description="Whatever you need to test",
)
```

## See also

- [reference/rest-api.md#backtest](../reference/rest-api.md#backtest) — `run-regimes` endpoint
- [tutorials/03-optimize-walk-forward.md](../tutorials/03-optimize-walk-forward.md) — combine regime testing with walk-forward
