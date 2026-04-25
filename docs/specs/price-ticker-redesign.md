# Price ticker redesign — design exploration

**Status**: design exploration. No code change yet.
**Trigger**: incident in mid-2026 — `dlob.drift.trade` went dead in production
traffic. Flint paper-trading sessions stopped getting fresh marks, downstream
Kubera ledger reconciliation caught it via Flint's DuckDB candle history (kubera
commit `574899a`). The flint-side fix at the time bypassed DLOB by switching
to Hyperliquid (kubera-tracked commit `b7e6111`); this doc revisits the choice.

---

## What the price ticker is for

`flint/paper/price_ticker.py:PriceTicker` polls one mid-price per tracked market
every N seconds (default 5s) and stashes them in a `Dict[market, float]`. Two
consumers:

1. `flint/paper/engine.py:651` — between-candles mark-to-market. The bar candle
   gives the canonical close-price for the bar; the ticker fills in the
   intra-bar gap so the UI sees live unrealized PnL movement instead of a step
   function every minute/hour.
2. `flint/api/main.py:84` — wired onto `paper_engine.price_ticker` at lifespan
   startup.

**Display-only**. Backtests don't use it. Paper-execution fills don't use it.
The strategy callback never sees these prices. Liquidation checks use the
candle close, not the ticker. So the blast radius of "ticker is wrong / down"
is limited to UI freshness — but the UI looking frozen for hours during a
flat market is exactly what made the DLOB outage hard to spot.

## Why it was hardcoded

`PriceTicker.__init__` takes `markets: List[str]` and loops them through
one fetch function (`_fetch_mid_price`) which calls a single hardcoded URL
(`DLOB_BASE = "https://dlob.drift.trade"`). Three reasons it ended up that way:

1. **Drift was the only paper venue at v0.1.** When Hyperliquid landed, the
   ticker wasn't generalized — it kept polling Drift even for HL paper
   sessions, because "the actual fill price comes from the candle close
   anyway".
2. **DLOB was free, public, no API key.** No reason to look further.
3. **Mark price ≈ oracle price ≈ DLOB mid for liquid markets.** Even a
   wrong-venue mid is "close enough for the UI" — until DLOB returns nothing.

The brittleness wasn't in the URL choice. It was in **single-source-no-fallback
+ wrong layer**: the ticker is solving "what should the UI show as a current
price?" without any escalation when its primary source goes dark.

## Available price sources, ranked

| Source | Coverage | Cost | Latency | Reliability | Used elsewhere in Flint |
|---|---|---|---|---|---|
| Drift DLOB `/l2` | Drift perps only | free, public | ~200 ms | brittle (just died) | `drift_ws.py` for live execution |
| Hyperliquid `/info allMids` | every HL perp in one call | free, public | ~150 ms | strong | `hyperliquid_candles.py` |
| Pyth pull-feed (Hermes) | majors only (SOL/USD, BTC/USD, ETH/USD…) | free, public | ~300 ms | very strong | `pyth_ws.py`, `pyth_candles.py` |
| Drift Data API `/market/.../candles` | Drift perps | free, public | seconds (last bar) | strong | `drift_candles.py` |
| Birdeye / CoinGecko | spot only | free w/ rate limit | ~500 ms | medium | `birdeye_provider.py` |
| **Local DuckDB last candle** | every market with stored history | free, in-process | <1 ms | only-as-fresh-as-collector | every backtest |

Pyth covers the majors but breaks on the long tail (WIF, RENDER, JUP). HL
`allMids` covers every Flint perp because every Flint perp also lists on HL.
Drift Data API has the correct prices for Drift markets but returns the
last *closed* bar, not a tick — so it's better than DLOB only when the bar
is recent.

## Design options

### Option A: stay on one venue, swap to Hyperliquid

What was already shipped externally (`b7e6111`). Replace the URL + scaling.
~30 LOC change.

**Pros**: minimal diff, one HTTP call per tick instead of N, already proven
during the incident.

**Cons**:
- Same single-point-of-failure shape — when HL goes down, ticker dies again.
- HL's mid for `SOL` is HL's book mid, not Drift's. Drift paper sessions get
  a slightly off price that doesn't match what the strategy filled at on the
  candle close. Usually within 1 bp; can drift wider during dislocations.
- No principle for "what should we do when we add a new venue?"

### Option B: per-venue routing (no fallback)

`PriceTicker(market_to_venue: Dict[str, str], sources: Dict[str, PriceSource])`.
Each market resolves to its venue's source. `SOL-PERP` on Drift → DLOB,
`SOL-PERP` on HL → HL `/info`. **Caller specifies venue per market.**

