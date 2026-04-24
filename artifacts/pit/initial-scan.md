# Point-in-Time Audit — 2026-04-24 21:05 UTC

Every data provider must declare `PIT_METADATA` documenting its timestamp convention. Providers without a declaration are flagged for human review.

**Summary:** 26 providers · 26 ✓ pass · 0 ⚠ warn · 0 ✗ fail

| Provider | Verdict | Issues |
|---|---|---|
| `birdeye` | ✓ pass | — |
| `candle_aggregator` | ✓ pass | — |
| `ccxt_markets` | ✓ pass | — |
| `ccxt_provider` | ✓ pass | — |
| `coingecko` | ✓ pass | — |
| `custom` | ✓ pass | — |
| `drift_api` | ✓ pass | — |
| `drift_candles` | ✓ pass | — |
| `drift_s3` | ✓ pass | — |
| `drift_ws` | ✓ pass | — |
| `funding_rates` | ✓ pass | — |
| `gecko` | ✓ pass | — |
| `helius` | ✓ pass | — |
| `hyperliquid_candles` | ✓ pass | — |
| `hyperliquid_orderbook` | ✓ pass | — |
| `hyperliquid_ws` | ✓ pass | — |
| `jupiter` | ✓ pass | — |
| `jupiter_borrow` | ✓ pass | — |
| `open_interest` | ✓ pass | — |
| `orca` | ✓ pass | — |
| `orca_ticks` | ✓ pass | — |
| `pyth` | ✓ pass | — |
| `pyth_candles` | ✓ pass | — |
| `pyth_ws` | ✓ pass | — |
| `raydium` | ✓ pass | — |
| `tardis` | ✓ pass | — |

---

## Required PIT_METADATA template

```python
PIT_METADATA = {
    "candle_ts": "bar-close",
    "funding_ts": "accrual-time",
    "orderbook_ts": "exchange-time",
    "oi_ts": "block-time",
    "reviewed": "YYYY-MM-DD",
}
```