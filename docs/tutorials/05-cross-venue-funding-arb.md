# Tutorial 5 — Cross-Venue Funding Arbitrage

End state: you understand why cross-venue strategies require different setup, and you've run a funding arb backtest with per-venue margin, capital allocation, and proper cost modeling.

Prereq: Tutorials 1–3 finished.

Time: ~30 minutes.

## The idea

Perp funding rates are set by each venue independently. Drift's SOL-PERP funding might be +0.05% while Hyperliquid's is +0.01%. A market-neutral position — long Hyperliquid, short Drift — captures the 4 bps spread per funding cycle while being (almost) indifferent to price.

**Approximately.** Real basis can move against you; transfer costs are real; cross-venue execution is imperfect. Backtesting this properly requires more care than a single-venue strategy.

## Step 1 — Download funding from multiple venues

For SOL-PERP, Flint can pull funding from 7 venues (drift, hyperliquid, okx, bybit, dydx, gateio, bitget). All free, no API keys.

**API:**

```bash
curl -X POST localhost:8000/api/v1/data/download \
  -H 'Content-Type: application/json' \
  -d '{"market":"SOL-PERP","start_ts":1720000000,"end_ts":1743465600}'
```

Funding venues auto-fetch when the market ends in `-PERP`.

**CLI:** `flint data download --market SOL-PERP --days 180` — same behavior.

Verify:

```bash
curl -s "localhost:8000/api/v1/data/funding?market=SOL-PERP" | jq '.venues | keys'
# ["drift", "hyperliquid", "okx", "bybit", "dydx", "gateio", "bitget"]
```

## Step 2 — Inspect the funding heatmap

UI: **Funding Heatmap** page. Rows are venues, columns are time. Red = paying funding, green = receiving. Look for:

- **Persistent spread** between two venues (say Drift always 5–10 bps above Bybit).
- **Volatility** — spreads that mean-revert are tradable; spreads that drift don't help.
- **Direction stability** — if Drift is sometimes higher and sometimes lower, capture is noisy.

This is the pre-backtest sanity check. If no obvious spread pattern, the strategy won't work regardless of parameter tuning.

## Step 3 — Run the built-in funding arb

Flint ships `FundingArbStrategy` (`funding_arb`). Start with defaults:

```bash
curl -X POST localhost:8000/api/v1/backtest/run \
  -H 'Content-Type: application/json' \
  -d '{
    "strategy": "funding_arb",
    "market": "SOL-PERP",
    "resolution_s": 60,
    "start_ts": 1720000000, "end_ts": 1743465600,
    "initial_capital": 10000,
    "fee_rate": 0.0005,
    "margin_tracking": true,
    "capital_allocation": { "drift": 5000, "hyperliquid": 5000 },
    "params": {
      "min_spread_bps": 5.0,
      "exit_spread_bps": 1.0,
      "max_hold_hours": 24,
      "position_size_usd": 1000
    }
  }'
```

Key things that are different from a single-venue backtest:

- **`margin_tracking: true`** — positions exist on two venues, each with its own margin engine. Required for honest cost modeling.
- **`capital_allocation`** — partition starting cash across venues. Cross-venue transfers are simulated with delays; if capital isn't pre-positioned, the strategy pays the transfer cost.
- **`resolution_s: 60`** — funding changes hourly at most; minute resolution lets the strategy react to spread changes within a funding window.

## Step 4 — Read the results with per-venue breakdown

```json
{
  "metrics": {
    "total_pnl": 1240,
    "sharpe_ratio": 1.6,
    "max_drawdown": 0.08,
    "funding_paid": -680,
    "total_fees": 340
  },
  "per_venue_pnl":            { "drift": -120, "hyperliquid": 1360 },
  "per_venue_funding_income": { "drift": 420,  "hyperliquid": 260 },
  "per_venue_trades":         { "drift": 48,   "hyperliquid": 48 }
}
```

Key reads:

- **Funding received > fees paid.** If funding receive (`-funding_paid = 680`) is larger than `total_fees`, the arb is net positive. Otherwise you're paying for the strategy.
- **Per-venue PnL balance.** Both legs should be small — this is market-neutral. If one leg is wildly profitable and the other is wildly negative, you're running directional risk, not an arb.
- **Trade count parity.** `per_venue_trades["drift"] ≈ per_venue_trades["hyperliquid"]` — legs should pair up. Large mismatch means orders are failing on one venue.

## Step 5 — Costs that matter more here

Cross-venue arbitrage is **cost-dominated**. Small edges, high turnover.

