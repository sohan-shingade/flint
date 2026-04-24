# Point-in-Time Audit — 2026-04-24 02:37 UTC

Every data provider must declare `PIT_METADATA` documenting its timestamp convention. Providers without a declaration are flagged for human review.

**Summary:** 25 providers · 3 ✓ pass · 22 ⚠ warn · 0 ✗ fail

| Provider | Verdict | Issues |
|---|---|---|
| `birdeye` | ⚠ warn | no PIT_METADATA — human review required |
| `candle_aggregator` | ⚠ warn | no PIT_METADATA — human review required |
| `ccxt_markets` | ⚠ warn | no PIT_METADATA — human review required |
| `ccxt_provider` | ⚠ warn | no PIT_METADATA — human review required |
| `coingecko` | ⚠ warn | no PIT_METADATA — human review required |
| `drift_api` | ⚠ warn | no PIT_METADATA — human review required |
| `drift_candles` | ✓ pass | — |
| `drift_s3` | ⚠ warn | no PIT_METADATA — human review required |
| `drift_ws` | ⚠ warn | no PIT_METADATA — human review required |
| `funding_rates` | ✓ pass | — |
| `gecko` | ⚠ warn | no PIT_METADATA — human review required |
| `helius` | ⚠ warn | no PIT_METADATA — human review required |
| `hyperliquid_candles` | ✓ pass | — |
| `hyperliquid_orderbook` | ⚠ warn | no PIT_METADATA — human review required |
| `hyperliquid_ws` | ⚠ warn | no PIT_METADATA — human review required |
| `jupiter` | ⚠ warn | no PIT_METADATA — human review required |
| `jupiter_borrow` | ⚠ warn | no PIT_METADATA — human review required |
| `open_interest` | ⚠ warn | no PIT_METADATA — human review required |
| `orca` | ⚠ warn | no PIT_METADATA — human review required |
| `orca_ticks` | ⚠ warn | no PIT_METADATA — human review required |
| `pyth` | ⚠ warn | no PIT_METADATA — human review required |
| `pyth_candles` | ⚠ warn | no PIT_METADATA — human review required |
| `pyth_ws` | ⚠ warn | no PIT_METADATA — human review required |
| `raydium` | ⚠ warn | no PIT_METADATA — human review required |
| `tardis` | ⚠ warn | no PIT_METADATA — human review required |

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