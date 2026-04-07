# Pyth-First Pricing + Per-Venue Fill Pipelines — Design Spec

**Date**: 2026-04-05
**Status**: Draft

## Overview

Refactor Flint's data model from "multiple candle sources, one flat fee" to "one canonical price source (Pyth), multiple execution venue fill pipelines." Each venue has a realistic fill model that simulates how it actually fills trades — slippage, fees, latency, orderbook depth, and venue-specific mechanics.

This is decomposed into 3 sub-projects:
1. **Pyth Price Pipeline + Data Model Refactor** — foundation
2. **Per-Venue Fill Pipelines + Orderbook Collection** — execution realism
3. **UI Overhaul** — Data Explorer, BacktestLab, Onboarding redesign

## Sub-project 1: Pyth Price Pipeline + Data Model Refactor

### 1.1 Price Data Source

**Current**: Candles from multiple venues (Drift, Hyperliquid, Binance, OKX, Bybit, CoinGecko) stored with `venue` column. UI lets users pick candle sources.

**New**: Pyth oracle prices are the sole price source. One set of candles per market.

- New `PythCandleProvider` in `flint/providers/pyth_candles.py`
- Uses Pyth's TradingView-compatible endpoint: `benchmarks.pyth.network/v1/shims/tradingview/history`
- Returns clean OHLCV bars (1m to 1W resolution). No custom aggregation needed.
- Market mapping: reuses existing Pyth feed ID mapping from `flint/providers/pyth.py` (22 pairs)
- Candles stored with `venue='pyth'` in existing `candles` table
- All backtest queries default to `venue='pyth'`

**What Pyth is NOT used for**: Funding rates and borrow rates are still downloaded per venue as today. Pyth only replaces the candle/price data source.

### 1.2 Download API Refactor

`POST /api/v1/data/download` changes:

```json
// Old
{"market": "SOL-PERP", "venue": "drift", "funding_venues": ["drift", "okx"]}

// New
{"market": "SOL-PERP", "execution_venues": ["drift", "hyperliquid", "jupiter"]}
```

- `venue` param removed (price always from Pyth)
- `funding_venues` accepted as alias for `execution_venues` (backward compat)
- `execution_venues` controls what supplementary data gets downloaded:
  - Drift/Hyperliquid/Binance/OKX/Bybit: funding rates + orderbook snapshots
  - Jupiter: borrow rates
- `download_orderbooks: true` (default) controls whether orderbook snapshots are fetched for selected venues

### 1.3 Migration Path

**Auto-migration on startup** (`flint serve` or `flint init`):

1. Query `sync_metadata` for all markets with existing candle data
2. For each market, check if `venue='pyth'` candles exist for that date range
3. If not, queue background Pyth candle download for those markets/ranges
4. Show one-time UI banner: "Migrating price data to Pyth oracle prices. Your existing venue data is preserved."
5. Migration tracked in `sync_metadata` with `provider='pyth_migration'`

**Rollback safety**: Migration is idempotent. If Pyth backfill fails partway, the backtest engine falls back to any available venue candle if `venue='pyth'` doesn't exist for a given range. No existing data is deleted.

**What changes for users**:
- `flint init` downloads Pyth candles instead of Drift candles
- Data Explorer no longer shows "Candle Source" selector
- Backtests automatically use Pyth candles

### 1.4 Config

```yaml
# flint.yaml
price_source: pyth  # hardcoded default, future-proofs for alternatives
```

No new env vars needed — Pyth APIs are free and keyless.

---

## Sub-project 2: Per-Venue Fill Pipelines + Orderbook Collection

### 2.1 Fill Pipeline Architecture

**Base interface** (unchanged):
```python
class FillModel(ABC):
    def fill_market(self, order, candle) -> Optional[Fill]
    def fill_limit(self, order, candle) -> Optional[Fill]
    def check_stop_trigger(self, order, candle) -> bool
    def set_orderbook(self, book: OrderbookSnapshot)
```

**New venue-specific fill models**:

| Class | File | Execution Model |
|-------|------|-----------------|
| `DriftFillModel` | `fill_drift.py` | 3-tier: JIT Dutch auction → DLOB walk → vAMM backstop |
| `HyperliquidFillModel` | `fill_hyperliquid.py` | Pure CLOB walk + HLP backstop |
| `JupiterFillModel` | `fill_jupiter.py` | Oracle price + keeper delay interpolation + quadratic impact |
| `CexFillModel` | `fill_cex.py` | Base CLOB walk (shared by Binance/OKX/Bybit) |
| `BinanceFillModel` | `fill_cex.py` | CexFillModel with Binance params |
| `OkxFillModel` | `fill_cex.py` | CexFillModel with OKX params |
| `BybitFillModel` | `fill_cex.py` | CexFillModel + IOC price band capping |

