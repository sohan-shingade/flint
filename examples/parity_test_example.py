"""
Backtest vs Paper Trade Parity Test
====================================
Runs the same strategy through the backtest engine and paper broker
on identical data, then compares results. This validates that Flint's
backtest results are predictive of paper trading behavior.

    python examples/parity_test_example.py

Pass threshold: < 2% PnL divergence between backtest and paper engines.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone, timedelta

from flint.store import FlintStore
from flint.providers.drift_candles import DriftCandleProvider
from flint.strategy.momentum_breakout import MomentumBreakoutStrategy
from flint.backtest.parity import ParityTest


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MARKET = "SOL-PERP"
RESOLUTION_S = 3600
LOOKBACK_DAYS = 90
INITIAL_CAPITAL = 10_000.0
FEE_RATE = 0.0005


def ensure_candles(store: FlintStore, start_ts: int, end_ts: int):
    """Return candles from the store, downloading if needed."""
    candles = store.query_candles(MARKET, RESOLUTION_S, start_ts, end_ts)
    if len(candles) >= LOOKBACK_DAYS * 20:
        return candles

    print(f"  Downloading {LOOKBACK_DAYS} days of {MARKET} hourly candles from Drift...")
    provider = DriftCandleProvider()
    try:
        fetched = provider.fetch_candles(MARKET, RESOLUTION_S, start_ts, end_ts)
    finally:
        provider.close()

    if fetched:
        store.upsert_candles(fetched)
        print(f"  Stored {len(fetched):,} candles.")
        return store.query_candles(MARKET, RESOLUTION_S, start_ts, end_ts)

    if candles:
        print(f"  Download returned no data, using {len(candles)} existing candles.")
        return candles

    print("  ERROR: No candle data available.")
    print("  Run: python examples/canonical_backtest.py")
    sys.exit(1)


def main():
    print("=" * 60)
    print("  Flint Parity Test: Backtest vs Paper Broker")
    print("=" * 60)
    print()

    now = datetime.now(timezone.utc)
    end_ts = int(now.timestamp())
    start_ts = int((now - timedelta(days=LOOKBACK_DAYS)).timestamp())

    # --- Load data ---
    print(f"[1/3] Loading {MARKET} data ({LOOKBACK_DAYS} days, {RESOLUTION_S}s candles)")
    store = FlintStore()
    candles = ensure_candles(store, start_ts, end_ts)
    print(f"       {len(candles):,} candles loaded.")
    print()

    # --- Build strategy ---
    print("[2/3] Strategy: MomentumBreakout (default params)")
    strategy = MomentumBreakoutStrategy(
        breakout_lookback=20,
        trailing_stop_pct=0.02,
        oracle_confirmation=0,
    )
    print(f"       lookback={strategy.breakout_lookback}, "
          f"trailing_stop={strategy.trailing_stop_pct:.1%}")
    print()

    # --- Run parity test ---
    print("[3/3] Running parity test...")
    t0 = time.perf_counter()
    test = ParityTest(
        strategy=strategy,
        market=MARKET,
        candles=candles,
        initial_capital=INITIAL_CAPITAL,
        fee_rate=FEE_RATE,
    )
    report = test.run()
    elapsed = time.perf_counter() - t0

    # --- Print results ---
    print()
    print("-" * 60)
    print(report.summary())
    print("-" * 60)
    print(f"  Elapsed: {elapsed:.2f}s")
    print()

    if not report.passed:
        print("NOTE: Parity test FAILED. Possible causes:")
        print("  - Paper broker processes fills differently (order timing)")
        print("  - Strategy uses context methods only available in one engine")
        print("  - Position sizing rounding differences between engines")
        print("  - Edge cases in close-of-position logic at end of data")
        print()
        print("This does not mean your strategy is bad -- it means the two")
        print("engines diverged on this data. Investigate the divergence")
        print("before trusting backtest results for live trading.")


if __name__ == "__main__":
    main()
