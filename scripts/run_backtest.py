#!/usr/bin/env python3
"""Fetch SOL-PERP data from Drift S3, run MA crossover backtest, print results."""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, ".")

from flint.backtest.engine import BacktestEngine
from flint.providers.drift_s3 import DriftS3Provider
from flint.store import FlintStore
from flint.strategy.ma_crossover import MACrossoverStrategy


def main() -> None:
    # Fetch March 2024 SOL-PERP data (dense daily coverage)
    end_ts = int(datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp())
    start_ts = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    resolution_s = 3600 * 24  # 1-hour candles

    print(f"Fetching SOL-PERP trades from Drift S3 ({datetime.fromtimestamp(start_ts, tz=timezone.utc).date()} to {datetime.fromtimestamp(end_ts, tz=timezone.utc).date()})...")
    t0 = time.time()

    provider = DriftS3Provider()
    candles = provider.fetch_candles("SOL-PERP", resolution_s, start_ts, end_ts)
    provider.close()

    fetch_time = time.time() - t0
    print(f"  → {len(candles)} hourly candles in {fetch_time:.1f}s")

    if not candles:
        print("No candle data retrieved. Exiting.")
        return

    # Store in DuckDB
    store = FlintStore("./data/flint.duckdb")
    n = store.upsert_candles(candles)
    print(f"  → Stored {n} candles in DuckDB")

    # Verify round-trip
    loaded = store.query_candles("SOL-PERP", resolution_s, start_ts, end_ts)
    print(f"  → Verified: {len(loaded)} candles read back from store")
    store.close()

    # Run backtest
    strategy = MACrossoverStrategy(fast_period=10, slow_period=30)
    engine = BacktestEngine(
        strategy,
        initial_capital=10_000,
        fee_rate=0.0005,
    )
    result = engine.run(candles)

    print(f"\n{'='*50}")
    print(f"  Strategy:       {strategy.name}")
    print(f"  Market:         SOL-PERP (Drift)")
    print(f"  Period:         {datetime.fromtimestamp(start_ts, tz=timezone.utc).date()} → {datetime.fromtimestamp(end_ts, tz=timezone.utc).date()}")
    print(f"  Resolution:     {resolution_s // 3600}h candles")
    print(f"  Candles:        {len(candles)}")
    print(f"{'='*50}")
    print(f"  Total PnL:      ${result.total_pnl:,.2f}")
    print(f"  Win Rate:       {result.win_rate:.1%}")
    print(f"  Max Drawdown:   {result.max_drawdown:.2%}")
    print(f"  Sharpe Ratio:   {result.sharpe_ratio:.2f}")
    print(f"  Total Trades:   {result.total_trades}")
    print(f"  Winners:        {result.winning_trades}")
    print(f"  Losers:         {result.losing_trades}")
    print(f"{'='*50}")

    if result.positions:
        print("\nTrade log:")
        for i, p in enumerate(result.positions, 1):
            direction = "LONG" if p.size > 0 else "SHORT"
            entry_dt = datetime.fromtimestamp(p.entry_ts, tz=timezone.utc).strftime("%m/%d %H:%M")
            exit_dt = datetime.fromtimestamp(p.exit_ts, tz=timezone.utc).strftime("%m/%d %H:%M")
            print(
                f"  #{i:>2}  {direction}  entry=${p.entry_price:,.2f} @ {entry_dt}"
                f"  exit=${p.exit_price:,.2f} @ {exit_dt}"
                f"  pnl=${p.pnl:,.2f}"
            )


if __name__ == "__main__":
    main()