**Engine integration**: `BacktestEngine` holds `Dict[str, FillModel]` mapping venue names to fill model instances. The `venue` parameter on orders determines which fill model processes them.

### 2.2 Drift 3-Tier Fill Model

`DriftFillModel` in `flint/execution/fill_drift.py`

**Tier 1 — JIT Dutch Auction** (~60% of volume):
- Simulates Dutch auction over `auction_slots=20` (~10s)
- Price starts favorable to taker, linearly degrades toward oracle
- `jit_fill_probability=0.6` controls how often JIT makers fill
- When JIT fills: fill price = auction price at simulated fill time (typically 1-3 bps better than oracle)

**Tier 2 — DLOB Walk** (~30% of volume):
- Unfilled portion walks the L2 orderbook snapshot (from Drift S3 archives)
- Standard price-time priority fill
- If no orderbook data: uses synthetic depth model

**Tier 3 — vAMM Backstop** (~10% of volume):
- Remaining unfilled portion routes to existing `VammCurve` (`flint/execution/vamm.py`)
- Constant-product AMM: slippage scales with order size relative to `sqrt_k`
- 10-slot speed bump simulated as additional latency

**Composition**: Single order can split across tiers. Blended average fill price. Taker fee (10 bps default) applied on total filled amount.

### 2.3 Hyperliquid Fill Model

`HyperliquidFillModel` in `flint/execution/fill_hyperliquid.py`

**Pure CLOB walk**:
- Walk L2 orderbook from best price outward (price-time priority)
- Historical L2 data from `s3://hyperliquid-archive/market_data/` (free, LZ4 compressed)
- Fallback: synthetic depth model (deeper than Drift, thinner than CEXes)

**HLP backstop**:
- If orderbook exhausted: remaining portion fills at `oracle_price * (1 + impact_coefficient * remaining_pct)`
- Simulates HLP vault stepping in as liquidity provider of last resort

**Params**: Taker 4.5 bps, maker rebate -1.5 bps, latency 0.2s ± 0.1s.

### 2.4 Jupiter Fill Model

`JupiterFillModel` in `flint/execution/fill_jupiter.py`

**Oracle-priced pool fill with keeper delay**:
- No orderbook — pool-based execution at Pyth oracle price
- `JupiterFillModel` receives a candle lookahead buffer via `set_candle_buffer(candles: List[Candle])` called by the engine before each bar. This gives the fill model access to upcoming bars for interpolation.
- Keeper delay modeled as continuous time, not rounded to bar boundaries:
  - `delay_s = random(base_latency - jitter, base_latency + jitter)` (default 12s ± 8s)
  - If delay lands within current bar: interpolate `open + (delay_s / bar_duration) * (close - open)`
  - If delay crosses into next bar: use `next_bar.open + (remaining_s / bar_duration) * (next_bar.close - next_bar.open)`
  - Fill price = interpolated price + quadratic impact fee

**Quadratic price impact fee**:
```
impact_fee = (notional_usd / impact_fee_scalar) * notional_usd
```
- `impact_fee_scalar` per custody: SOL ~1B, ETH ~1B, BTC ~1B
- $10K trade: ~$0.10 impact (negligible)
- $1M trade: ~$1,000 impact (significant)

**Flat fee**: 6 bps on open and close. No maker/taker distinction.

**Borrow cost**: Already implemented via `BorrowCostModel` from Jupiter Perps integration. Continuous accrual via cumulative rate.

### 2.5 CEX Fill Models (Binance, OKX, Bybit)

`CexFillModel` base in `flint/execution/fill_cex.py`, with venue subclasses.

**Shared CLOB walk**:
- Walk L2 orderbook from best price outward
- Volume-weighted average fill price across touched levels
- If no orderbook data: synthetic depth model fallback

**Per-venue differences**:

| | Binance | OKX | Bybit |
|--|---------|-----|-------|
| Taker | 5.0 bps | 5.0 bps | 5.5 bps |
| Maker | 2.0 bps | 2.0 bps | 2.0 bps |
| Latency | 0.2s ± 0.1s | 0.3s ± 0.15s | 0.3s ± 0.15s |
| Special | Deepest books | Standard | IOC price band cap |

