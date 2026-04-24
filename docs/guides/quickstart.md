# Quickstart

Minimal path from `pip install` to first backtest. For the longer walkthrough with metrics interpretation, see [Tutorial 1](../tutorials/01-install-first-backtest.md). For every other topic, see the [Documentation Index](../README.md).

## Install

```bash
git clone https://github.com/sohan-shingade/flint.git && cd flint
pip install -e .
flint init                    # scaffold + download sample data + demo backtest
flint serve                   # API + UI at localhost:8000
```

## Download data

```bash
flint data download --market SOL-PERP --days 180
```

Free, no API keys. Funding rates from 7 venues auto-fetch for any `-PERP` market.

## Run a backtest

```bash
curl -sX POST localhost:8000/api/v1/backtest/run \
  -H 'Content-Type: application/json' \
  -d '{"strategy":"momentum","market":"SOL-PERP",
       "start_ts":1727740800,"end_ts":1743465600,
       "initial_capital":10000,"fee_rate":0.0006}' | jq
```

The response returns a `id`; poll `/api/v1/backtest/{id}/results` until `status == "complete"`.

Or use the UI — BacktestLab → **Momentum** → pick a date range → **Run**.

## Key metrics

- **Sharpe** — risk-adjusted return. `>1.0` interesting, `>2.0` suspicious.
- **Max drawdown** — worst peak-to-trough drop. `<20%` tolerable.
- **Profit factor** — gross wins ÷ gross losses. `>1.5` strong.
- **Monte Carlo P5** — pessimistic worst case over 500 bootstraps.

Full definitions: [reference/metrics.md](../reference/metrics.md).

## Optimize

```bash
flint optimize strategies/user/my_strat.py --metric sharpe_ratio --trials 100
```

Requires a `parameters()` classmethod on the strategy. Walkthrough: [Tutorial 3](../tutorials/03-optimize-walk-forward.md).

## Walk-forward

Always validate an optimization with walk-forward before deploying:

```bash
curl -sX POST localhost:8000/api/v1/optimize/walk-forward \
  -d '{"code":"...","market":"SOL-PERP","start_ts":...,"end_ts":...,
       "n_windows":5,"train_pct":0.7,"trials_per_window":30}'
```

Overfitting ratio `≥ 0.5` = usable; `< 0.3` = severely overfit.

## Deploy to paper

On any backtest result, click **Deploy to Paper** (UI) or:

```bash
curl -sX POST localhost:8000/api/v1/paper/start \
  -d '{"strategy":"momentum","market":"SOL-PERP",
       "initial_capital":10000,"venue":"drift",
       "risk_config":{"max_drawdown_pct":0.15}}'
```

Run paper for 2–4 weeks before live. Full flow: [Tutorial 4](../tutorials/04-paper-to-live.md).

## MCP (AI integration)

```bash
claude mcp add flint -- python -m flint.mcp_server
```

20 tools exposed to Claude Code / other MCP clients. See [reference/mcp-tools.md](../reference/mcp-tools.md).

## Next steps

- **Author a custom strategy** → [Tutorial 2](../tutorials/02-author-a-strategy.md)
- **Understand the architecture** → [concepts/architecture.md](../concepts/architecture.md)
- **Go live on Drift / Hyperliquid** → [Tutorial 4](../tutorials/04-paper-to-live.md) + [validation/devnet-testing-guide.md](../validation/devnet-testing-guide.md)
- **Full docs index** → [docs/README.md](../README.md)

## CLI cheat sheet

```bash
flint init                              # scaffold project + sample
flint serve                             # API + UI on :8000
flint serve --dev                       # API only; run UI separately
flint backtest <strategy.py>            # backtest from CLI
flint optimize <strategy.py>            # Optuna search
flint data download --market SOL-PERP   # download data
flint data status                       # coverage inventory
flint new my_strategy                   # scaffold strategy file
flint parity momentum --start ... --end ...     # backtest ↔ paper parity
flint calibrate drift --market SOL-PERP         # fit impact from live fills
flint live <strategy.py> --real         # real trading (devnet by default)
```

Full reference: [reference/cli.md](../reference/cli.md).
