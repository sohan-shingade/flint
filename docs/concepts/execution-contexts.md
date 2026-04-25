# Execution Contexts

Why the same strategy runs in backtest, paper, and live without code changes — and where the semantics diverge.

## The hierarchy

```
ExecutionContext (ABC)
├── BacktestContext              replay candles, fills via FillPipeline
├── PaperBroker                  same fill models, live WebSocket ticks
└── LiveExecutionContext (ABC)
    ├── LiveDriftContext           driftpy + Solana RPC
    ├── LiveHyperliquidContext     REST + EIP-712
    ├── LiveCCXTContext            Binance, OKX, Bybit, etc.
    └── MultiVenueLiveContext      routes by venue=
```

Every context exposes the same order API (`market_order`, `limit_order`, `stop_order`, ...) and the same state properties (`account`, `positions`, `pending_orders`). See the full surface in [python-sdk.md §ExecutionContext](../reference/python-sdk.md#executioncontext).

## What differs

| Aspect | Backtest | Paper | Live |
|---|---|---|---|
| Clock | Bar timestamp | Wall clock (WebSocket) | Wall clock |
| Candles | From DuckDB | Built by `CandleAggregator` from trade stream | Same as paper |
| Fills | `FillPipeline` | `FillPipeline` | Venue-actual |
| Latency | Synthetic | Synthetic | Real network + venue |
| Fees | Modeled | Modeled | Real |
| Funding | Modeled from stored rates | Modeled from live rates | Real venue funding |
| Kill switch | Optional | Yes (`risk_config.max_drawdown_pct`) | Yes (`EquityMonitor`) |
| Order rejection | Risk guards | Risk guards + broker | Risk guards + venue + network errors |

Backtest and paper use **identical** fill modeling. Paper is not a "closer-to-reality" fill model — the only thing that changes is whether candles come from disk or from a WebSocket. This is intentional: paper tests execution *timing*, not fills.

## What to test where

**Backtest answers:**

- Does the strategy make money on history?
- How overfit is it? (via walk-forward)
- How does it behave under different regimes?

**Paper answers:**

- Are signals generated fast enough to act on?
- Does the strategy depend on data that's missing or late?
- How does current market regime differ from the backtest window?

**Live answers:**

- Does your fill model match reality on this venue?
- Can the venue handle your order size without rejection?
- Does the strategy survive network outages, RPC failures, venue downtime?

Do them in that order. Skip paper at your own risk.

## Parity testing

If backtest and paper diverge on the same data, **something is wrong with fill modeling or data handling, not with the strategy**. Run:

```bash
flint parity <strategy> --market SOL-PERP --start 2025-01-01 --end 2025-03-01
```

or `POST /api/v1/backtest/parity`. Pass threshold: `<2%` PnL divergence. Divergence > 2% means look at fills, slippage config, or funding timing, **not** the strategy logic. See [how-to/run-parity-test.md](../how-to/run-parity-test.md).

## Venue routing

`ctx.market_order(..., venue="drift")` vs `venue="hyperliquid"`. In backtest each venue uses its own `VenueConfig`. In live, `MultiVenueLiveContext` dispatches to the right connector. This is what makes cross-venue funding arb strategies one code path.

## Not in this doc

- What the fills look like → [fill-pipeline.md](fill-pipeline.md)
- What the risk guards do → [risk-model.md](risk-model.md)
- Where backtests diverge from live → [backtests-vs-reality.md](backtests-vs-reality.md)
