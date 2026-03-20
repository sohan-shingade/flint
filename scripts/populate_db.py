#!/usr/bin/env python3
"""Bulk download SOL-PERP trade data from Drift S3 and populate DuckDB with 1h candles.

Covers 2022-01-01 through today. Downloads one day at a time, aggregates to
1-hour candles, and upserts into DuckDB. Prints progress as it goes.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")

from flint.providers.drift_s3 import DriftS3Provider
from flint.store import FlintStore


def main() -> None:
    start = datetime(2022, 1, 1, tzinfo=timezone.utc)
    end = datetime.now(timezone.utc)
    resolution_s = 3600  # 1-hour candles
    market = "SOL-PERP"

    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())

    print(f"Populating DuckDB with {market} 1h candles")
    print(f"  Range: {start.date()} → {end.date()}")
    print(f"  Source: Drift S3 (drift-historical-data-v2)")
    print()

    store = FlintStore("./data/flint.duckdb")
    provider = DriftS3Provider()

    total_candles = 0
    skipped = 0
    errors = 0
    t0 = time.time()

    # Process one month at a time to keep memory reasonable
    current = start
    while current < end:
        # Compute first of next month
        next_month = (datetime(current.year, current.month, 1, tzinfo=timezone.utc) + timedelta(days=32)).replace(day=1)
        month_end = min(next_month, end)

        # If we're in the final partial month, ensure we still advance past it
        if month_end <= current:
            break

        chunk_start = int(current.timestamp())
        chunk_end = int(month_end.timestamp())
        label = current.strftime("%Y-%m")

        # Skip months we already have data for:
        # check if we have at least 80% of expected hourly candles
        existing = store.query_candles(market, resolution_s, chunk_start, chunk_end)
        expected_hours = (chunk_end - chunk_start) / 3600
        if len(existing) >= expected_hours * 0.8:
            skipped += 1
            print(f"  {label}  SKIP  ({len(existing)} candles already in DB)")
            current = month_end
            continue

        try:
            candles = provider.fetch_candles(market, resolution_s, chunk_start, chunk_end)
            if candles:
                n = store.upsert_candles(candles)
                total_candles += n
                elapsed = time.time() - t0
                print(f"  {label}  {n:>5} candles  ({total_candles:>7} new, {elapsed:.0f}s)")
            else:
                print(f"  {label}  --  no data on S3")
        except Exception as e:
            errors += 1
            print(f"  {label}  ERROR: {e}")

        current = month_end

    provider.close()

    # Summary
    elapsed = time.time() - t0
    count = store.count_candles(market, resolution_s)
    store.close()

    print()
    print(f"Done in {elapsed:.0f}s")
    print(f"  Total candles in DB: {count:,}")
    print(f"  New candles added:   {total_candles:,}")
    print(f"  Months skipped:      {skipped}")
    print(f"  Errors:              {errors}")
    print(f"  DB path: ./data/flint.duckdb")


if __name__ == "__main__":
    main()
