# How to: Download market data

For bulk / multi-market / long-range downloads.

## Single market, quick

```bash
flint data download --market SOL-PERP --days 180
```

Or API:

```bash
curl -X POST localhost:8000/api/v1/data/download \
  -H 'Content-Type: application/json' \
  -d '{"market":"SOL-PERP","start_ts":1720000000,"end_ts":1743465600}'
```

Funding rates for the 7 perp venues auto-fetch when the market ends in `-PERP`.

## Multi-market, long range — use async

```bash
DL_ID=$(curl -sX POST localhost:8000/api/v1/data/download-async \
  -H 'Content-Type: application/json' \
  -d '{"markets":["SOL-PERP","BTC-PERP","ETH-PERP","WIF-PERP"],"start_ts":1709251200,"end_ts":1743465600}' \
  | jq -r .download_id)

while true; do
  R=$(curl -s localhost:8000/api/v1/data/download-async/$DL_ID/status)
  echo $R | jq '.progress | {pct, elapsed_s}'
  [ "$(echo $R | jq -r .status)" = "complete" ] && break
  [ "$(echo $R | jq -r .status)" = "failed" ] && { echo $R; exit 1; }
  sleep 5
done
```

Async avoids the 30s sync timeout and shows per-market progress.

## Specific venues only

```json
{ "market": "SOL-PERP", "start_ts": ..., "end_ts": ...,
  "venues": ["hyperliquid", "okx"] }
```

## Verify coverage

```bash
curl -s "localhost:8000/api/v1/data/check?market=SOL-PERP&resolution_s=3600&start_ts=...&end_ts=..." | jq .
# { "has_data": true, "covers_range": true, "candle_count": 2160, "coverage_pct": 100, ... }
```

Multi-market check:

```bash
curl -sX POST localhost:8000/api/v1/data/check-markets \
  -H 'Content-Type: application/json' \
  -d '{"markets":["SOL-PERP","BTC-PERP"],"resolution_s":3600,"start_ts":...,"end_ts":...}'
# {"ready": true, "markets": {...}, "missing": []}
```

## See what's in the DB

```bash
flint data status                                   # CLI
curl -s localhost:8000/api/v1/data/markets | jq .   # API
curl -s localhost:8000/api/v1/data/freshness | jq . # how recent
```

## Purge and re-download

```bash
curl -X DELETE localhost:8000/api/v1/data/market/SOL-PERP
```

Deletes candles, funding, OI, orderbook, liquidations, and `sync_metadata`. Next download starts fresh.

## Gotchas

- **Hyperliquid + Pyth** are the core candle sources — free, no API keys. Pyth oracle candles fill gaps where Hyperliquid history is thin.
- **CoinGecko** fills spot-only gaps (BTC, ETH). Don't expect hourly granularity — daily at best for most tokens.
- **Funding gaps** — not all venues publish continuous funding history. Gate.io and Bitget sometimes return sparse results; cross-check via `/api/v1/data/funding?market=...` and pick venues with complete coverage for your window.
- **Jupiter historical data is not really available.** `perps-api.jup.ag` exposes *current* borrow rates and pool state only — no historical OHLCV, no historical borrow-rate series, no historical notional volume. `JupiterBorrowCollector` can accumulate rates going forward, but you can't backfill. Treat Jupiter strategies as forward-only: no honest backtest exists before your collection start date. For price history on a Jupiter-traded token, substitute Pyth oracle candles or a spot source like Birdeye.

## Related

- [reference/rest-api.md#data](../reference/rest-api.md#data) — full endpoint list
- [reference/data-providers.md](../reference/data-providers.md) — which providers cover what