**Bybit IOC conversion**: `BybitFillModel` overrides `fill_market()` to cap the orderbook walk at a price band (1% from mark). Returns partial fill if book exhausted within band.

### 2.6 Synthetic Depth Model (Fallback)

When no real orderbook data is available, fill models generate a synthetic orderbook.

**Model**: Log-normal depth distribution per venue.

```python
@dataclass
class DepthProfile:
    bid_depth_1pct: float    # USD liquidity within 1% of mid (bid side)
    ask_depth_1pct: float    # USD liquidity within 1% of mid (ask side)
    concentration: float     # How concentrated at top-of-book (0-1)
    spread_bps: float        # Typical bid-ask spread
```

**Per-venue defaults** (BTC-PERP baseline, other markets scale linearly by relative volume):

| Venue | Depth ±1% | Concentration | Spread |
|-------|-----------|---------------|--------|
| Binance | $20M | 0.7 | 0.5 bps |
| OKX | $10M | 0.6 | 1.0 bps |
| Bybit | $8M | 0.6 | 1.0 bps |
| Hyperliquid | $5M | 0.5 | 1.5 bps |
| Drift | $2M | 0.4 | 3.0 bps |

Given a mid price and `DepthProfile`, distributes liquidity across 20 price levels following the concentration curve. Fill model walks this synthetic book identically to a real one.

Overridable per venue in `flint.yaml`.

### 2.7 Tardis.dev Integration

New provider: `flint/providers/tardis.py`

- REST API: `GET https://api.tardis.dev/v1/data-feeds/{exchange}/{data_type}` with date range
- Auth: `FLINT_TARDIS_API_KEY` in config
- Data type: `book_snapshot_25` (top 25 levels, sampled every 1 minute)
- Downsampled to 5-minute aligned boundaries on ingestion
- Stored in `orderbook_snapshots` table
- `sync_metadata` tracks downloads to avoid re-fetching
- Cost awareness: logs estimated data cost, respects `tardis_max_gb_per_request` limit (default: 1 GB)

**Fallback chain in fill models**:
1. Real orderbook snapshot in store → walk it
2. Tardis API key configured → download, cache, walk
3. Neither → generate synthetic orderbook from `DepthProfile`

### 2.8 Orderbook Snapshot Storage

Existing `orderbook_snapshots` table schema works as-is (`venue`, `market`, `ts`, `bids`, `asks` as JSON arrays).

**All timestamps normalized to 5-minute aligned boundaries** on ingestion (`:00`, `:05`, `:10`, etc.), regardless of source sampling time. This ensures cross-venue timestamp alignment.

New store method:
```python
def query_nearest_orderbook(self, venue: str, market: str, ts: int) -> Optional[OrderbookSnapshot]
```

**Backfill providers**:
- `DriftOrderbookProvider` — Drift S3 archives (existing, may need minor adaptation)
- `HyperliquidOrderbookProvider` — `s3://hyperliquid-archive/market_data/` LZ4 files
- `TardisOrderbookProvider` — Tardis REST API

All produce `OrderbookSnapshot` model, store via `store.upsert_orderbook_snapshots()`.

**Storage sizing**: ~500MB for 20 levels × 2 sides × 5 min × 6 venues × 3 markets × 90 days.

**Testing**: Cross-venue timestamp collision tests — verify three venues with offset source timestamps all normalize correctly to aligned boundaries and return correct nearest matches independently.

### 2.9 Venue boundary edge case tests

- Switching venues mid-strategy (close Drift position, open Jupiter position same bar)
- Partial fills across venue configs
- Multi-venue positions at the same timestamp
- Orderbook snapshot gaps (venue has data for some bars but not others)

---

## Sub-project 3: UI Overhaul

### 3.1 Data Explorer Redesign

**Current layout**: Presets → Time Range → Candle Sources → Funding Venues → Markets → Download

**New layout**:

**1. Markets** (moved to top)
- Same grid of perpetuals + spot
- Presets stay (Starter Pack, DeFi Pack, etc.)

**2. Time Range**
- Same as today

**3. Execution Venues** (replaces Candle Sources + Funding Venues)
- Cards layout, one per venue
- Each card shows:
  - Venue name + color
  - Data downloaded: "Funding rates (1h) + Orderbook depth" or "Borrow rates + Pool impact"
  - Data source badge: "Free" or "Requires Tardis API key"
- Quick-select: "All DEX" (Drift, Hyperliquid, Jupiter), "All CEX" (Binance, OKX, Bybit), "All"

**4. Price Source** (informational banner, non-selectable)
- "Price data: Pyth Oracle — canonical oracle prices used across all Solana perp venues"