**Pros**: prices always match the venue the strategy is paper-trading on. No
cross-venue drift. Adding a new venue = adding one source.

**Cons**: doesn't fix the brittleness — when the per-market venue goes down,
that market goes dark.

### Option C: fallback chain (recommended)

`PriceTicker(markets, sources: List[PriceSource])`. Each tick walks the
sources in order, takes the first one that returns a price, surfaces the
source name in the response so the UI can show "via DLOB / via HL / stale".

```
DriftDLOBSource     ──┐
HyperliquidInfoSource ├──► first-success wins per-market per-tick
PythSource            │
LocalDuckDBSource   ──┘   (last-known candle close, with staleness flag)
```

The DuckDB fallback is the killer feature: even when **every** live source is
down, the UI shows "last seen: $128.50 (3m ago)" instead of empty cells. The
staleness banner makes the failure mode visible in one glance.

**Pros**:
- Survives a single venue going dark (the actual incident).
- Per-market venue preference lives in the source-list ordering, not in a
  separate mapping — easy to reason about.
- DuckDB fallback turns a black-screen outage into a yellow-banner outage.
- Reuses the existing provider classes (`HyperliquidCandleProvider`,
  `DriftCandleProvider`, `PythCandleProvider`) for the source implementations
  — no new HTTP clients.

**Cons**:
- Per-tick HTTP fan-out unless the source-list is reordered to "batch first"
  (HL `allMids` → covers most markets in one call; DLOB only as fallback).
  Order of sources matters for cost.
- Slight schema-drift risk — each source must be wrapped to return the same
  `Optional[float]` shape. Trivial.

### Option D: WebSocket subscription (Pyth pull-feed via `pyth_ws.py`)

Already partly built (`flint/providers/pyth_ws.py`). Subscribe to the Pyth
Hermes endpoint and let it push mid updates. No polling at all.

**Pros**: lowest latency, lowest HTTP overhead.

**Cons**:
- Pyth coverage gaps on long-tail perps (WIF, JUP).
- Connection lifecycle is its own complexity (reconnect, heartbeat — same
  problems we already solved on the WS hook side, but server-side now).
- `pyth_ws.py` exists but isn't wired into the paper engine today; would
  need lifespan hooks.

### Option E: do nothing, accept failure

Keep DLOB. When it dies, the UI freezes. Users learn to refresh. Zero LOC.

**Cons**: this is what just happened.

## Recommendation

**Option C with the source order `[Hyperliquid /info allMids, Drift DLOB,
Pyth, LocalDuckDB]`**. Concrete plan:

1. Define `PriceSource` ABC with `get(market) → Optional[float]` and
   `name → str`.
2. Implement four concrete sources backing the four entries above. The HL
   one batches via `allMids` so it costs one HTTP call regardless of how
   many markets are tracked. Drift DLOB stays as the second-line fallback
   because it has tighter prices on Drift-native markets when it's actually
   working.
3. `PriceTicker` accepts `sources: List[PriceSource]` (default to the four
   above). Per tick, walk markets, walk sources, first hit wins. Record the
   source name on the returned price so consumers can render "via HL".
4. `LocalDuckDBSource` reads the last candle row from `FlintStore.query_candles(market, resolution_s, limit=1)`
   and returns `(close, ts)` — UI compares `now() - ts` to flag staleness.
5. Add a tiny `/api/v1/system/price-sources` endpoint returning per-source
   health (last-success ts, error count) so the ConnectionBanner can show
   "all sources healthy" / "DLOB down, fell back to HL".

Implementation cost: ~150 LOC + 20 tests. About a day of work end-to-end.

## What this fixes vs doesn't

**Fixes**:
- DLOB outage → automatic failover to HL.
- HL outage → automatic failover to Pyth (for majors) or DuckDB.
- Long-tail perps where Pyth lacks coverage → HL covers them.
- Adding a new venue → add one source, drop into the list.
- Visibility into source health → new endpoint + banner.

**Doesn't fix**:
- The fact that paper-trade marks for Drift sessions are HL prices when DLOB
  is down. Acceptable for display; would still be wrong for any logic that
  reads the ticker (currently nothing does).
- Total internet outage. DuckDB last-candle fallback covers the read path
  but the UI still flags as stale.
- Strategy logic latency-sensitive on mid-price. Not a current consumer; if
  it becomes one, it should subscribe to the relevant venue's WS feed
  directly, not piggyback the display ticker.

## Decision deferred to next loop

Not committed. Awaiting confirmation that Option C is the right shape
before writing the actual `PriceSource` abstraction + sources + ticker
rewrite + tests.
