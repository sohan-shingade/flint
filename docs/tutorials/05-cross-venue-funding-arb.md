# Tutorial 5 — Cross-Venue Funding Arbitrage

End state: you understand why cross-venue strategies require different setup, and you've run a funding arb backtest with per-venue margin, capital allocation, and proper cost modeling.

Prereq: Tutorials 1–3 finished.

Time: ~30 minutes.

## The idea

Perp funding rates are set by each venue independently. OKX's SOL-PERP funding might be +0.05% while Hyperliquid's is +0.01%. A market-neutral position — long Hyperliquid, short OKX — captures the 4 bps spread per funding cycle while being (almost) indifferent to price.

This is a **backtest/research example**: it pairs a DEX perp (Hyperliquid) against a CEX perp (OKX) using collected funding data. Live CEX execution is out of scope — live legs run on Hyperliquid.

**Approximately.** Real basis can move against you; transfer costs are real; cross-venue execution is imperfect. Backtesting this properly requires more care than a single-venue strategy.

## Step 1 — Download funding from multiple venues

For SOL-PERP, Flint can pull funding from 6 venues (hyperliquid, okx, bybit, dydx, gateio, bitget). All free, no API keys.

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
# ["hyperliquid", "okx", "bybit", "dydx", "gateio", "bitget"]
```

## Step 2 — Inspect the funding heatmap

UI: **Funding Heatmap** page. Rows are venues, columns are time. Red = paying funding, green = receiving. Look for:

- **Persistent spread** between two venues (say OKX always 5–10 bps above Bybit).
- **Volatility** — spreads that mean-revert are tradable; spreads that drift don't help.
- **Direction stability** — if OKX is sometimes higher and sometimes lower, capture is noisy.

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
    "capital_allocation": { "hyperliquid": 5000, "okx": 5000 },
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
  "per_venue_pnl":            { "hyperliquid": 1360, "okx": -120 },
  "per_venue_funding_income": { "hyperliquid": 260,  "okx": 420 },
  "per_venue_trades":         { "hyperliquid": 48,   "okx": 48 }
}
```

Key reads:

- **Funding received > fees paid.** If funding receive (`-funding_paid = 680`) is larger than `total_fees`, the arb is net positive. Otherwise you're paying for the strategy.
- **Per-venue PnL balance.** Both legs should be small — this is market-neutral. If one leg is wildly profitable and the other is wildly negative, you're running directional risk, not an arb.
- **Trade count parity.** `per_venue_trades["hyperliquid"] ≈ per_venue_trades["okx"]` — legs should pair up. Large mismatch means orders are failing on one venue.

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

OKX leg: taker ~5 bps round trip (CEX maker/taker schedule). Hyperliquid leg: ~7 bps. You need spreads > ~15 bps *after* fees to make money consistently. Backtest defaults (`min_spread_bps: 5`) are often too aggressive.

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
        if "okx" not in fr or "hyperliquid" not in fr: return Signal.HOLD

        # Most recent hourly rate for each venue (bps annualized)
        okx_bps = fr["okx"][-1][1] * 8760 * 10_000
        hl_bps  = fr["hyperliquid"][-1][1] * 8760 * 10_000
        spread_bps = okx_bps - hl_bps

        okx_pos = ctx.position("SOL-PERP", venue="okx")
        hl_pos  = ctx.position("SOL-PERP", venue="hyperliquid")
        in_trade = okx_pos is not None or hl_pos is not None

        size = self.size / candle.close

        # Enter: short the higher-funding venue, long the lower-funding venue
        if not in_trade and abs(spread_bps) > self.entry:
            if spread_bps > 0:   # okx funding higher → short okx, long HL
                ctx.market_order("SOL-PERP", Side.SHORT, size, venue="okx")
                ctx.market_order("SOL-PERP", Side.LONG,  size, venue="hyperliquid")
            else:
                ctx.market_order("SOL-PERP", Side.LONG,  size, venue="okx")
                ctx.market_order("SOL-PERP", Side.SHORT, size, venue="hyperliquid")

        # Exit: spread compresses
        elif in_trade and abs(spread_bps) < self.exit:
            ctx.close_position("SOL-PERP", venue="okx")
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

Same flow as [Tutorial 4](04-paper-to-live.md), with one extra step: set up capital on both venues **before** going live. Transfers take time; a strategy that opens one leg but can't fund the other will run one-sided until the transfer clears.

Live, only the Hyperliquid leg executes (the OKX leg is research-only in this example). Multi-venue live routing uses `MultiVenueLiveContext`. Walkthrough: [how-to/configure-multi-venue.md](../how-to/configure-multi-venue.md).

## Common failure modes

- **Both legs fill at different times.** One-sided exposure until the second fill. Reduce with smaller orders, faster venues, or HLP vaults on HL.
- **Legs get liquidated independently.** OKX's maintenance margin differs from HL's (2.5%). Size so the tighter-margin venue is the binding constraint.
- **Funding regime flip.** If the strategy is short the higher-funding venue and signs reverse persistently, the strategy now pays funding. Add a regime detector or hard stop.
- **Transfer costs eat the edge.** Don't rebalance aggressively. Pre-position capital and leave it parked.

## What's next

- [Tutorial 6 — Custom data provider](06-custom-data-provider.md) — add a new funding source
- [concepts/margin-capital.md](../concepts/margin-capital.md) — cross-venue capital mechanics
- [reference/strategy-templates.md](../reference/strategy-templates.md) — other cross-venue templates (`basis_trade`, `multi_venue_funding`)
