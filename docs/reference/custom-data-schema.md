# Custom Dataset Schema

Users bring their own historical data through the same pipeline as built-in providers. Input formats: CSV and Parquet. Output: rows in the shared DuckDB store, tagged `venue="custom"` and market name prefixed with `custom:` to avoid collision with built-in markets.

This document is the authoritative schema. Any file that passes strict validation can be used in backtests, calibration, reconciliation, and proof notebooks.

---

## Universal rules (apply to every table)

1. Timestamps are UTC epoch seconds (`int64`), never milliseconds, never naive datetime. Use `int` in Parquet, `int` in CSV.
2. Timestamps must be **strictly monotonic** — no duplicates, no out-of-order rows.
3. The declared `resolution_s` (for candles) or the intrinsic cadence (for funding/orderbook) must match the actual row spacing within tolerance of 1 second.
4. Missing required columns: hard error.
5. Extra columns: silently ignored.
6. Market names must start with `custom:` (e.g. `custom:BTC-SPOT`, `custom:ETH-FTX`).
7. Every row gets a `source_hash` field computed as `sha256(file_content)` at import time, stored in the sync_metadata table.

---

## Candles

```
ts_epoch_s (int64, bar-close UTC, strictly monotonic)
open       (float64)
high       (float64)
low        (float64)
close      (float64)
volume     (float64, ≥ 0)
market     (string, must start with "custom:")
resolution_s (int32, bar duration in seconds)
```

**Example CSV:**

```
ts_epoch_s,open,high,low,close,volume,market,resolution_s
1700000000,45123.45,45200.00,45100.00,45180.25,120.5,custom:BTC-SPOT,3600
1700003600,45180.25,45350.00,45150.00,45305.75,98.2,custom:BTC-SPOT,3600
```

**Validation:**
- `high >= max(open, close)` per row
- `low <= min(open, close)` per row
- `resolution_s` matches `ts_epoch_s[i+1] - ts_epoch_s[i]` within 1s tolerance across the file
- No duplicate `(ts_epoch_s, market)` rows

---

## Funding rates

```
ts_epoch_s (int64, accrual/stamp time, strictly monotonic per (market, venue))
market     (string, "custom:*")
venue      (string)
rate       (float64, as a decimal — 0.0001 = 1bp, not 1.0)
interval_s (int32, funding interval in seconds)
```

**Validation:**
- `abs(rate) < 1.0` (sanity: a 100% hourly funding rate is almost certainly a unit error)
- `interval_s > 0`
- No duplicate `(ts_epoch_s, market, venue)` rows

---

## Orderbook snapshots

Stored per snapshot, not per level. Use JSON arrays for depth.

```
ts_epoch_s   (int64, exchange-time not poll-time)
market       (string, "custom:*")
venue        (string)
bid_prices   (JSON array of float64, descending)
bid_sizes    (JSON array of float64, same length as bid_prices)
ask_prices   (JSON array of float64, ascending)
ask_sizes    (JSON array of float64, same length as ask_prices)
```

**Validation:**
- `len(bid_prices) == len(bid_sizes)` and same for asks
- `bid_prices[0] < ask_prices[0]` (no crossed book)
- `bid_prices` strictly decreasing, `ask_prices` strictly increasing
- All sizes > 0

---

## Fills (for reconciliation / calibration)

```
ts_epoch_s (int64, exchange fill time)
market     (string)
venue      (string)
side       (string, "long" or "short")
size       (float64, > 0)
price      (float64, > 0)
fee        (float64, ≥ 0)
order_id   (string, optional)
```

**Validation:**
- `side ∈ {"long", "short"}`
- `size > 0 && price > 0 && fee >= 0`

---

## Importing

### CLI

```bash
flint data import --config flint.yaml
```

Reads the `custom_providers` block from `flint.yaml`:

```yaml
custom_providers:
  - name: my_btc_spot
    type: csv
    path: ./data/custom/btc_spot.csv
    table: candles
    markets: ["custom:BTC-SPOT"]
  - name: my_eth_funding
    type: parquet
    path: ./data/custom/eth_funding.parquet
    table: funding
    markets: ["custom:ETH-PERP"]
```

### Python

```python
from flint.providers.custom import CustomCSVProvider
from flint.store import FlintStore

store = FlintStore("data/flint.duckdb")
provider = CustomCSVProvider(
    path="./data/custom/btc_spot.csv",
    table="candles",
    markets=["custom:BTC-SPOT"],
)
n = provider.load_and_upsert(store)
print(f"Loaded {n} rows, source_hash={provider.source_hash}")
```

---

## Provenance

Every import records a `sync_metadata` row containing the source file's SHA-256 hash. Proof notebooks (Phase 1.5) pin this hash so the exact input is reproducible:

```sql
SELECT market, source, source_hash, last_ts
FROM sync_metadata
WHERE market LIKE 'custom:%';
```

When re-running a proof notebook, Flint asserts that the current file's hash matches the pinned one — mismatch is a loud error, not a silent drift.

---

## Namespace isolation

Built-in markets like `SOL-PERP` and custom markets like `custom:BTC-SPOT` cannot collide. The `custom:` prefix is enforced at load time. If you need to ingest data for a market Flint also supports natively, either pick a distinct name (`custom:SOL-SPOT-MYVENUE`) or overwrite the built-in market via `--force`.

---

## See also

- [`docs/specs/phase-1-trust-correctness.md#16-custom-dataset-ingest`](../specs/phase-1-trust-correctness.md#16-custom-dataset-ingest) — implementation spec
- [`docs/how-to/calibrate-slippage.md`](../how-to/calibrate-slippage.md) — uses BYO fill log
- Source: `flint/providers/custom.py`
