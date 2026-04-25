# Flint Documentation

Pick a doc by what you're trying to do. This index follows the [Diátaxis](https://diataxis.fr/) split — **tutorials** learn, **how-to** accomplish, **reference** look up, **concepts** explain.

## Start here

| Goal | Doc |
|---|---|
| First time — install to first backtest | [Tutorial 1 — Install + first backtest](tutorials/01-install-first-backtest.md) |
| Understand what Flint does | [Concepts — Architecture](concepts/architecture.md) |
| See the top-level feature surface | [README](../README.md) |

## Tutorials (learn)

Linear walkthroughs. Work through in order.

1. [Install and Run Your First Backtest](tutorials/01-install-first-backtest.md)
2. [Author a Strategy](tutorials/02-author-a-strategy.md) — custom RSI+MACD from scratch
3. [Optimize and Walk-Forward](tutorials/03-optimize-walk-forward.md) — Optuna + overfitting detection
4. [Paper to Live](tutorials/04-paper-to-live.md) — full deployment checklist
5. [Cross-Venue Funding Arbitrage](tutorials/05-cross-venue-funding-arb.md) — multi-venue strategy
6. [Custom Data Provider](tutorials/06-custom-data-provider.md) — extend Flint

## How-to (accomplish)

Short, task-oriented recipes.

- [Download market data](how-to/download-data.md)
- [Calibrate slippage from live fills](how-to/calibrate-slippage.md)
- [Run a parity test](how-to/run-parity-test.md)
- [Add API keys](how-to/add-api-keys.md)
- [Enable the Rust engine](how-to/run-rust-engine.md)
- [Compare backtests](how-to/compare-backtests.md)
- [Configure risk guards](how-to/configure-risk-guards.md)
- [Resume a paper session](how-to/resume-paper-session.md)

## Reference (look up)

Exhaustive catalogs.

- [REST API](reference/rest-api.md) — 61 endpoints across 12 routers
- [CLI](reference/cli.md) — 14 commands
- [Python SDK](reference/python-sdk.md) — `Strategy`, `ExecutionContext`, `BacktestEngine`, `FlintStore`
- [MCP tools](reference/mcp-tools.md) — 20 tools for AI integration
- [Config](reference/config.md) — `flint.yaml` + env schema
- [Strategy templates](reference/strategy-templates.md) — all 20 built-ins with defaults
- [Data providers](reference/data-providers.md) — 26 provider classes
- [Indicators](reference/indicators.md) — 20 technical indicators
- [Venue configs](reference/venue-configs.md) — per-venue fees, margin, latency
- [Metrics](reference/metrics.md) — every metric Flint reports

## Concepts (understand)

Explanation-oriented. Read these when the how-tos aren't enough.

- [Architecture](concepts/architecture.md) — top-level map
- [Execution Contexts](concepts/execution-contexts.md) — backtest vs paper vs live
- [Fill Pipeline](concepts/fill-pipeline.md) — 4-tier impact model
- [Margin & Capital](concepts/margin-capital.md) — per-venue margin, cross-venue cash
- [Regimes](concepts/regimes.md) — 8 curated market windows
- [Risk Model](concepts/risk-model.md) — guards + kill switch
- [Backtests vs Reality](concepts/backtests-vs-reality.md) — the realism ceiling

## Validation & safety

- [Safety Rails](validation/safety-rails.md) — failure-scenario walkthrough
- [Known Limitations](validation/known-limitations.md) — what the backtester does and doesn't model
- [Devnet Testing Guide](validation/devnet-testing-guide.md) — pre-mainnet checklist
- [Fill Model Comparison](validation/fill-model-comparison.md) — empirical writeup

## Built-in strategy deep-dives

- [Funding Arb](strategies/funding_arb.md)
- [Funding Mean Reversion](strategies/funding_mean_reversion.md)
- [Momentum Breakout](strategies/momentum_breakout.md)
- [Basis Trade](strategies/basis_trade.md)
- [MEV Arb Monitor](strategies/mev_arb_monitor.md)

## By role

| You are | Start with |
|---|---|
| **New user** | [Tutorial 1](tutorials/01-install-first-backtest.md) → [Architecture](concepts/architecture.md) |
| **Strategy author** | [Tutorial 2](tutorials/02-author-a-strategy.md) → [Python SDK](reference/python-sdk.md) → [Indicators](reference/indicators.md) |
| **Deploying live** | [Tutorial 4](tutorials/04-paper-to-live.md) → [Risk Model](concepts/risk-model.md) → [Safety Rails](validation/safety-rails.md) |
| **API consumer** | [REST API](reference/rest-api.md) |
| **MCP / AI agent** | [MCP tools](reference/mcp-tools.md) |
| **Extending Flint** | [Tutorial 6](tutorials/06-custom-data-provider.md) → [Architecture](concepts/architecture.md) |

## See also

- [CLAUDE.md](../CLAUDE.md) — AI-assistant guide (overlaps with this index)
- [CONTRIBUTING.md](../CONTRIBUTING.md)
- [ROADMAP.md](../ROADMAP.md)