| Cost | Where it's modeled |
|---|---|
| Taker fees both sides | `VenueConfig.taker_fee_bps` per venue |
| Funding paid (short side) | Multi-venue funding data |
| Slippage on both legs | `FillPipeline` per venue |
| Transfer costs | `VenueAllocator` (configured delays) |
| Solana priority fees | `tx_cost_priority_fee_lamports` |
| Jito tips (if enabled) | `tx_cost_jito_tip_lamports` |

Drift alone: taker 10 bps + priority fee + Jito tip ≈ 12–15 bps round trip. Hyperliquid leg: ~7 bps. You need spreads > ~20 bps *after* fees to make money consistently. Backtest defaults (`min_spread_bps: 5`) are often too aggressive.

## Step 6 — Write a custom cross-venue strategy

```python
from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side

class MyFundingArb(Strategy):
    def __init__(self, entry_bps=20.0, exit_bps=5.0, size_usd=2000.0):
        self.entry, self.exit, self.size = entry_bps, exit_bps, size_usd

    @property
    def name(self): return "my-funding-arb"

    def reset(self): pass

    def on_candle(self, candle, history, ctx=None):
        if ctx is None: return Signal.HOLD

        fr = ctx.get_funding_by_venue("SOL-PERP", lookback=1)
        if "drift" not in fr or "hyperliquid" not in fr: return Signal.HOLD

        # Most recent hourly rate for each venue (bps annualized)
        drift_bps = fr["drift"][-1][1] * 8760 * 10_000
        hl_bps    = fr["hyperliquid"][-1][1] * 8760 * 10_000
        spread_bps = drift_bps - hl_bps

        drift_pos = ctx.position("SOL-PERP", venue="drift")
        hl_pos    = ctx.position("SOL-PERP", venue="hyperliquid")
        in_trade  = drift_pos is not None or hl_pos is not None

        size = self.size / candle.close

        # Enter: short the higher-funding venue, long the lower-funding venue
        if not in_trade and abs(spread_bps) > self.entry:
            if spread_bps > 0:   # drift funding higher → short drift, long HL
                ctx.market_order("SOL-PERP", Side.SHORT, size, venue="drift")
                ctx.market_order("SOL-PERP", Side.LONG,  size, venue="hyperliquid")
            else:
                ctx.market_order("SOL-PERP", Side.LONG,  size, venue="drift")
                ctx.market_order("SOL-PERP", Side.SHORT, size, venue="hyperliquid")

        # Exit: spread compresses
        elif in_trade and abs(spread_bps) < self.exit:
            ctx.close_position("SOL-PERP", venue="drift")
            ctx.close_position("SOL-PERP", venue="hyperliquid")

        return Signal.HOLD

    @classmethod
    def parameters(cls):
        return {
            "entry_bps": {"type": "float", "low": 10.0, "high": 50.0, "default": 20.0},
            "exit_bps":  {"type": "float", "low": 1.0,  "high": 10.0, "default": 5.0},
            "size_usd":  {"type": "float", "low": 500,  "high": 5000, "default": 2000},
        }
```

Context methods used:

- `ctx.get_funding_by_venue(market, lookback)` → `{venue: [(ts, rate), ...]}`
- `ctx.position(market, venue)` → `PositionInfo | None`
- `ctx.market_order(market, side, size, venue=...)` — per-venue routing
- `ctx.close_position(market, venue=...)`

## Step 7 — Paper, then live

Same flow as [Tutorial 4](04-paper-to-live.md), with one extra step: set up capital on both venues **before** going live. Transfers take time; a strategy that opens a Drift leg but can't fund the HL leg will run one-sided until the transfer clears.

Drift + Hyperliquid live: use `MultiVenueLiveContext`. Walkthrough: [how-to/configure-multi-venue.md](../how-to/configure-multi-venue.md).

## Common failure modes

- **Both legs fill at different times.** One-sided exposure until the second fill. Reduce with smaller orders, faster venues, or HLP vaults on HL.
- **Legs get liquidated independently.** Drift's maintenance margin (5%) is tighter than HL's (2.5%). Size so *Drift* is the binding constraint, not HL.
- **Funding regime flip.** If the strategy is short the higher-funding venue and signs reverse persistently, the strategy now pays funding. Add a regime detector or hard stop.
- **Transfer costs eat the edge.** Don't rebalance aggressively. Pre-position capital and leave it parked.

## What's next

- [Tutorial 6 — Custom data provider](06-custom-data-provider.md) — add a new funding source
- [concepts/margin-capital.md](../concepts/margin-capital.md) — cross-venue capital mechanics
- [reference/strategy-templates.md](../reference/strategy-templates.md) — other cross-venue templates (`basis_trade`, `multi_venue_funding`)