**5. Download**
- Per-venue progress: "Pyth candles ✓ | Drift funding ✓ | Drift orderbooks ↻ | ..."
- Warnings surfaced: "Binance orderbook data requires Tardis API key — using synthetic depth model"

**6. Migration banner** (shown once after upgrade)
- "Flint now uses Pyth oracle prices as the canonical price source. Your existing venue candle data is preserved."

### 3.2 BacktestLab Venue Selector

**Replace fee dropdown** with Execution Venue selector:

```
EXECUTION VENUE
┌─────────────────────────────────────────────┐
│ [Drift ▼]                                   │
│                                             │
│ Fill Model:  JIT Auction → DLOB → vAMM      │
│ Taker Fee:   10 bps  │  Maker: -2 bps       │
│ Latency:     8s ± 5s                         │
│ Depth:       Real orderbook (Drift S3)       │
│ Funding:     Hourly (positive/negative)      │
└─────────────────────────────────────────────┘
```

- Venue selection auto-configures fill model, fees, latency
- Info panel shows venue execution profile
- Missing data warnings: "No orderbook data — using synthetic depth"
- Multi-venue strategies: auto-detected from code, both venues shown side by side
- Old fee_rate dropdown preserved as "Advanced Override" (collapsed by default)

### 3.3 Onboarding Redesign

**Venues step** changes from two sections to one:

```
SELECT EXECUTION VENUES

Choose which venues to simulate trading on. Flint downloads
orderbook depth + funding data for realistic fill modeling.
Price data comes from Pyth oracle automatically.

[Drift - DEX - Free] [Hyperliquid - DEX - Free] [Jupiter - DEX - Free]
[Binance - CEX - Tardis*] [OKX - CEX - Tardis*] [Bybit - CEX - Tardis*]

* Orderbook data requires Tardis API key. Without it, synthetic depth is used.
```

Default selection: Drift + Hyperliquid (free, good coverage).

---

## Files Summary

### Sub-project 1: New/Modified Files
| File | Change |
|------|--------|
| `flint/providers/pyth_candles.py` | New: PythCandleProvider |
| `flint/api/routes/data.py` | Modify: download endpoint (remove venue, add execution_venues) |
| `flint/cli.py` | Modify: `flint init` uses Pyth |
| `flint/config.py` | Add: `price_source` field |
| `flint/store.py` | Add: migration helper methods |

### Sub-project 2: New/Modified Files
| File | Change |
|------|--------|
| `flint/execution/fill_drift.py` | New: DriftFillModel (3-tier) |
| `flint/execution/fill_hyperliquid.py` | New: HyperliquidFillModel |
| `flint/execution/fill_jupiter.py` | New: JupiterFillModel (keeper-delay-aware) |
| `flint/execution/fill_cex.py` | New: CexFillModel + Binance/OKX/Bybit subclasses |
| `flint/execution/synthetic_depth.py` | New: DepthProfile + synthetic book generator |
| `flint/providers/tardis.py` | New: TardisOrderbookProvider |
| `flint/providers/hyperliquid_orderbook.py` | New: HyperliquidOrderbookProvider |
| `flint/execution/fill_models.py` | Modify: keep existing models, add registry |
| `flint/execution/venue_config.py` | Modify: add fill_model_type field |
| `flint/backtest/engine.py` | Modify: per-venue fill model dispatch |
| `flint/store.py` | Add: query_nearest_orderbook method |

### Sub-project 3: Modified Files
| File | Change |
|------|--------|
| `ui/src/pages/DataExplorer.tsx` | Full redesign |
| `ui/src/pages/BacktestLab.tsx` | Venue selector replaces fee dropdown |
| `ui/src/pages/Setup.tsx` | Onboarding venues step redesign |

---

## External Dependencies

| Dependency | Purpose | Required? |
|------------|---------|-----------|
| Pyth Benchmarks API | Historical OHLCV candles | Yes (free, keyless) |
| Drift S3 archives | Historical orderbook data | No (free, enhances Drift fill model) |
| Hyperliquid S3 archive | Historical orderbook data | No (free, enhances HL fill model) |
| Tardis.dev API | CEX historical orderbook data | No (paid, `FLINT_TARDIS_API_KEY`) |

## Config

```yaml
# flint.yaml additions
price_source: pyth
tardis_api_key: ""                 # optional, for CEX orderbook data
tardis_max_gb_per_request: 1.0     # cost guard
```

```env
FLINT_TARDIS_API_KEY=td_xxx        # alternative to YAML
```
